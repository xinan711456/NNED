# This file mainly implements a basic attention model for neural event extraction
# input file consists of sentences which contain one-event.
import os
import re
import sys
import time
import random
import cPickle
from collections import Counter
from aceEventUtil import loadEventHierarchy
#from get_constituent_topdown_oracle import unkify
from util import outputPRF, outputParameters
from util import loadVocab, loadTag, loadTrainData, loadPretrain
from util import loadTrainData2, loadPretrain2
from util import output_normal_pretrain, output_dynet_format
from util import check_trigger, check_trigger_test, check_data
from util import get_trigger, evalPRF, evalPRF_iden

import numpy as np
from nltk.tokenize import sent_tokenize, word_tokenize

import torch
import torch.autograd as autograd
from torch.autograd import Variable
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim

from lstm_trigger import LSTMTrigger
torch.manual_seed(1)
Tab = "\t"

def prepare_sequence(seq, to_ix):
    idxs = [to_ix[w] if w in to_ix else len(to_ix)-1 for w in seq]
    tensor = autograd.Variable(torch.LongTensor(idxs), requires_grad=False)
    return tensor

def arr2tensor(arr):
    tensor = autograd.Variable(torch.LongTensor(arr), requires_grad=False)
    return tensor

def eval_model(data, model, loss_function, data_flag, gpu):
    debug = True
    loss_all = 0
    gold_results = []
    pred_results = []
    for sent, tags, gold_triggers in data[:]:

        sentence_in = arr2tensor(sent)
        targets = arr2tensor(tags)

        if gpu:
            sentence_in = sentence_in.cuda()
            targets = targets.cuda()

        tag_space, tag_scores, tag_space_iden = model(sentence_in, gpu)

        _, tag_outputs = tag_scores.data.max(1)
        if gpu: tag_outputs = tag_outputs.cpu()
        #print tag_outputs.numpy().tolist()
        sys_triggers = get_trigger(tag_outputs.view(len(tags)).numpy().tolist())

        if 1:
            gold_results.append(gold_triggers)
            pred_results.append(sys_triggers)
        else:
            gold_results.append(tags)
            pred_results.append(tag_outputs.numpy().tolist())

        if debug and data_flag == "train":
            if len(gold_results) in range(10, 13):
                if len(gold_triggers) == 0: continue
                print "-gold tag", gold_triggers
                print "-out tag", sys_triggers
        #loss = loss_function(tag_scores, targets)
        loss = loss_function(tag_space, targets)
        loss_all += loss.data[0]
    prf = evalPRF(gold_results, pred_results, data_flag)
    prf_iden = evalPRF_iden(gold_results, pred_results)
    return loss_all, prf, prf_iden

def load_data2(args_arr):
    debug = True
    train_filename, dev_filename, test_filename, pretrain_embedding_filename, _tag_filename, _vocab_filename, model_path = args_arr

    pretrain_embedding, pretrain_vocab = loadPretrain2(pretrain_embedding_filename)
    print "## pretrained embedding loaded.", time.asctime(), pretrain_embedding.shape

    training_data = loadTrainData2(train_filename)
    print "## train loaded.", train_filename, time.asctime()
    dev_data = loadTrainData2(dev_filename)
    print "## dev loaded.", test_filename, time.asctime()
    test_data = loadTrainData2(test_filename)
    print "## test loaded.", test_filename, time.asctime()
    all_data = training_data + dev_data + test_data
    vocab = sorted(list(set([word_text for sent_text, _ in all_data for word_text in sent_text])))
    vocab = dict(zip(vocab, range(len(vocab))))
    vocab["<unk>"] = len(vocab)
    #pretrain_vocab = vocab
    #pretrain_embedding = np.random.uniform(-1.0, 1.0, (len(pretrain_vocab), 300))
    unk_id = len(pretrain_vocab)-1

    id2word = dict([(pretrain_vocab[item], item) for item in pretrain_vocab])
    if debug:
        for i in range(10, 13):
            sent_tags = training_data[i][1]
            triggers = [(word_idx, training_data[i][0][word_idx], tag) for word_idx, tag in enumerate(sent_tags) if tag != "none"]
            print "## eg:", training_data[i]
            print triggers
    tags_data = sorted(list(set([tag_text for sent_text, tags_text in training_data for tag_text in tags_text if tag_text != "none"])))
    tags_data = dict(zip(tags_data, range(1, len(tags_data)+1)))
    #tags_data = dict([(tag_text, tag_id+1) for tag_id, tag_text in enumerate(tags_data)])
    tags_data["none"] = 0

    ## text to id
    training_data = [([pretrain_vocab[word_text] if word_text in pretrain_vocab else unk_id for word_text in sent_text_arr], [tags_data.get(tag_text) for tag_text in tags_text_arr]) for sent_text_arr, tags_text_arr in training_data]
    dev_data = [([pretrain_vocab[word_text] if word_text in pretrain_vocab else unk_id for word_text in sent_text_arr], [tags_data.get(tag_text) for tag_text in tags_text_arr]) for sent_text_arr, tags_text_arr in dev_data]
    test_data = [([pretrain_vocab[word_text] if word_text in pretrain_vocab else unk_id for word_text in sent_text_arr], [tags_data.get(tag_text) for tag_text in tags_text_arr]) for sent_text_arr, tags_text_arr in test_data]
    if debug:
        for i in range(10, 13):
            sent_tags = training_data[i][1]
            triggers = [(word_idx, id2word[training_data[i][0][word_idx]], tag) for word_idx, tag in enumerate(sent_tags) if tag != 0]
            print "## eg:", training_data[i]
            print triggers
    return training_data, dev_data, test_data, pretrain_vocab, tags_data, pretrain_embedding, model_path

def load_data(args_arr):
    train_filename, _dev_filename, test_filename, pretrain_embedding_filename, tag_filename, vocab_filename, model_path = args_arr

# pretrain embedding: matrix (vocab_size, pretrain_embed_dim)
    pretrain_embedding = loadPretrain(pretrain_embedding_filename)
    print "## pretrained embedding loaded.", time.asctime(), pretrain_embedding.shape

# vocab: word: word_id
    vocab = loadVocab(vocab_filename)
    print "## vocab loaded.", time.asctime()

# train test
    training_data = loadTrainData(train_filename)
    print "## train loaded.", train_filename, time.asctime()
    #training_data = check_data(training_data, vocab)
    test_data = loadTrainData(test_filename)
    print "## test loaded.", test_filename, time.asctime()
    #test_data = check_data(test_data, vocab)
    #check_trigger_test(training_data, test_data)

# tags_data: tag_name: tag_id
    tags_data = loadTag(tag_filename)
    print "## event tags loaded.", time.asctime()

    #for sent, tag in training_data:
    #    check_trigger(tag)
    #for sent, tag in test_data:
    #    check_trigger(tag)
    return training_data, None, test_data, vocab, tags_data, pretrain_embedding, model_path

def init_embedding(dim1, dim2):
    init_embedding = np.random.uniform(-0.01, 0.01, (dim, dim))
    return np.matrix(init_embedding)

##############
def getArg(args, flag):
    arg = None
    if flag in args:
        arg = args[args.index(flag)+1]
    return arg

# arguments received from arguments
def parseArgs(args):
    arg1 = getArg(args, "-train")
    arg2 = getArg(args, "-dev")
    arg3 = getArg(args, "-test")
    arg4 = getArg(args, "-embed")

    arg5 = getArg(args, "-tag")
    arg6 = getArg(args, "-vocab")

    arg7 = getArg(args, "-model")
    return [arg1, arg2, arg3, arg4, arg5, arg6, arg7]


def train(para_arr, data_sets, debug=False):

    vocab_size, tagset_size, embedding_dim, hidden_dim = para_arr[:4]
    dropout, bilstm, num_layers, gpu, iteration_num, learning_rate = para_arr[4:10]
    training_size, dev_size, test_size = para_arr[10:13]
    conv_width1, conv_width2, conv_filter_num, hidden_dim_snd = para_arr[13:17]
    model_path, test_as_dev, shuffle_train, use_conv, use_pretrain, loss_flag, opti_flag = para_arr[17:24]

    training_data, dev_data, test_data, vocab, tags_data, pretrain_embedding = data_sets

    training_data = [(item[0], item[1], get_trigger(item[1])) for item in training_data]
    dev_data = [(item[0], item[1], get_trigger(item[1])) for item in dev_data]
    test_data = [(item[0], item[1], get_trigger(item[1])) for item in test_data]
    random_dim = 10

# init model
    if not use_pretrain:
        pretrain_embedding = init_embedding(vocab_size, embedding_dim)

    model_params_to_feed = [use_pretrain, use_conv, bilstm, gpu, num_layers, dropout, embedding_dim, hidden_dim, hidden_dim_snd, conv_width1, conv_width2, conv_filter_num, vocab_size, tagset_size, random_dim, pretrain_embedding]

    model = LSTMTrigger(model_params_to_feed)

    if loss_flag == "cross-entropy":
        loss_function = nn.CrossEntropyLoss()
    else:
        loss_function = nn.NLLLoss()

    parameters = filter(lambda a:a.requires_grad, model.parameters())
    if opti_flag == "ada":
        optimizer = optim.Adadelta(parameters, lr=learning_rate)
    elif opti_flag == "sgd":
        optimizer = optim.SGD(model.parameters(), lr=learning_rate)

# training
    best_f1 = -1.0
    for epoch in range(iteration_num):
        training_id = 0
        #if model.word_embeddings.weight.grad is not None:
        #    print "## word embedding grad:", torch.sum(model.word_embeddings.weight.grad), model.word_embeddings.weight.grad[:5, :5]
        for sent, tags, gold_triggers in training_data:
            if training_id == 0:
                print "## training instance:", training_id
            iden_tags = [1 if tag != 0 else tag for tag in tags]

            model.zero_grad()
            model.hidden = model.init_hidden(gpu)

            sentence_in = arr2tensor(sent)
            targets = arr2tensor(tags)
            iden_targets = arr2tensor(iden_tags)

            if gpu:
                sentence_in = sentence_in.cuda()
                targets = targets.cuda()
                iden_targets = iden_targets.cuda()

            #if training_id < 1:    debug = True
            tag_space, tag_scores, tag_space_iden = model(sentence_in, gpu, debug)

            #loss = loss_function(tag_scores, targets)
            loss = loss_function(tag_space, targets) + loss_function(tag_space_iden, iden_targets)
            #loss_iden = loss_function(tag_space_iden, iden_targets)
            #loss += loss_iden
            loss.backward()
            optimizer.step()
            training_id += 1

        loss_train, prf_train, prf_train_iden = eval_model(training_data, model, loss_function, "train", gpu)
        print "## train results on epoch:", epoch, Tab, loss_train, time.asctime(), Tab,
        outputPRF(prf_train)
        print "## Iden result:", 
        outputPRF(prf_train_iden)

# result on dev
        loss_dev, prf_dev, prf_dev_iden = eval_model(dev_data, model, loss_function, "dev", gpu)
        if prf_dev[2] > best_f1:
            print "##-- New best dev results on epoch", epoch, Tab, best_f1, "(old best)", Tab, loss_dev, time.asctime(), Tab,
            best_f1 = prf_dev[2]
            torch.save(model, model_path)
        else:
            print "##-- dev results on epoch", epoch, Tab, best_f1, "(best f1)", Tab, loss_dev, time.asctime(), Tab,
        outputPRF(prf_dev)
        print "## Iden result:",
        outputPRF(prf_dev_iden)
# result on test
        if epoch >= 10 and epoch % 10 == 0:
            #if epoch % 100 == 0:
            #    model = torch.load(model_path)
            loss_test, prf_test, prf_test_iden = eval_model(test_data, model, loss_function, "test_final", gpu)
            print "##-- test results on epoch", epoch, Tab, loss_test, time.asctime(), Tab,
            outputPRF(prf_test)
            print "## Iden result:",
            outputPRF(prf_test_iden)

# final result on test
    model = torch.load(model_path)
    loss_test, prf_test, prf_test_iden = eval_model(test_data, model, loss_function, "test_final", gpu)
    print "## Final results on test", loss_test, time.asctime(), Tab,
    outputPRF(prf_test)
    print "## Iden result:",
    outputPRF(prf_test_iden)


if __name__ == "__main__":
    print "Usage: python .py -train trainFile -embed embeddingFile -ace aceArgumentFile -dev devFile -test testFile"
    print sys.argv

    #######################
    ## set parameters
    gpu = torch.cuda.is_available()
    print "gpu available:", gpu
    #gpu = false
    test_as_dev = False
    shuffle_train = True # to select last 500 as dev in feng data
    use_pretrain = True
    loss_flag = "cross-entropy" # or nlloss
    opti_flag = "sgd"  # or "ada"
    dropout = 0.5
    bilstm = True
    num_layers = 1
    iteration_num = 200
    hidden_dim = 100
    learning_rate = 0.03
    embedding_dim = 100

    use_conv = False
    conv_width1 = 2
    conv_width2 = 3
    conv_filter_num = 100
    hidden_dim_snd = 300


    #######################
    ## load datasets
    args_arr = parseArgs(sys.argv)
    train_filename, dev_filename, test_filename, pretrain_embedding_filename, tag_filename, vocab_filename, model_path = args_arr

    if "-dev" in sys.argv:
        training_data, dev_data, test_data, vocab, tags_data, pretrain_embedding, model_path = load_data2(args_arr)
    else:
        training_data, dev_data, test_data, vocab, tags_data, pretrain_embedding, model_path = load_data(args_arr)
        if test_as_dev:
            dev_data = test_data
        else:
            if shuffle_train:
                random.shuffle(training_data, lambda: 0.3) # shuffle data before get dev
            training_data = training_data[:-500]
            dev_data = training_data[-500:]
    model_path = model_path + "_" + time.strftime("%Y%m%d%H%M%S", time.gmtime()) + "_"

    vocab_size = len(vocab)
    pretrain_vocab_size, pretrain_embed_dim = pretrain_embedding.shape
    tagset_size = len(tags_data)

    if 0:
        all_data = training_data+dev_data+test_data
        sent_lens = [len(item[0]) for item in all_data]
        print "## Statistic sent length:", max(sent_lens), min(sent_lens)
        sys.exit(0)
    if 0:
        output_normal_pretrain(pretrain_embedding, vocab, "../ni_data/f.ace.pretrain300.vectors")
        output_dynet_format(training_data, vocab, tags_data, "../ni_data/f.ace_trigger.train")
        output_dynet_format(dev_data, vocab, tags_data, "../ni_data/f.ace_trigger.dev")
        output_dynet_format(test_data, vocab, tags_data, "../ni_data/f.ace_trigger.test")
        sys.exit(0)


    #######################
    ## store and output all parameters
    if use_pretrain: embedding_dim = pretrain_embed_dim

    para_arr = [vocab_size, tagset_size, embedding_dim, hidden_dim]
    para_arr.extend([dropout, bilstm, num_layers, gpu, iteration_num, learning_rate])
    para_arr.extend([len(training_data), len(dev_data), len(test_data)])
    para_arr.extend([conv_width1, conv_width2, conv_filter_num, hidden_dim_snd])
    param_str = "p"+str(embedding_dim) + "_hd" + str(hidden_dim) + "_2hd" + str(hidden_dim_snd)
    if use_conv: param += "_f" + str(conv_filter_num) + "_c" + str(conv_width1) + "_c" + str(conv_width2)
    param_str += "_lr" + str(learning_rate*100)# + "_" + str() + "_" + str()
    model_path += param_str
    para_arr.extend([model_path, test_as_dev, shuffle_train, use_conv, use_pretrain, loss_flag, opti_flag])
    outputParameters(para_arr)

    #######################
    # begin to train
    data_sets = training_data, dev_data, test_data, vocab, tags_data, pretrain_embedding
    train(para_arr, data_sets)

