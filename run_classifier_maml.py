# coding=utf-8

"""BERT finetuning runner."""

from __future__ import absolute_import, division, print_function

import argparse
import csv
import logging
import os
import random
import sys
import math

import numpy as np
import torch
from torch import optim
from torch.utils.data import (DataLoader, RandomSampler, SequentialSampler,
                              TensorDataset)
from torch.utils.data.distributed import DistributedSampler
from tqdm import tqdm, trange

from torch.nn import CrossEntropyLoss, MSELoss
# from scipy.stats import pearsonr, spearmanr
from sklearn.metrics import matthews_corrcoef, f1_score
from copy import deepcopy

from pytorch_pretrained_bert.file_utils import PYTORCH_PRETRAINED_BERT_CACHE, WEIGHTS_NAME, CONFIG_NAME
from pytorch_pretrained_bert.modeling import BertForSequenceClassification, BertConfig
from pytorch_pretrained_bert.tokenization import BertTokenizer
from pytorch_pretrained_bert.optimization import BertAdam, WarmupLinearSchedule

logger = logging.getLogger(__name__)
os.environ["CUDA_VISIBLE_DEVICES"] = "1"


class InputExample(object):
    """A single training/test example for simple sequence classification."""

    def __init__(self, guid, text_a, text_b=None, label=None):
        """Constructs a InputExample.

        Args:
            guid: Unique id for the example.
            text_a: string. The untokenized text of the first sequence. For single
            sequence tasks, only this sequence must be specified.
            text_b: (Optional) string. The untokenized text of the second sequence.
            Only must be specified for sequence pair tasks.
            label: (Optional) string. The label of the example. This should be
            specified for train and dev examples, but not for test examples.
        """
        self.guid = guid
        self.text_a = text_a
        self.text_b = text_b
        self.label = label


class InputFeatures(object):
    """A single set of features of data."""

    def __init__(self, input_ids, input_mask, segment_ids, label_id):
        self.input_ids = input_ids
        self.input_mask = input_mask
        self.segment_ids = segment_ids
        self.label_id = label_id


class DataProcessor(object):
    """Base class for data converters for sequence classification data sets."""

    def get_train_examples(self, data_dir):
        """Gets a collection of `InputExample`s for the train set."""
        raise NotImplementedError()

    def get_dev_examples(self, data_dir):
        """Gets a collection of `InputExample`s for the dev set."""
        raise NotImplementedError()

    def get_labels(self):
        """Gets the list of labels for this data set."""
        raise NotImplementedError()

    @classmethod
    def _read_tsv(cls, input_file, quotechar=None):
        """Reads a tab separated value file."""
        with open(input_file, "r", encoding="utf-8") as f:
            reader = csv.reader(f, delimiter="\t", quotechar=quotechar)
            lines = []
            for line in reader:
                if sys.version_info[0] == 2:
                    # line = list(uni(cell, 'utf-8') for cell in line)
                    line = list(cell for cell in line)
                lines.append(line)
            return lines


class MrpcProcessor(DataProcessor):
    """Processor for the MRPC data set (GLUE version)."""

    def get_train_examples(self, data_dir):
        """See base class."""
        logger.info("LOOKING AT {}".format(os.path.join(data_dir, "train.tsv")))
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "train.tsv")), "train")

    def get_dev_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "dev.tsv")), "dev")

    def get_labels(self):
        """See base class."""
        return ["0", "1"]

    def _create_examples(self, lines, set_type):
        """Creates examples for the training and dev sets."""
        examples = []
        for (i, line) in enumerate(lines):
            if i == 0:
                continue
            guid = "%s-%s" % (set_type, i)
            text_a = line[3]
            text_b = line[4]
            label = line[0]
            examples.append(
                InputExample(guid=guid, text_a=text_a, text_b=text_b, label=label))
        return examples


class MnliProcessor(DataProcessor):
    """Processor for the MultiNLI data set (GLUE version)."""

    def get_train_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "train.tsv")), "train")

    def get_dev_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "dev_matched.tsv")),
            "dev_matched")

    def get_labels(self):
        """See base class."""
        return ["contradiction", "entailment", "neutral"]

    def _create_examples(self, lines, set_type):
        """Creates examples for the training and dev sets."""
        examples = []
        for (i, line) in enumerate(lines):
            if i == 0:
                continue
            guid = "%s-%s" % (set_type, line[0])
            text_a = line[8]
            text_b = line[9]
            label = line[-1]
            examples.append(
                InputExample(guid=guid, text_a=text_a, text_b=text_b, label=label))
        return examples


class MnliMismatchedProcessor(MnliProcessor):
    """Processor for the MultiNLI Mismatched data set (GLUE version)."""

    def get_dev_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "dev_mismatched.tsv")),
            "dev_matched")


class ColaProcessor(DataProcessor):
    """Processor for the CoLA data set (GLUE version)."""

    def get_train_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "train.tsv")), "train")

    def get_dev_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "dev.tsv")), "dev")

    def get_labels(self):
        """See base class."""
        return ["0", "1"]

    def _create_examples(self, lines, set_type):
        """Creates examples for the training and dev sets."""
        examples = []
        for (i, line) in enumerate(lines):
            guid = "%s-%s" % (set_type, i)
            text_a = line[3]
            label = line[1]
            examples.append(
                InputExample(guid=guid, text_a=text_a, text_b=None, label=label))
        return examples


class Sst2Processor(DataProcessor):
    """Processor for the SST-2 data set (GLUE version)."""

    def get_train_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "train.tsv")), "train")

    def get_dev_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "dev.tsv")), "dev")

    def get_labels(self):
        """See base class."""
        return ["0", "1"]

    def _create_examples(self, lines, set_type):
        """Creates examples for the training and dev sets."""
        examples = []
        for (i, line) in enumerate(lines):
            if i == 0:
                continue
            guid = "%s-%s" % (set_type, i)
            text_a = line[0]
            label = line[1]
            examples.append(
                InputExample(guid=guid, text_a=text_a, text_b=None, label=label))
        return examples


class StsbProcessor(DataProcessor):
    """Processor for the STS-B data set (GLUE version)."""

    def get_train_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "train.tsv")), "train")

    def get_dev_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "dev.tsv")), "dev")

    def get_labels(self):
        """See base class."""
        return [None]

    def _create_examples(self, lines, set_type):
        """Creates examples for the training and dev sets."""
        examples = []
        for (i, line) in enumerate(lines):
            if i == 0:
                continue
            guid = "%s-%s" % (set_type, line[0])
            text_a = line[7]
            text_b = line[8]
            label = line[-1]
            examples.append(
                InputExample(guid=guid, text_a=text_a, text_b=text_b, label=label))
        return examples


class QqpProcessor(DataProcessor):
    """Processor for the STS-B data set (GLUE version)."""

    def get_train_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "train.tsv")), "train")

    def get_dev_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "dev.tsv")), "dev")

    def get_labels(self):
        """See base class."""
        return ["0", "1"]

    def _create_examples(self, lines, set_type):
        """Creates examples for the training and dev sets."""
        examples = []
        for (i, line) in enumerate(lines):
            if i == 0:
                continue
            guid = "%s-%s" % (set_type, line[0])
            try:
                text_a = line[3]
                text_b = line[4]
                label = line[5]
            except IndexError:
                continue
            examples.append(
                InputExample(guid=guid, text_a=text_a, text_b=text_b, label=label))
        return examples


class QnliProcessor(DataProcessor):
    """Processor for the STS-B data set (GLUE version)."""

    def get_train_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "train.tsv")), "train")

    def get_dev_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "dev.tsv")), 
            "dev_matched")

    def get_labels(self):
        """See base class."""
        return ["entailment", "not_entailment"]

    def _create_examples(self, lines, set_type):
        """Creates examples for the training and dev sets."""
        examples = []
        for (i, line) in enumerate(lines):
            if i == 0:
                continue
            guid = "%s-%s" % (set_type, line[0])
            text_a = line[1]
            text_b = line[2]
            label = line[-1]
            examples.append(
                InputExample(guid=guid, text_a=text_a, text_b=text_b, label=label))
        return examples


class RteProcessor(DataProcessor):
    """Processor for the RTE data set (GLUE version)."""

    def get_train_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "train.tsv")), "train")

    def get_dev_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "dev.tsv")), "dev")

    def get_labels(self):
        """See base class."""
        return ["entailment", "not_entailment"]

    def _create_examples(self, lines, set_type):
        """Creates examples for the training and dev sets."""
        examples = []
        for (i, line) in enumerate(lines):
            if i == 0:
                continue
            guid = "%s-%s" % (set_type, line[0])
            text_a = line[1]
            text_b = line[2]
            label = line[-1]
            examples.append(
                InputExample(guid=guid, text_a=text_a, text_b=text_b, label=label))
        return examples


class WnliProcessor(DataProcessor):
    """Processor for the WNLI data set (GLUE version)."""

    def get_train_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "train.tsv")), "train")

    def get_dev_examples(self, data_dir):
        """See base class."""
        return self._create_examples(
            self._read_tsv(os.path.join(data_dir, "dev.tsv")), "dev")

    def get_labels(self):
        """See base class."""
        return ["0", "1"]

    def _create_examples(self, lines, set_type):
        """Creates examples for the training and dev sets."""
        examples = []
        for (i, line) in enumerate(lines):
            if i == 0:
                continue
            guid = "%s-%s" % (set_type, line[0])
            text_a = line[1]
            text_b = line[2]
            label = line[-1]
            examples.append(
                InputExample(guid=guid, text_a=text_a, text_b=text_b, label=label))
        return examples


class AmazonProcessor(DataProcessor):
    """Processor for the Amazon data set ."""
    def calculate_task_num(self, data_dir):
        with open(os.path.join(data_dir, 'workspace.filtered.list'), 'r') as train_f:
            train_list = train_f.readlines()
        self.train_task_num = len(train_list)*3

        with open(os.path.join(data_dir, 'workspace.target.list'), 'r') as test_f:
            test_list = test_f.readlines()
        self.test_task_num = len(test_list)*3
        return self.train_task_num, self.test_task_num

    def _read_file(self,dataname):
        with open(dataname, "r") as f:
            reader = csv.reader(f, delimiter="\t")
            lines = []
            for line in reader:
                lines.append(line)
            return lines

    def _divide_tasks(self, data_name, type):
        task_class = ['t2', 't5', 't4']
        tasks = []
        total_len = 0
        for task in task_class:
            file_name = data_name+'.'+task+'.'+type
            print(file_name)
            task_data = self._read_file(file_name)
            tasks.append(task_data)
            total_len = total_len+len(task_data)
        return tasks, total_len

    def _get_examples(self, data_dir, filter_name, type):
        tasks = []
        labels = []
        total_len = 0
        with open(os.path.join(data_dir, filter_name), 'r') as f:
            task_list = f.readlines()
            task_list = [name.strip() for name in task_list]
        for task_name in task_list:
            diverse_task, task_len = self._divide_tasks(os.path.join(os.path.join(data_dir, "data"), task_name), type)
            tasks.extend(diverse_task)
            total_len += task_len
        return tasks, total_len

    def load_all_data(self, data_dir):
        self.max_train_number = 0
        self.max_dev_number = 0
        self.max_test_number = 0
        self.train_tasks, self.train_number = self._get_examples(data_dir, 'workspace.filtered.list', 'train')
        self.dev_tasks, self.dev_number = self._get_examples(data_dir, 'workspace.filtered.list', 'dev')
        self.test_tasks, self.test_number = self._get_examples(data_dir, 'workspace.filtered.list', 'test')
        self.fsl_train, self.fsl_train_number = self._get_examples(data_dir, 'workspace.target.list', 'train')
        self.fsl_dev, self.fsl_dev_number = self._get_examples(data_dir, 'workspace.target.list', 'dev')
        self.fsl_test, self.fsl_test_number = self._get_examples(data_dir, 'workspace.target.list', 'test')
        self.train_examples = []
        self.train_labels = []
        self.dev_examples = []
        self.dev_labels = []
        self.test_examples = []
        self.test_labels = []
        self.fsl_train_examples = []
        self.fsl_train_labels = []
        self.fsl_dev_examples = []
        self.fsl_dev_labels = []
        self.fsl_test_examples = []
        self.fsl_test_labels = []
        for id in range(self.train_task_num):
            examples, labels = self.get_train_examples(data_dir, id)
            self.train_examples.append(examples)
            self.train_labels.append(labels)
            examples, labels = self.get_dev_examples(data_dir, id)
            self.dev_examples.append(examples)
            self.dev_labels.append(labels)
            examples, labels = self.get_test_examples(data_dir, id)
            self.test_examples.append(examples)
            self.test_labels.append(labels)
        for id in range(self.test_task_num):
            examples, labels = self.get_fsl_train_examples(data_dir, id)
            self.fsl_train_examples.append(examples)
            self.fsl_train_labels.append(labels)
            examples, labels = self.get_fsl_dev_examples(data_dir, id)
            self.fsl_dev_examples.append(examples)
            self.fsl_dev_labels.append(labels)
            examples, labels = self.get_fsl_test_examples(data_dir, id)
            self.fsl_test_examples.append(examples)
            self.fsl_test_labels.append(labels)

    def get_train_task_len(self,task_id):
        return len(self.train_examples[task_id])

    def get_train_examples(self, data_dir, task_id=0):
        return self._create_examples(self.train_tasks[task_id], "train", task_id)

    def get_dev_examples(self, data_dir,task_id=0):
        """See base class."""
        return self._create_examples(self.dev_tasks[task_id],"dev",task_id) 

    def get_test_examples(self, data_dir,task_id=0):
        """See base class."""
        return self._create_examples(self.test_tasks[task_id],"test",task_id)

    def get_fsl_train_examples(self, data_dir, task_id=0):
        
        return self._create_examples(self.fsl_train[task_id],"train",task_id) 

    def get_fsl_dev_examples(self, data_dir,task_id=0):
        """See base class."""
        return self._create_examples(self.fsl_dev[task_id],"dev",task_id) 

    def get_fsl_test_examples(self, data_dir,task_id=0):
        """See base class."""
        return self._create_examples(self.fsl_test[task_id],"test",task_id)

    def get_fsl_support(self, data_dir, task_id=0):
        examples, labels = self.get_fsl_train_examples(data_dir, task_id)
        label_names = self.get_labels()
        true_exp_indices = [i for i, e in enumerate(labels) if e == label_names[0]]
        false_exp_indices = [i for i, e in enumerate(labels) if e == label_names[1]]
        support = []
        support.extend([examples[i] for i in true_exp_indices])
        support.extend([examples[i] for i in false_exp_indices])
        random.shuffle(support)
        return support

    def get_labels(self):
        """See base class."""
        return ["-1", "1"]

    def get_next_batch(self, B, N, K, Q, set_type, task_id):
        '''
        B: batch size.
        N: the number of relations for each batch(2)
        K: the number of support instances for each relation
        Q: the number of query instances for each relation
        return: support_set, query_set, query_label
        '''
        support = []
        query = []
        if set_type == 'train':
            train_examples = self.train_examples[task_id]
            train_labels = self.train_labels[task_id]
            dev_examples = self.dev_examples[task_id]
            dev_labels = self.dev_labels[task_id]
        else:
            return support, query
        label_names = self.get_labels()
        true_train_indices = [i for i, e in enumerate(train_labels) if e == label_names[0]]
        false_train_indices = [i for i, e in enumerate(train_labels) if e == label_names[1]]

        # true_dev_indices = [i for i, e in enumerate(dev_labels) if e == label_names[0]]
        # false_dev_indices = [i for i, e in enumerate(dev_labels) if e == label_names[1]]
        # print("dev examples number: ", len(dev_examples))
        for one_sample in range(B):
            select_indices = np.random.choice(true_train_indices, K, False)
            support.extend([train_examples[i] for i in select_indices])
            # select_indices = np.random.choice(true_dev_indices, Q, False)
            # query.extend([dev_examples[i] for i in select_indices])
            select_indices = np.random.choice(false_train_indices, K, False)
            support.extend([train_examples[i] for i in select_indices])
            # select_indices = np.random.choice(false_dev_indices, Q, False)
            # query.extend([dev_examples[i] for i in select_indices])
        return support, query

    def _create_examples(self, lines, set_type, set_id):
        """Creates examples for the training and dev sets."""
        examples = []
        labels = []
        for (i, line) in enumerate(lines):    
            guid = "%s-%s-%s" % (set_type, set_id, i)
            if set_type == "test":
                text_a = line[0]
                # print(text_a)
                label = line[1]
            else:
                try:
                    text_a = line[0]
                    label = line[1]
                except:
                    print(line)
            examples.append(
                InputExample(guid=guid, text_a=text_a, text_b=None, label=label))
            labels.append(label)
        return examples, labels


def convert_examples_to_features(examples, label_list, max_seq_length, tokenizer, output_mode):
    """Loads a data file into a list of `InputBatch`s."""
    label_map = {label: i for i, label in enumerate(label_list)}

    features = []
    for (ex_index, example) in enumerate(examples):
        # if ex_index % 10000 == 0:
        #     logger.info("Writing example %d of %d" % (ex_index, len(examples)))

        tokens_a = tokenizer.tokenize(example.text_a)

        tokens_b = None
        if example.text_b:
            tokens_b = tokenizer.tokenize(example.text_b)
            # Modifies `tokens_a` and `tokens_b` in place so that the total
            # length is less than the specified length.
            # Account for [CLS], [SEP], [SEP] with "- 3"
            _truncate_seq_pair(tokens_a, tokens_b, max_seq_length - 3)
        else:
            # Account for [CLS] and [SEP] with "- 2"
            if len(tokens_a) > max_seq_length - 2:
                tokens_a = tokens_a[:(max_seq_length - 2)]

        # The convention in BERT is:
        # (a) For sequence pairs:
        #  tokens:   [CLS] is this jack ##son ##ville ? [SEP] no it is not . [SEP]
        #  type_ids: 0   0  0    0    0     0       0 0    1  1  1  1   1 1
        # (b) For single sequences:
        #  tokens:   [CLS] the dog is hairy . [SEP]
        #  type_ids: 0   0   0   0  0     0 0
        #
        # Where "type_ids" are used to indicate whether this is the first
        # sequence or the second sequence. The embedding vectors for `type=0` and
        # `type=1` were learned during pre-training and are added to the wordpiece
        # embedding vector (and position vector). This is not *strictly* necessary
        # since the [SEP] token unambiguously separates the sequences, but it makes
        # it easier for the model to learn the concept of sequences.
        #
        # For classification tasks, the first vector (corresponding to [CLS]) is
        # used as as the "sentence vector". Note that this only makes sense because
        # the entire model is fine-tuned.
        tokens = ["[CLS]"] + tokens_a + ["[SEP]"]
        segment_ids = [0] * len(tokens)

        if tokens_b:
            tokens += tokens_b + ["[SEP]"]
            segment_ids += [1] * (len(tokens_b) + 1)

        input_ids = tokenizer.convert_tokens_to_ids(tokens)

        # The mask has 1 for real tokens and 0 for padding tokens. Only real
        # tokens are attended to.
        input_mask = [1] * len(input_ids)

        # Zero-pad up to the sequence length.
        padding = [0] * (max_seq_length - len(input_ids))
        input_ids += padding
        input_mask += padding
        segment_ids += padding

        assert len(input_ids) == max_seq_length
        assert len(input_mask) == max_seq_length
        assert len(segment_ids) == max_seq_length

        if output_mode == "classification":
            label_id = label_map[example.label]
        elif output_mode == "regression":
            label_id = float(example.label)
        else:
            raise KeyError(output_mode)

        # if ex_index < 5:
        #     logger.info("*** Example ***")
        #     logger.info("guid: %s" % (example.guid))
        #     logger.info("tokens: %s" % " ".join(
        #             [str(x) for x in tokens]))
        #     logger.info("input_ids: %s" % " ".join([str(x) for x in input_ids]))
        #     logger.info("input_mask: %s" % " ".join([str(x) for x in input_mask]))
        #     logger.info(
        #             "segment_ids: %s" % " ".join([str(x) for x in segment_ids]))
        #     logger.info("label: %s (id = %d)" % (example.label, label_id))

        features.append(
                InputFeatures(input_ids=input_ids,
                              input_mask=input_mask,
                              segment_ids=segment_ids,
                              label_id=label_id))
    return features


def _truncate_seq_pair(tokens_a, tokens_b, max_length):
    """Truncates a sequence pair in place to the maximum length."""

    # This is a simple heuristic which will always truncate the longer sequence
    # one token at a time. This makes more sense than truncating an equal percent
    # of tokens from each, since if one sequence is very short then each token
    # that's truncated likely contains more information than a longer sequence.
    while True:
        total_length = len(tokens_a) + len(tokens_b)
        if total_length <= max_length:
            break
        if len(tokens_a) > len(tokens_b):
            tokens_a.pop()
        else:
            tokens_b.pop()


def simple_accuracy(preds, labels):
    return (preds == labels).mean()


def acc_and_f1(preds, labels):
    acc = simple_accuracy(preds, labels)
    f1 = f1_score(y_true=labels, y_pred=preds)
    return {
        "acc": acc,
        "f1": f1,
        "acc_and_f1": (acc + f1) / 2,
    }


def get_train_prob(reward_prob, K, epsilon):
    indices = np.argpartition(reward_prob, -K)[-K:]
    one_hot_prob = np.zeros((len(reward_prob)))
    for ind in indices:
        one_hot_prob[ind] = 1
    one_hot_prob = epsilon*np.ones(len(reward_prob))+one_hot_prob
    return one_hot_prob/np.sum(one_hot_prob)


def compute_metrics(task_name, preds, labels):
    assert len(preds) == len(labels)
    if task_name == "cola":
        return {"mcc": matthews_corrcoef(labels, preds)}
    elif task_name == "sst-2":
        return {"acc": simple_accuracy(preds, labels)}
    elif task_name == "mrpc":
        return acc_and_f1(preds, labels)
    # elif task_name == "sts-b":
    #     return pearson_and_spearman(preds, labels)
    elif task_name == "qqp":
        return acc_and_f1(preds, labels)
    elif task_name == "mnli":
        return {"acc": simple_accuracy(preds, labels)}
    elif task_name == "mnli-mm":
        return {"acc": simple_accuracy(preds, labels)}
    elif task_name == "qnli":
        return {"acc": simple_accuracy(preds, labels)}
    elif task_name == "rte":
        return {"acc": simple_accuracy(preds, labels)}
    elif task_name == "wnli":
        return {"acc": simple_accuracy(preds, labels)}
    elif task_name == "amazon":
        return {"acc": simple_accuracy(preds, labels)}
    else:
        raise KeyError(task_name)


def accuracy(pred, label):
    '''
    pred: prediction result
    label: label with whatever size
    return: Accuracy[A single Value]
    '''
    return torch.mean((pred.view(-1)) == (label.view(-1)).type(torch.FloatTensor))


def main():
    parser = argparse.ArgumentParser()
    ## Required parameters
    parser.add_argument("--data_dir",
                        default="data/Amazon_few_shot",
                        type=str,
                        help="The input data dir. Should contain the .tsv files (or other data files) for the task.")
    parser.add_argument("--bert_model", default="models/finetuned_lm", type=str,
                        help="Bert pre-trained model selected in the list: bert-base-uncased, "
                        "bert-large-uncased, bert-base-cased, bert-large-cased, bert-base-multilingual-uncased, "
                        "bert-base-multilingual-cased, bert-base-chinese.")
    parser.add_argument("--task_name",
                        default="Amazon",
                        type=str,
                        help="The name of the task to train.")
    parser.add_argument("--output_dir",
                        default="outputs/amazon_maml",
                        type=str,
                        help="The output directory where the model predictions and checkpoints will be written.")
    ## Other parameters
    parser.add_argument("--is_init",
                        default=False,
                        type=bool,
                        help="whether initialize the model")
    parser.add_argument("--is_reptile",
                        default=False,
                        type=bool,
                        help="whether use reptile or fomaml method")
    parser.add_argument("--cache_dir",
                        default="",
                        type=str,
                        help="Where do you want to store the pre-trained models downloaded from s3")
    parser.add_argument("--max_seq_length",
                        default=128,
                        type=int,
                        help="The maximum total input sequence length after WordPiece tokenization. \n"
                             "Sequences longer than this will be truncated, and sequences shorter \n"
                             "than this will be padded.")
    parser.add_argument("--do_train", default=True,
                        action='store_true',
                        help="Whether to run training.")
    parser.add_argument("--do_eval", default=True,
                        action='store_true',
                        help="Whether to run eval on the dev set.")
    parser.add_argument("--do_lower_case", default=True,
                        action='store_true',
                        help="Set this flag if you are using an uncased model.")
    parser.add_argument("--train_batch_size",
                        default=32,
                        type=int,
                        help="Total batch size for training.")
    parser.add_argument("--eval_batch_size",
                        default=8,
                        type=int,
                        help="Total batch size for eval.")
    parser.add_argument("--learning_rate",
                        default=1e-5,
                        type=float,
                        help="The initial learning rate for Adam.")
    parser.add_argument("--inner_learning_rate",
                        default=2e-6,
                        type=float,
                        help="The inner learning rate for Adam")
    parser.add_argument("--outer_learning_rate",
                        default=1e-5,
                        type=float,
                        help="The meta learning rate for Adam, actual learning rate!")
    parser.add_argument("--FSL_learning_rate",
                        default=2e-5,
                        type=float,
                        help="The FSL learning rate for Adam!")           
    parser.add_argument("--FSL_epochs",
                        default=1,
                        type=int,
                        help="The FSL learning epochs for training!")                    
    parser.add_argument("--num_train_epochs",
                        default=2.0,
                        type=float,
                        help="Total number of training epochs to perform.")
    parser.add_argument("--warmup_proportion",
                        default=0.1,
                        type=float,
                        help="Proportion of training to perform linear learning rate warmup for. "
                             "E.g., 0.1 = 10%% of training.")
    parser.add_argument("--no_cuda",
                        action='store_true',
                        help="Whether not to use CUDA when available")
    parser.add_argument("--local_rank",
                        type=int,
                        default=-1,
                        help="local_rank for distributed training on gpus")
    parser.add_argument('--seed',
                        type=int,
                        default=42,
                        help="random seed for initialization")
    parser.add_argument('--gradient_accumulation_steps',
                        type=int,
                        default=1,
                        help="Number of updates steps to accumulate before performing a backward/update pass.")
    parser.add_argument('--fp16',
                        action='store_true',
                        help="Whether to use 16-bit float precision instead of 32-bit")
    parser.add_argument('--loss_scale',
                        type=float, default=0,
                        help="Loss scaling to improve fp16 numeric stability. Only used when fp16 set to True.\n"
                             "0 (default value): dynamic loss scaling.\n"
                             "Positive power of 2: static loss scaling value.\n")
    parser.add_argument('--server_ip', type=str, default='', help="Can be used for distant debugging.")
    parser.add_argument('--server_port', type=str, default='', help="Can be used for distant debugging.")
    args = parser.parse_args()

    if args.server_ip and args.server_port:
        # Distant debugging - see https://code.visualstudio.com/docs/python/debugging#_attach-to-a-local-script
        import ptvsd
        print("Waiting for debugger attach")
        ptvsd.enable_attach(address=(args.server_ip, args.server_port), redirect_output=True)
        ptvsd.wait_for_attach()

    processors = {
        "cola": ColaProcessor,
        "mnli": MnliProcessor,
        "mnli-mm": MnliMismatchedProcessor,
        "mrpc": MrpcProcessor,
        "sst-2": Sst2Processor,
        "sts-b": StsbProcessor,
        "qqp": QqpProcessor,
        "qnli": QnliProcessor,
        "rte": RteProcessor,
        "wnli": WnliProcessor,
        "amazon": AmazonProcessor,
    }

    output_modes = {
        "cola": "classification",
        "mnli": "classification",
        "mrpc": "classification",
        "sst-2": "classification",
        "sts-b": "regression",
        "qqp": "classification",
        "qnli": "classification",
        "rte": "classification",
        "wnli": "classification",
        "amazon": "classification"
    }

    if args.local_rank == -1 or args.no_cuda:
        device = torch.device("cuda" if torch.cuda.is_available() and not args.no_cuda else "cpu")
        n_gpu = torch.cuda.device_count()
    else:
        torch.cuda.set_device(args.local_rank)
        device = torch.device("cuda", args.local_rank)
        n_gpu = 1
        # Initializes the distributed backend which will take care of sychronizing nodes/GPUs
        torch.distributed.init_process_group(backend='nccl')

    logging.basicConfig(format = '%(asctime)s - %(levelname)s - %(name)s -   %(message)s',
                        datefmt = '%m/%d/%Y %H:%M:%S',
                        level = logging.INFO if args.local_rank in [-1, 0] else logging.WARN)

    logger.info("device: {} n_gpu: {}, distributed training: {}, 16-bits training: {}".format(
        device, n_gpu, bool(args.local_rank != -1), args.fp16))

    if args.gradient_accumulation_steps < 1:
        raise ValueError("Invalid gradient_accumulation_steps parameter: {}, should be >= 1".format(
                            args.gradient_accumulation_steps))

    args.train_batch_size = args.train_batch_size // args.gradient_accumulation_steps

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if n_gpu > 0:
        torch.cuda.manual_seed_all(args.seed)

    if not args.do_train and not args.do_eval:
        raise ValueError("At least one of `do_train` or `do_eval` must be True.")

    if os.path.exists(args.output_dir) and os.listdir(args.output_dir) and args.do_train:
        raise ValueError("Output directory ({}) already exists and is not empty.".format(args.output_dir))
    if not os.path.exists(args.output_dir):
        os.makedirs(args.output_dir)

    task_name = args.task_name.lower()

    if task_name not in processors:
        raise ValueError("Task not found: %s" % (task_name))

    processor = processors[task_name]()
    output_mode = output_modes[task_name]

    label_list = processor.get_labels()
    print("read data!")
    train_task_number, fsl_task_number = processor.calculate_task_num(args.data_dir)
    processor.load_all_data(args.data_dir)
    print("load finished!")
    
    num_labels = len(label_list)

    tokenizer = BertTokenizer.from_pretrained(args.bert_model, do_lower_case=args.do_lower_case)

    train_examples = None
    num_train_optimization_steps = None
    proto_hidden = 100
    Inner_epochs = 4
    N_iteration = 2000
    N_shot = 5
    N_query = 3
    N_task = 5
    N_class = num_labels
    train_batch_s = 1
    Is_reptile = args.is_reptile
    if args.do_train:
        # train_examples = processor.get_train_examples(args.data_dir)
        num_train_optimization_steps = N_iteration*(N_shot+N_query)*N_class*N_task
        if args.local_rank != -1:
            num_train_optimization_steps = num_train_optimization_steps // torch.distributed.get_world_size()

    # Prepare model
    cache_dir = args.cache_dir if args.cache_dir else os.path.join(str(PYTORCH_PRETRAINED_BERT_CACHE), 'distributed_{}'.format(args.local_rank))
    model = BertForSequenceClassification.from_pretrained(args.bert_model, cache_dir=cache_dir, num_labels=num_labels)  # num_labels as proto_network's embedding size
    print("Whether Initialize the model?")
    if args.is_init:
        print("Initializing........")
        model.apply(model.init_bert_weights)
    if args.fp16:
        model.half()
    model.to(device)
    if args.local_rank != -1:
        try:
            from apex.parallel import DistributedDataParallel as DDP
        except ImportError:
            raise ImportError("Please install apex from https://www.github.com/nvidia/apex to use distributed and fp16 training.")

        model = DDP(model)
    elif n_gpu > 1:
        model = torch.nn.DataParallel(model)

    # Prepare optimizer
    optimizer = optim.Adam(model.parameters(), lr=args.inner_learning_rate)
    K_last = 3
    epsilon = 1e-6
    task_rewards = {}
    last_observation = {}
    for task_id in range(train_task_number):
        task_rewards[task_id] = []
        last_observation[task_id] = 0

    if args.do_train:
        task_list = np.random.permutation(np.arange(train_task_number))
        
        for epoch in trange(int(N_iteration), desc="Iterations"):
            reward_prob = []
            for task_id in task_list:
                if len(task_rewards[task_id]) == 0:
                    reward_prob.append(1)
                elif len(task_rewards[task_id]) < K_last:
                    reward_prob.append(abs(np.random.choice(task_rewards[task_id], 1)[0]))
                else:
                    reward_prob.append(abs(np.random.choice(task_rewards[task_id][-K_last:], 1)[0]))
            reward_prob = get_train_prob(reward_prob, N_task,epsilon)
            selected_tasks = np.random.choice(task_list, N_task,replace=False)
            weight_before = deepcopy(model.state_dict())
            update_vars = []
            fomaml_vars = []
            for task_id in tqdm(selected_tasks, desc="Task"):
                task_acc = 0
                for _ in range(Inner_epochs):
                    train_support, train_query = processor.get_next_batch(train_batch_s, N_class, N_shot, N_query, 'train', task_id)
                    support_features = convert_examples_to_features(train_support, label_list, args.max_seq_length, tokenizer, output_mode)

                    support_input_ids = torch.tensor([f.input_ids for f in support_features], dtype=torch.long).to(device)
                    support_mask_ids = torch.tensor([f.input_mask for f in support_features], dtype=torch.long).to(device)
                    support_seg_ids = torch.tensor([f.segment_ids for f in support_features], dtype=torch.long).to(device)
                    if output_mode == "classification":
                        support_labels = torch.tensor([f.label_id for f in support_features], dtype=torch.long).to(device)
                    elif output_mode == "regression":
                        all_label_ids = torch.tensor([f.label_id for f in support_features], dtype=torch.float).to(device)
                    last_backup = deepcopy(model.state_dict())
                    model.train()
                    # define a new function to compute loss values for both output_modes
                    logits = model(support_input_ids, support_seg_ids, support_mask_ids, labels=None)
                    # print(logits.shape)
                    # print(support_labels.shape)
                    if output_mode == "classification":
                        loss_fct = CrossEntropyLoss()
                        loss = loss_fct(logits.view(-1, num_labels), support_labels.view(-1))
                    elif output_mode == "regression":
                        loss_fct = MSELoss()
                        loss = loss_fct(logits.view(-1), support_labels.view(-1))

                    preds = logits.detach().cpu().numpy()
                    result = compute_metrics(task_name, np.argmax(preds, axis=1), support_labels.detach().cpu().numpy())
                    task_acc = result['acc']
                    # logger.info("Batch %d ,Accuracy: %s", step,result["acc"])

                    if n_gpu > 1:
                        loss = loss.mean() # mean() to average on multi-gpu.
                    if args.gradient_accumulation_steps > 1:
                        loss = loss / args.gradient_accumulation_steps
                    
                    loss.backward()
                    optimizer.step()
                    optimizer.zero_grad()

                weight_after = deepcopy(model.state_dict())
                update_vars.append(weight_after)
                tmp_fomaml_var = {}

                task_rewards[task_id].append(task_acc-last_observation[task_id])
                last_observation[task_id] = task_acc
                if not Is_reptile:
                    for name in weight_after:
                        tmp_fomaml_var[name] = weight_after[name]-last_backup[name]
                    fomaml_vars.append(tmp_fomaml_var)
                model.load_state_dict(weight_before)
            new_weight_dict = {}
            # print(weight_before)
            if Is_reptile:
                for name in weight_before:
                    weight_list = [tmp_weight_dict[name] for tmp_weight_dict in update_vars]
                    weight_shape = list(weight_list[0].size())
                    stack_shape = [len(weight_list)] + weight_shape
                    stack_weight = torch.empty(stack_shape)
                    for i in range(len(weight_list)):
                        stack_weight[i,:] = weight_list[i] 
                    new_weight_dict[name] = torch.mean(stack_weight, dim=0).cuda()
                    new_weight_dict[name] = weight_before[name]+(new_weight_dict[name]-weight_before[name])/args.inner_learning_rate*args.outer_learning_rate
            else:
                for name in weight_before: 
                    weight_list = [tmp_weight_dict[name] for tmp_weight_dict in fomaml_vars]
                    weight_shape = list(weight_list[0].size())
                    stack_shape = [len(weight_list)] + weight_shape
                    stack_weight = torch.empty(stack_shape)
                    for i in range(len(weight_list)):
                        stack_weight[i,:] = weight_list[i]
                    new_weight_dict[name] = torch.mean(stack_weight, dim=0).cuda()
                    new_weight_dict[name] = weight_before[name]+new_weight_dict[name]/args.inner_learning_rate*args.outer_learning_rate
            model.load_state_dict(new_weight_dict)

    if args.do_train and (args.local_rank == -1 or torch.distributed.get_rank() == 0) :
        # Save a trained model, configuration and tokenizer
        model_to_save = model.module if hasattr(model, 'module') else model  # Only save the model it-self

        # If we save using the predefined names, we can load using `from_pretrained`
        output_model_file = os.path.join(args.output_dir, WEIGHTS_NAME)
        output_config_file = os.path.join(args.output_dir, CONFIG_NAME)

        torch.save(model_to_save.state_dict(), output_model_file)
        model_to_save.config.to_json_file(output_config_file)
        tokenizer.save_vocabulary(args.output_dir)

        # Load a trained model and vocabulary that you have fine-tuned
        model = BertForSequenceClassification.from_pretrained(args.output_dir, num_labels=num_labels)
        tokenizer = BertTokenizer.from_pretrained(args.output_dir, do_lower_case=args.do_lower_case)
    else:
        model = BertForSequenceClassification.from_pretrained(args.bert_model, num_labels=num_labels)
    model.to(device)

    loss_list = {}
    global_step = 0
    nb_tr_steps = 0
    Meta_optimizer = optim.Adam(model.parameters(), lr=args.FSL_learning_rate)

    if args.do_eval and (args.local_rank == -1 or torch.distributed.get_rank() == 0):
        weight_before = deepcopy(model.state_dict())
        for task_id in trange(fsl_task_number, desc="Task"):
            model.train() 
            for _ in range(args.FSL_epochs):
                support_examples = processor.get_fsl_support(args.data_dir, task_id)
                support_features = convert_examples_to_features(
                    support_examples, label_list, args.max_seq_length, tokenizer, output_mode
                )
                support_input = torch.tensor([f.input_ids for f in support_features], dtype=torch.long).to(device)
                support_mask = torch.tensor([f.input_mask for f in support_features], dtype=torch.long).to(device)
                support_seg = torch.tensor([f.segment_ids for f in support_features], dtype=torch.long).to(device)
                support_labels = torch.tensor([f.label_id for f in support_features], dtype=torch.long).to(device)
                
                logits = model(support_input, support_seg, support_mask, labels=None)
                loss = CrossEntropyLoss()
                loss = loss(logits.view(-1, num_labels), support_labels.view(-1))
                loss.backward()
                # print("Current loss: ", loss)
                # print("fsl training!")
                Meta_optimizer.step()
                Meta_optimizer.zero_grad()

            eval_examples,_ = processor.get_fsl_test_examples(args.data_dir, task_id)
            eval_features = convert_examples_to_features(
                eval_examples, label_list, args.max_seq_length, tokenizer, output_mode)
            logger.info("***** Running evaluation *****")
            logger.info("  Num examples = %d", len(eval_examples))
            logger.info("  Batch size = %d", args.eval_batch_size)
            all_input_ids = torch.tensor([f.input_ids for f in eval_features], dtype=torch.long)
            all_input_mask = torch.tensor([f.input_mask for f in eval_features], dtype=torch.long)
            all_segment_ids = torch.tensor([f.segment_ids for f in eval_features], dtype=torch.long)

            if output_mode == "classification":
                all_label_ids = torch.tensor([f.label_id for f in eval_features], dtype=torch.long)
            elif output_mode == "regression":
                all_label_ids = torch.tensor([f.label_id for f in eval_features], dtype=torch.float)

            eval_data = TensorDataset(all_input_ids, all_input_mask, all_segment_ids, all_label_ids)
            # Run prediction for full data
            eval_sampler = SequentialSampler(eval_data)
            eval_dataloader = DataLoader(eval_data, sampler=eval_sampler, batch_size=args.eval_batch_size)

            model.eval()
            eval_loss = 0
            nb_eval_steps = 0
            preds = []

            for input_ids, input_mask, segment_ids, label_ids in tqdm(eval_dataloader, desc="Evaluating"):
                input_ids = input_ids.to(device)
                input_mask = input_mask.to(device)
                segment_ids = segment_ids.to(device)
                label_ids = label_ids.to(device)

                with torch.no_grad():        
                    logits = model(input_ids, segment_ids, input_mask, labels=None)

                # create eval loss and other metric required by the task
                if output_mode == "classification":
                    loss_fct = CrossEntropyLoss()
                    tmp_eval_loss = loss_fct(logits.view(-1, num_labels), label_ids.view(-1))
                elif output_mode == "regression":
                    loss_fct = MSELoss()
                    tmp_eval_loss = loss_fct(logits.view(-1), label_ids.view(-1))
                
                eval_loss += tmp_eval_loss.mean().item()
                nb_eval_steps += 1
                if len(preds) == 0:
                    preds.append(logits.detach().cpu().numpy())
                else:
                    preds[0] = np.append(
                        preds[0], logits.detach().cpu().numpy(), axis=0)

            eval_loss = eval_loss / nb_eval_steps
            preds = preds[0]
            softmax_output = preds

            if output_mode == "classification":
                preds = np.argmax(preds, axis=1)
            elif output_mode == "regression":
                preds = np.squeeze(preds)
            print("Index    Prediction    Labels    softmax_output")
            if (task_id-1)%3 == 0:
                for i in range(len(preds)):
                    if preds[i] != all_label_ids.numpy()[i]:
                        print("Wrong Prediction! ")
                        print(i, "    ",preds[i], "    ", all_label_ids.numpy()[i], softmax_output[i])
                        print(eval_examples[i].text_a)
            result = compute_metrics(task_name, preds, all_label_ids.numpy())
            # loss = tr_loss/nb_tr_steps if args.do_train else None

            result['eval_loss'] = eval_loss
            result['global_step'] = global_step
            loss_list[task_id] = result['acc']
            # result['loss'] = loss

            output_eval_file = os.path.join(args.output_dir, "eval_results_"+str(task_id)+".txt")
            with open(output_eval_file, "w") as writer:
                logger.info("***** Eval results *****")
                for key in sorted(result.keys()):
                    logger.info("  %s = %s", key, str(result[key]))
                    writer.write("%s = %s\n" % (key, str(result[key])))
            model.load_state_dict(weight_before)
            
        for id, acc in loss_list.items():
            print("Task id: ", id, " ---- acc: ", acc)
        print("Average acc is: ", np.mean(list(loss_list.values())))
        # hack for MNLI-MM
        if task_name == "mnli":
            task_name = "mnli-mm"
            processor = processors[task_name]()

            if os.path.exists(args.output_dir + '-MM') and os.listdir(args.output_dir + '-MM') and args.do_train:
                raise ValueError("Output directory ({}) already exists and is not empty.".format(args.output_dir))
            if not os.path.exists(args.output_dir + '-MM'):
                os.makedirs(args.output_dir + '-MM')

            eval_examples = processor.get_dev_examples(args.data_dir)
            eval_features = convert_examples_to_features(
                eval_examples, label_list, args.max_seq_length, tokenizer, output_mode)
            logger.info("***** Running evaluation *****")
            logger.info("  Num examples = %d", len(eval_examples))
            logger.info("  Batch size = %d", args.eval_batch_size)
            all_input_ids = torch.tensor([f.input_ids for f in eval_features], dtype=torch.long)
            all_input_mask = torch.tensor([f.input_mask for f in eval_features], dtype=torch.long)
            all_segment_ids = torch.tensor([f.segment_ids for f in eval_features], dtype=torch.long)
            all_label_ids = torch.tensor([f.label_id for f in eval_features], dtype=torch.long)

            eval_data = TensorDataset(all_input_ids, all_input_mask, all_segment_ids, all_label_ids)
            # Run prediction for full data
            eval_sampler = SequentialSampler(eval_data)
            eval_dataloader = DataLoader(eval_data, sampler=eval_sampler, batch_size=args.eval_batch_size)

            model.eval()
            eval_loss = 0
            nb_eval_steps = 0
            preds = []

            for input_ids, input_mask, segment_ids, label_ids in tqdm(eval_dataloader, desc="Evaluating"):
                input_ids = input_ids.to(device)
                input_mask = input_mask.to(device)
                segment_ids = segment_ids.to(device)
                label_ids = label_ids.to(device)

                with torch.no_grad():
                    logits = model(input_ids, segment_ids, input_mask, labels=None)
            
                loss_fct = CrossEntropyLoss()
                tmp_eval_loss = loss_fct(logits.view(-1, num_labels), label_ids.view(-1))
            
                eval_loss += tmp_eval_loss.mean().item()
                nb_eval_steps += 1
                if len(preds) == 0:
                    preds.append(logits.detach().cpu().numpy())
                else:
                    preds[0] = np.append(
                        preds[0], logits.detach().cpu().numpy(), axis=0)

            eval_loss = eval_loss / nb_eval_steps
            preds = preds[0]
            preds = np.argmax(preds, axis=1)
            result = compute_metrics(task_name, preds, all_label_ids.numpy())
            loss = tr_loss/nb_tr_steps if args.do_train else None

            result['eval_loss'] = eval_loss
            result['global_step'] = global_step
            result['loss'] = loss

            output_eval_file = os.path.join(args.output_dir + '-MM', "eval_results.txt")
            with open(output_eval_file, "w") as writer:
                logger.info("***** Eval results *****")
                for key in sorted(result.keys()):
                    logger.info("  %s = %s", key, str(result[key]))
                    writer.write("%s = %s\n" % (key, str(result[key])))


if __name__ == "__main__":
    main()
