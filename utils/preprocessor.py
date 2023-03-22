import torch
import os
import re
import string
import requests
import multiprocessing
import pandas as pd

from tqdm import tqdm
from utils.tree_helper import Tree_Helper
from Sastrawi.StopWordRemover.StopWordRemoverFactory import StopWordRemoverFactory
from Sastrawi.Stemmer.StemmerFactory import StemmerFactory
from transformers import BertTokenizer
from torch.utils.data import TensorDataset, DataLoader, RandomSampler, SequentialSampler

class Preprocessor(object):
    def __init__(self, method, dataset, batch_size, bert_model):
        super(Preprocessor, self).__init__()
        if not os.path.exists('datasets'):
            os.makedirs('datasets')

        if not os.path.exists(f'datasets/{dataset}_product_tokopedia.csv'):
            version = {
                'small': '0.1',
                'large': '0.0'
            }
                    
            file = requests.get(f'https://github.com/bintangfjulio/product_categories_classification/releases/download/{version[dataset]}/{dataset}_product_tokopedia.csv', allow_redirects=True)
            open(f'datasets/{dataset}_product_tokopedia.csv', 'wb').write(file.content)

        self.dataset = pd.read_csv(f'datasets/{dataset}_product_tokopedia.csv')
        self.tree = Tree_Helper(tree_file=f'datasets/{dataset}_hierarchy.tree', dataset=self.dataset)
        self.batch_size = batch_size
        self.method = method
        self.stop_words = StopWordRemoverFactory().get_stop_words()
        self.stemmer = StemmerFactory().create_stemmer()
        self.tokenizer = BertTokenizer.from_pretrained(bert_model)
    
    def preprocessor(self, level='all'):
        if self.method == 'section':
            pass

        else:
            if not os.path.exists(f"datasets/{self.method}_level_{str(level)}_train_set.pt") and not os.path.exists(f"datasets/{self.method}_level_{str(level)}_valid_set.pt") and not os.path.exists(f"datasets/{self.method}_level_{str(level)}_test_set.pt"):
                print("\nPreprocessing Data...")
                self.preprocessing_data(dataset=self.dataset, method=self.method, tree=self.tree, level=level)
                print('[ Preprocessing Completed ]\n')
            
            print("\nLoading Data...")
            train_set = torch.load(f"datasets/{self.method}_level_{str(level)}_train_set.pt")
            valid_set = torch.load(f"datasets/{self.method}_level_{str(level)}_valid_set.pt")
            test_set = torch.load(f"datasets/{self.method}_level_{str(level)}_test_set.pt")
            print('[ Loading Completed ]\n')

            return train_set, valid_set, test_set
    
    def get_max_length(self, dataset, extra_length=5):
        sentences_token = []
        
        for row in dataset.values.tolist():
            row = str(row[0]).split()
            sentences_token.append(row)

        token_length = [len(token) for token in sentences_token]
        max_length = max(token_length) + extra_length
        
        return max_length
    
    def preprocessing_data(self, dataset, method, tree, level): 
        level_on_nodes_indexed, idx_on_section, section_on_idx = tree.generate_hierarchy()
        max_length = self.get_max_length(dataset=dataset)
    
        input_ids, target = [], []
        preprocessing_progress = tqdm(dataset.values.tolist())

        for row in preprocessing_progress:
            text = self.text_cleaning(str(row[0]))
            token = self.tokenizer(text=text, max_length=max_length, padding="max_length", truncation=True)  

            if method == 'flat':
                last_node = row[-1].split(" > ")[-1].lower()
                flat_target = level_on_nodes_indexed[len(level_on_nodes_indexed) - 1][last_node]
                input_ids.append(token['input_ids'])
                target.append(flat_target)
            
            elif method == 'level':
                node_on_level = row[-1].split(" > ")[level].lower()
                member_on_level = level_on_nodes_indexed[level]
                level_target = member_on_level[node_on_level]
                input_ids.append(token['input_ids'])
                target.append(level_target)

            elif method == 'section':
                nodes = row[-1].lower().split(" > ")
                section = {}

                for node in nodes:
                    section_idx = section_on_idx[node]
                    nodes_on_section = idx_on_section[section_idx]
                    section_target = nodes_on_section.index(node)
                    section[section_idx] = section_target

                input_ids.append(token['input_ids'])
                target.append(section)
                
        if method == 'section':
            pass

        else:
            train_set, valid_set, test_set = self.dataset_splitting(input_ids, target)
            torch.save(train_set, f"datasets/{self.method}_level_{str(level)}_train_set.pt")
            torch.save(valid_set, f"datasets/{self.method}_level_{str(level)}_valid_set.pt")
            torch.save(test_set, f"datasets/{self.method}_level_{str(level)}_test_set.pt")

    def text_cleaning(self, text):
        text = text.lower()
        text = re.sub(r"[^A-Za-z0-9(),!?\'\-`]", " ", text)
        text = re.sub('\n', ' ', text)
        text = re.sub(r'@\w+', '', text)
        text = re.sub(r'http\S+', '', text)
        text = text.translate(str.maketrans('', '', string.punctuation))
        text = re.sub("'", '', text)
        text = re.sub(r'\d+', '', text)
        text = ' '.join([word for word in text.split() if word not in self.stop_words and len(word) > 1])
        text = self.stemmer.stem(text.strip())

        return text

    def dataset_splitting(self, input_ids, target):
        input_ids = torch.tensor(input_ids)
        target = torch.tensor(target)
        
        tensor_dataset = TensorDataset(input_ids, target)

        train_valid_size = round(len(tensor_dataset) * 0.8)
        test_size = len(tensor_dataset) - train_valid_size

        train_valid_set, test_set = torch.utils.data.random_split(tensor_dataset, [train_valid_size, test_size])

        train_size = round(len(train_valid_set) * 0.9)
        valid_size = len(train_valid_set) - train_size

        train_set, valid_set = torch.utils.data.random_split(train_valid_set, [train_size, valid_size])

        return train_set, valid_set, test_set      

    def flat_dataloader(self, stage):
        flat_train_set, flat_valid_set, flat_test_set = self.preprocessor() 
        
        train_sampler = RandomSampler(flat_train_set)
        valid_sampler = SequentialSampler(flat_valid_set)
        test_sampler = SequentialSampler(flat_test_set)
        
        if stage == 'fit':
            train_dataloader = DataLoader(dataset=flat_train_set,
                                        sampler=train_sampler,
                                        batch_size=self.batch_size,
                                        num_workers=multiprocessing.cpu_count())

            val_dataloader = DataLoader(dataset=flat_valid_set,
                                        sampler=valid_sampler,
                                        batch_size=self.batch_size,
                                        num_workers=multiprocessing.cpu_count())

            return train_dataloader, val_dataloader

        elif stage == 'test':
            test_dataloader = DataLoader(dataset=flat_test_set,
                                        sampler=test_sampler,
                                        batch_size=self.batch_size,
                                        num_workers=multiprocessing.cpu_count())

            return test_dataloader  

    def level_dataloader(self, stage, level):
        level_train_set, level_valid_set, level_test_set = self.preprocessor(level=level) 

        train_sampler = RandomSampler(level_train_set)
        valid_sampler = SequentialSampler(level_valid_set)
        test_sampler = SequentialSampler(level_test_set)
        
        if stage == 'fit':
            train_dataloader = DataLoader(dataset=level_train_set,
                                        sampler=train_sampler,
                                        batch_size=self.batch_size,
                                        num_workers=multiprocessing.cpu_count())

            val_dataloader = DataLoader(dataset=level_valid_set,
                                        sampler=valid_sampler,
                                        batch_size=self.batch_size,
                                        num_workers=multiprocessing.cpu_count())

            return train_dataloader, val_dataloader

        elif stage == 'test':
            test_dataloader = DataLoader(dataset=level_test_set,
                                        sampler=test_sampler,
                                        batch_size=self.batch_size,
                                        num_workers=multiprocessing.cpu_count())

            return test_dataloader
        
    def section_dataloader(self):
        pass
    