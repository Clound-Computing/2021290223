import torch
import random
import pandas as pd
import json
import numpy as np
import jieba
from transformers import BertTokenizer
from torch.utils.data import TensorDataset, DataLoader

label_dict = {
    "real": 0,
    "fake": 1
}

label_dict_online = {
    'truth': 0,
    'rumor': 1
}

# category_dict = {
#     "2010": 0,
#     "2011": 1,
#     "2012": 2,
#     "2013": 3,
#     "2014": 4,
#     "2015": 5,
#     "2016": 6,
#     "2017": 7,
#     "2018": 8,
#     "2019": 9,
#     "2020": 9,
#     "2021": 9
# }

year_category_dict = {
    "2003": 0,
    "2004": 1,
    "2005": 2,
    "2006": 3,
    "2007": 4,
    "2008": 5,
    "2009": 6,
    "2010": 7,
    "2011": 8,
    "2012": 9,
    "2013": 10,
    "2014":11,
    "2015": 12,
    "2016": 13,
    "2017": 14,
    "2018": 15,
    "2019": 16,
    "2020": 17,
}

year_season_category_dict = {
    "2005-01": 0,
    "2005-02": 1,
    "2005-03": 2,
    "2005-04": 3,
    "2006-01": 4,
    "2006-02": 5,
    "2006-03": 6,
    "2006-04": 7,
    "2007-01": 8,
    "2007-02": 9,
    "2007-03": 10,
    "2007-04": 11,
    "2008-01": 12,
    "2008-02": 13,
    "2008-03": 14,
    "2008-04": 15,
    "2009-01": 16,
    "2009-02": 17,
    "2009-03": 18,
    "2009-04": 19,
    "2010-01": 20,
    "2010-02": 21,
    "2010-03": 22,
    "2010-04": 23,
    "2011-01": 24,
    "2011-02": 25,
    "2011-03": 26,
    "2011-04": 27,
    "2012-01": 28,
    "2012-02": 29,
    "2012-03": 30,
    "2012-04": 31,
    "2013-01": 32,
    "2013-02": 33,
    "2013-03": 34,
    "2013-04": 35,
    "2014-01": 36,
    "2014-02": 37,
    "2014-03": 38,
    "2014-04": 39,
    "2015-01": 40,
    "2015-02": 41,
    "2015-03": 42,
    "2015-04": 43,
    "2016-01": 44,
    "2016-02": 45,
    "2016-03": 46,
    "2016-04": 47,
    "2017-01": 48,
    "2017-02": 49,
    "2017-03": 50,
    "2017-04": 51,
    "2018-01": 52,
    "2018-02": 53,
    "2018-03": 54,
    "2018-04": 55,
    "2019-01": 56,
    "2019-02": 57,
    "2019-03": 58,
    "2019-04": 59,
    "2020-01": 60,
    "2020-02": 61,
    "2020-03": 62,
    "2020-04": 63,
}

def word2input(texts, max_len, bert_path):
    tokenizer = BertTokenizer.from_pretrained(bert_path)
    token_ids = []
    for i, text in enumerate(texts):
        token_ids.append(
            tokenizer.encode(text, max_length=max_len, add_special_tokens=True, padding='max_length',
                             truncation=True))
    token_ids = torch.tensor(token_ids)
    masks = torch.zeros(token_ids.shape)
    mask_token_id = tokenizer.pad_token_id
    for i, tokens in enumerate(token_ids):
        masks[i] = (tokens != mask_token_id)
    return token_ids, masks

def get_entity(entity_list):
    entity_content = []
    for item in entity_list:
        entity_content.append(item["entity"])
    entity_content = '[SEP]'.join(entity_content)
    return entity_content

def data_augment(content, entity_list, aug_prob):
    entity_content = []
    random_num = random.randint(1,100)
    if random_num <= 50:
        for item in entity_list:
            random_num = random.randint(1,100)
            if random_num <= int(aug_prob * 100):
                content = content.replace(item["entity"], '[MASK]')
            elif random_num <= int(2 * aug_prob * 100):
                content = content.replace(item["entity"], '')
            else:
                entity_content.append(item["entity"])
        entity_content = '[SEP]'.join(entity_content)
    else:
        content = list(jieba.cut(content))
        for index in range(len(content) - 1, -1, -1):
            random_num = random.randint(1,100)
            if random_num <= int(aug_prob * 100):
                del content[index]
            elif random_num <= int(2 * aug_prob * 100):
                content[index] = '[MASK]'
        content = ''.join(content)
        entity_content = get_entity(entity_list)

    return content, entity_content

def get_dataloader(path, max_len, batch_size, shuffle, use_endef, aug_prob, bert_path, data_type):
    # 处理news env数据
    if data_type == 'news_env':
        data_list = json.load(open(path, 'r',encoding='utf-8'))  # 读入json数据
        df_data = pd.DataFrame(columns=('content','label'))  # 创建空dataframe
        for item in data_list:  # 遍历数据，处理后加入df_data
            tmp_data = {}
            if shuffle == True and use_endef == True:
                tmp_data['content'], tmp_data['entity'] = data_augment(item['content'], item['entity_list'], aug_prob)
            else:
                tmp_data['content'] = item['content']
                tmp_data['entity'] = get_entity(item['entity_list'])
            tmp_data['label'] = item['label']
            tmp_data['year'] = item['time'].split(' ')[0].split('-')[0]
            df_data = df_data._append(tmp_data, ignore_index=True)
        emotion = np.load(path.replace('.json', '_emo.npy')).astype('float32')  # 读入并转换emotion
        emotion = torch.tensor(emotion)
        content = df_data['content'].to_numpy()
        entity_content = df_data['entity'].to_numpy()
        label = torch.tensor(df_data['label'].apply(lambda c: label_dict[c]).astype(int).to_numpy())  # 通过label_dict映射后转为为tensor
        year = torch.tensor(df_data['year'].apply(lambda c: category_dict[c]).astype(int).to_numpy())  # 通过categor_dict映射后转为为tensor
        content_token_ids, content_masks = word2input(content, max_len, bert_path)
        entity_token_ids, entity_masks = word2input(entity_content, 50, bert_path)
        dataset = TensorDataset(content_token_ids,
                                content_masks,
                                entity_token_ids,
                                entity_masks,
                                label,
                                year,
                                emotion
                                )

        dataloader = DataLoader(
            dataset=dataset,
            batch_size=batch_size,
            num_workers=4,
            pin_memory=True,
            shuffle=shuffle
        )
        return dataloader
    # 处理online数据
    elif data_type == 'online' or data_type == 'roll_online':
        data_list = json.load(open(path, 'r',encoding='utf-8'))
        df_data = pd.DataFrame(columns=('content','label'))
        for item in data_list:
            tmp_data = {}
            # if shuffle == True and use_endef == True:
            #     tmp_data['content'], tmp_data['entity'] = data_augment(item['content'], item['entity_list'], aug_prob)
            # else:
            tmp_data['content'] = item['content']
                # tmp_data['entity'] = get_entity(item['entity_list'])
            tmp_data['label'] = item['label']
            tmp_data['weight'] = item['weight']
            tmp_data['id'] = item['id']
            tmp_data['year'] = str(item['year'])
            tmp_data['year_season'] = str(item['year_season'])
            df_data = df_data._append(tmp_data, ignore_index=True)
        # emotion = np.load(path.replace('.json', '_emo.npy')).astype('float32')  # 读入并转换emotion
        # emotion = torch.tensor(emotion)
        content = df_data['content'].to_numpy()
        weight = torch.tensor(df_data['weight'].to_numpy())
        # entity_content = df_data['entity'].to_numpy()
        # tmp_label = df_data['label'].apply(lambda c: label_dict_online[c]).astype(int).to_numpy()
        # tmp_weight = df_data['weight'].to_numpy()
        label = torch.tensor(df_data['label'].apply(lambda c: label_dict_online[c]).astype(int).to_numpy())
        id = torch.tensor(df_data['id'].to_numpy())
        year = torch.tensor(df_data['year'].apply(lambda c: year_category_dict[c]).astype(int).to_numpy())
        year_season = torch.tensor(df_data['year_season'].apply(lambda c: year_season_category_dict[c]).astype(int).to_numpy())
        content_token_ids, content_masks = word2input(content, max_len, bert_path)
        # entity_token_ids, entity_masks = word2input(entity_content, 50, bert_path)
        dataset = TensorDataset(content_token_ids,
                                content_masks,
                                # entity_token_ids,
                                # entity_masks,
                                label,
                                weight,
                                id,
                                year,
                                year_season
                                # emotion
                                )
        dataloader = DataLoader(
            dataset=dataset,
            batch_size=batch_size,
            num_workers=1,  # 一个尝试
            pin_memory=False,
            shuffle=shuffle
        )
        return dataloader
    else:
        print('No match data type!')
        exit()
