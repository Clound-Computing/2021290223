import os
import torch
import tqdm
import time
from .layers import *
from sklearn.metrics import *
from transformers import BertModel
from utils.utils import data2gpu, Averager, metrics, Recorder
from utils.dataloader import get_dataloader
from utils.utils import get_split_path, get_tensorboard_writer, process_test_results


class InstanceWeightedBCELoss(nn.Module):
    """ BCE Loss With Instance-Level Weighting
    """
    def __init__(self):
        super().__init__()
        self.loss = torch.nn.BCELoss(reduction='none')

    def forward(self, pred, ground, instance_weight):
        unweighted_loss = self.loss(pred, ground)
        loss = torch.mean(unweighted_loss * instance_weight)
        
        return loss


class BERTModel(torch.nn.Module):
    """ BERT Model
    """
    def __init__(self, emb_dim, mlp_dims, dropout, bert_path):
        super(BERTModel, self).__init__()
        self.bert = BertModel.from_pretrained(bert_path).requires_grad_(False)

        for name, param in self.bert.named_parameters():
            if name.startswith("encoder.layer.11"):
                param.requires_grad = True
            else:
                param.requires_grad = False

        self.mlp = MLP(emb_dim, mlp_dims, dropout)
        self.attention = MaskAttention(emb_dim)
    
    def forward(self, **kwargs):
        inputs = kwargs['content']
        masks = kwargs['content_masks']
        bert_feature = self.bert(inputs, attention_mask = masks)[0]
        bert_feature, _ = self.attention(bert_feature, masks)
        output = self.mlp(bert_feature)
        return torch.sigmoid(output.squeeze(1))


class Trainer():
    def __init__(self,
                 config,
                 writer
                 ):
        self.config = config
        self.writer = writer
        
        self.save_path = os.path.join(
            self.config['save_param_dir'],
            self.config['model_name']+'_'+self.config['data_name'],
            str(self.config['time_index']))
        if os.path.exists(self.save_path):
            self.save_param_dir = self.save_path
        else:
            self.save_param_dir = os.makedirs(self.save_path)        

    def train(self, logger = None):
        writer = self.writer
        if(logger):
            logger.info('==================== {}: {} ===================='.format(self.config['time_str'], self.config['time_index']))
            logger.info('start training......')                
            print('\n\n')
            print('==================== {}: {} ===================='.format(self.config['time_str'], self.config['time_index']))
        self.model = BERTModel(self.config['emb_dim'], self.config['model']['mlp']['dims'], self.config['model']['mlp']['dropout'], self.config['bert_path'])
        if self.config['use_cuda']:
            self.model = self.model.cuda()

        loss_fn = InstanceWeightedBCELoss()
        optimizer = torch.optim.Adam(params=self.model.parameters(), lr=self.config['lr'], weight_decay=self.config['weight_decay'])
        val_recorder = Recorder(self.config['early_stop'])
        test_recorder = Recorder(self.config['early_stop'])

        print()
        print('train shuffle here:', self.config['train_shuffle'])
        print()

        # load in training data
        train_path = get_split_path(self.config['split_level'], self.config['data_type'], self.config['root_path'], self.config['time_index'], 'train.json')
        train_loader = get_dataloader(
            train_path, 
            self.config['max_len'], 
            self.config['batchsize'], 
            shuffle=self.config['train_shuffle'], 
            use_endef=False, 
            aug_prob=self.config['aug_prob'], 
            bert_path=self.config['bert_path'], 
            data_type=self.config['data_type']
        )

        # load in val data
        val_path = get_split_path(self.config['split_level'], self.config['data_type'], self.config['root_path'], self.config['time_index'], 'val.json')
        val_loader = get_dataloader(
            val_path, 
            self.config['max_len'], 
            self.config['batchsize'], 
            shuffle=False, 
            use_endef=False, 
            aug_prob=self.config['aug_prob'], 
            bert_path=self.config['bert_path'], 
            data_type=self.config['data_type'],
        )
        
        # load in test data
        test_path = get_split_path(self.config['split_level'], self.config['data_type'], self.config['root_path'], self.config['time_index'], 'test.json')
        test_future_loader = get_dataloader(
            test_path, 
            self.config['max_len'], 
            self.config['batchsize'], 
            shuffle=False, 
            use_endef=False, 
            aug_prob=self.config['aug_prob'], 
            bert_path=self.config['bert_path'],
            data_type=self.config['data_type']
        )


        for epoch in range(self.config['epoch']):
            print('---------- epoch: {} ----------'.format(epoch))
            self.model.train()
            train_data_iter = tqdm.tqdm(train_loader)
            avg_loss = Averager()

            for step_n, batch in enumerate(train_data_iter):
                batch_data = data2gpu(
                    batch, 
                    self.config['use_cuda'], 
                    data_type=self.config['data_type']
                )
                label = batch_data['label']
                weight = batch_data['weight']

                pred = self.model(**batch_data)
                loss = loss_fn(pred, label.float(), weight)
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                avg_loss.add(loss.item())

            print('----- in val progress... -----')
            val_results = self.test(val_loader)
            val_mark = val_recorder.add(val_results)
            print()

            # tensorlog
            writer.add_scalar(self.config['time_str']+'_'+str(self.config['time_index'])+'/train_loss', avg_loss.item(), global_step=epoch)
            writer.add_scalars(self.config['time_str']+'_'+str(self.config['time_index'])+'/val', val_results, global_step=epoch)

            # logger
            if(logger):
                logger.info('---------- epoch {} ----------'.format(epoch))
                logger.info('train loss: {}'.format(avg_loss.item()))
                logger.info('val result: {}'.format(val_results))

            if val_mark == 'save':
                torch.save(self.model.state_dict(),
                    os.path.join(self.save_path, 'parameter_bert.pkl'))
            elif val_mark == 'esc':
                break
            else:
                continue

        # ---------- test process ----------
        # get path
        test_dir = os.path.join(
            './logs/test/',
            self.config['model_name'] + '_' + self.config['data_name']
        )
        os.makedirs(test_dir, exist_ok=True)
        test_res_path = os.path.join(
            test_dir,
            self.config['time_str']+'_'+ str(self.config['time_index']) + '.json'
        )
        # load in model
        self.model.load_state_dict(torch.load(os.path.join(self.save_path, 'parameter_bert.pkl')))

        # predict
        future_results, label, pred, id, ae, acc = self.predict(test_future_loader)
        process_test_results(test_path, test_res_path, label, pred, id, ae, acc)

        writer.add_scalars(self.config['time_str']+'_'+str(self.config['time_index'])+'/test', future_results)
        
        if(logger):
            logger.info("start testing......")
            logger.info("test score: {}.".format(future_results))
            logger.info("lr: {}, aug_prob: {}, avg test score: {}.\n\n".format(self.config['lr'], self.config['aug_prob'], future_results['metric']))
        print('test results:', future_results)
        return future_results, test_recorder.max, os.path.join(self.save_path, 'parameter_bert.pkl')


    def test(self, dataloader):
        """ Test Func
        """
        pred = []
        label = []
        self.model.eval()
        data_iter = tqdm.tqdm(dataloader)
        for step_n, batch in enumerate(data_iter):
            with torch.no_grad():
                batch_data = data2gpu(
                    batch, 
                    self.config['use_cuda'], 
                    data_type=self.config['data_type']
                )
                batch_label = batch_data['label']
                batch_pred = self.model(**batch_data)

                label.extend(batch_label.detach().cpu().numpy().tolist())
                pred.extend(batch_pred.detach().cpu().numpy().tolist())
        
        return metrics(label, pred)

    
    def predict(self, dataloader):
        """ Predict Func
        """
        if self.config['eval_mode']:
            print('{} {} model loading...'.format(self.config['time_str'], self.config['time_index']))
            self.model = BERTModel(self.config['emb_dim'], self.config['model']['mlp']['dims'], self.config['model']['mlp']['dropout'], self.config['bert_path'])
            if self.config['use_cuda']:
                self.model = self.model.cuda()
            print('========== in test process ==========')
            print('now load in test model...')
            self.model.load_state_dict(torch.load(self.config['eval_model_path']))
        pred = []
        label = []
        id = []
        ae = []
        accuracy = []
        self.model.eval()
        data_iter = tqdm.tqdm(dataloader)
        for step_n, batch in enumerate(data_iter):
            with torch.no_grad():
                batch_data = data2gpu(
                    batch, 
                    self.config['use_cuda'], 
                    data_type=self.config['data_type']
                )
                batch_label = batch_data['label']
                batch_id = batch_data['id']
                batch_pred = self.model(**batch_data)

                cur_labels = batch_label.detach().cpu().numpy().tolist()
                cur_preds = batch_pred.detach().cpu().numpy().tolist()
                cur_ids = batch_id.detach().cpu().numpy().tolist()
                label.extend(cur_labels)
                pred.extend(cur_preds)
                id.extend(cur_ids)
                ae_list = []
                for index in range(len(cur_labels)):
                    ae_list.append(abs(cur_preds[index] - cur_labels[index]))
                accuracy_list = [1 if ae<0.5 else 0 for ae in ae_list]
                ae.extend(ae_list)
                accuracy.extend(accuracy_list)
        
        return metrics(label, pred), label, pred, id, ae, accuracy