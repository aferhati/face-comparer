# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function

import time
import datetime
import sys
import random
import os
import math
import re
import numpy as np
import copy
import csv
from collections import defaultdict
from scipy import misc
from sklearn.decomposition import PCA
from sklearn import manifold
from sklearn import decomposition

from keras.models import Sequential
from keras.layers.core import Dense, MaxoutDense, Dropout, Reshape, Flatten, Activation
from keras.layers.convolutional import Convolution2D, MaxPooling2D
from keras.optimizers import Adagrad, SGD
from keras.regularizers import l2, l1
from keras.layers.normalization import BatchNormalization
from keras.utils import generic_utils
from keras.layers.advanced_activations import LeakyReLU
from keras.layers.recurrent import GRU

from ImageAugmenter import ImageAugmenter
from MyMerge import MyMerge
from Plotter import Plotter
import matplotlib.pyplot as plt
from util import load_model, save_model_config, save_model_weights, save_optimizer_state
from skimage import transform

SEED = 42
LFWCROP_GREY_FILEPATH = "/media/aj/grab/ml/datasets/lfwcrop_grey"
IMAGES_FILEPATH = LFWCROP_GREY_FILEPATH + "/faces"
TRAIN_COUNT_EXAMPLES = 20000
VALIDATION_COUNT_EXAMPLES = 256
TEST_COUNT_EXAMPLES = 0
EPOCHS = 1000 * 1000
BATCH_SIZE = 64
SAVE_DIR = os.path.dirname(os.path.realpath(__file__)) + "/experiments"
SAVE_PLOT_FILEPATH = "%s/plots/{identifier}.png" % (SAVE_DIR)
SAVE_DISTRIBUTION_PLOT_FILEPATH = "%s/plots/{identifier}_distribution.png" % (SAVE_DIR)
SAVE_CSV_FILEPATH = "%s/experiments/csv/{identifier}.csv" % (SAVE_DIR)
SAVE_WEIGHTS_DIR = "%s/experiments/weights" % (SAVE_DIR)
SAVE_OPTIMIZER_STATE_DIR = "%s/experiments/optimizer_state" % (SAVE_DIR)
SAVE_CODE_DIR = "%s/experiments/code/{identifier}" % (SAVE_DIR)
SAVE_WEIGHTS_AFTER_EPOCHS = 20
SAVE_WEIGHTS_AT_END = False
SHOW_PLOT_WINDOWS = True
Y_SAME = 1
Y_DIFFERENT = 0

np.random.seed(SEED)
random.seed(SEED)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("identifier", help="A short name/identifier for your experiment, e.g. 'ex42b_more_dropout'.")
    parser.add_argument("load", help="Identifier of a previous experiment that you want to continue (loads weights, optimizer state and history).")
    args = parser.parse_args()
    validate_identifier(args.identifier)
    if args.load:
        validate_identifier(args.load, must_exist=True)

    if identifier_exists(args.identifier):
        if args.identifier != args.load:
            agreed = ask_continue("[WARNING] Identifier '%s' already exists and is different from load-identifier '%s'. It will be overwritten. Continue? [y/n]" % (args.identifier, args.load))
            if not agreed:
                return

    print("-----------------------")
    print("Loading validation dataset...")
    print("-----------------------")
    print("")
    pairs_val = get_image_pairs(IMAGES_FILEPATH, VALIDATION_COUNT_EXAMPLES, pairs_of_same_imgs=False, ignore_order=True, exclude_images=list(), seed=SEED, verbose=True)

    print("-----------------------")
    print("Loading training dataset...")
    print("-----------------------")
    print("")
    pairs_train = get_image_pairs(IMAGES_FILEPATH, TRAIN_COUNT_EXAMPLES, pairs_of_same_imgs=False, ignore_order=True, exclude_images=pairs_val, seed=SEED, verbose=True)
    print("-----------------------")

    assert len(pairs_val) == VALIDATION_COUNT_EXAMPLES
    assert len(pairs_train) == TRAIN_COUNT_EXAMPLES

    X_val, y_val = image_pairs_to_xy(pairs_val)
    X_train, y_train = image_pairs_to_xy(pairs_train)

    """
    plot_person_img_distribution(
        img_filepaths_test, img_filepaths_val, img_filepaths_train,
        only_y_value=Y_SAME,
        show_plot_windows=SHOW_PLOT_WINDOWS,
        save_to_filepath=SAVE_DISTRIBUTION_PLOT_FILEPATH
    )
    """

    print("Creating model...")
    model, optimizer = create_model()
    
    # Calling the compile method seems to mess with the seeds (theano problem?)
    # Therefore they are reset here (numpy seeds seem to be unaffected)
    # (Seems to still not make runs reproducible.)
    random.seed(SEED)

    la_plotter = LossAccPlotter(save_to_filepath=SAVE_PLOT_FILEPATH.format(identifier=args.identifier))

    if args.load:
        print("Loading previous model...")
        epoch_start, history = load_previous_model(args.load, model, optimizer, la_plotter)
    else:
        epoch_start = 0
        history = History()
    
    print("Training...")
    train_loop(args.identifier, model, optimizer, epoch_start, history, la_plotter)
    
    print("Finished.")

def validate_identifier(identifier, must_exist=True):
    if not identifier or identifier != re.sub("[^a-zA-Z0-9_]", "", identifier):
        raise Exception("Invalid characters in identifier, only a-z A-Z 0-9 and _ are allowed.")
    if must_exist:
        if not identifier_exists(identifier):
            raise Exception("No model with identifier '{}' seems to exist.".format(identifier))

def identifier_exists(identifier):
    filepath = SAVE_CSV_FILEPATH.format(identifier=identifier)
    if os.path.isfile(filepath):
        return True
    else:
        return False

def ask_continue(message):
    choice = raw_input(message)
    while choice not in ["y", "n"]:
        choice = raw_input("Enter 'y' (yes) or 'n' (no) to continue.")
    return choice == "y"

def load_previous_model(identifier, model, optimizer, la_plotter):
    # load optimizer state
    (success, last_epoch) = load_optimizer_state(optimizer, SAVE_OPTIMIZER_STATE_DIR, identifier)
    
    # load weights
    (success, last_epoch) = load_weights(model, SAVE_WEIGHTS_DIR, identifier)
    
    if not success:
        raise Exception("Cannot continue previous experiment, because no weights were saved (yet?).")
    
    # load history from csv file
    history = History()
    history.load_from_file("{}/{}.csv".format(SAVE_CSV_DIR, identifier))
    history = load_history(SAVE_CSV_DIR, identifier, last_epoch=last_epoch)
    
    # update loss acc plotter
    la_plotter.values_loss_train = history.loss_train
    la_plotter.values_loss_val = history.loss_val
    la_plotter.values_acc_train = history.acc_train
    la_plotter.values_acc_val = history.acc_val
    
    return last_epoch, history

def load_history(save_history_dir, identifier):
    # load previous loss/acc values per epoch from csv file
    csv_filepath = "{}/{}.csv".format(save_history_dir, identifier)
    csv_lines = open(csv_filepath, "r").readlines()
    csv_lines = csv_lines[1:] # no header
    csv_cells = [line.strip().split(",") for line in csv_lines]
    epochs = [int(cells[0]) for cells in csv_cells]
    stats_train_loss = [float(cells[1]) for cells in csv_cells]
    stats_val_loss = [float(cells[2]) for cells in csv_cells]
    stats_train_acc = [float(cells[3]) for cells in csv_cells]
    stats_val_acc = [float(cells[4]) for cells in csv_cells]
    
    if last_epoch == "last":
        start_epoch = epochs[-1] + 1
    else:
        start_epoch = last_epoch + 1
    
    epochs = range(start_epoch)
    history.add_all(start_epoch,
                    stats_train_loss[0:start_epoch],
                    stats_train_val[0:start_epoch],
                    stats_acc_train[0:start_epoch],
                    stats_acc_val[0:start_epoch])
    return history

def create_model():
    model = Sequential()
    
    # 32 x 32+2 x 32+2 = 32x34x34
    model.add(Convolution2D(32, 1, 3, 3, border_mode="full"))
    model.add(LeakyReLU(0.33))
    model.add(Dropout(0.00))
    # 32 x 34-2 x 34-2 = 32x32x32
    model.add(Convolution2D(32, 32, 3, 3, border_mode="valid"))
    model.add(LeakyReLU(0.33))
    model.add(Dropout(0.00))
    
    # 32 x 32/2 x 32/2 = 32x16x16
    model.add(MaxPooling2D(poolsize=(2, 2)))
    
    # 64 x 16-2 x 16-2 = 64x14x14
    model.add(Convolution2D(64, 32, 3, 3, border_mode="valid"))
    model.add(LeakyReLU(0.33))
    model.add(Dropout(0.00))
    # 64 x 14-2 x 14-2 = 64x12x12
    model.add(Convolution2D(64, 64, 3, 3, border_mode="valid"))
    model.add(LeakyReLU(0.33))
    model.add(Dropout(0.50))
    
    # 64x14x14 = 64x196 = 12544
    # In 64*4 slices: 64*4 x 196/4 = 256x49
    model.add(Reshape(64*4, int(196/4)))
    model.add(BatchNormalization((64*4, int(196/4))))
    
    model.add(GRU(196/4, 64, return_sequences=True))
    model.add(Flatten())
    model.add(BatchNormalization((64*(64*4),)))
    model.add(Dropout(0.50))
    model.add(Dense(64*(64*4), 1, init="glorot_uniform", W_regularizer=l2(0.000001)))
    model.add(Activation("sigmoid"))

    optimizer = Adagrad()
    
    print("Compiling model...")
    model.compile(loss="binary_crossentropy", class_mode="binary", optimizer=optimizer)
    
    return model, optimizer

class History(object):
    def __init__(self):
        #self.first_epoch = 1000 * 1000
        #self.last_epoch = -1
        self.epochs = []
        self.loss_train = []
        self.loss_val = []
        self.acc_train = []
        self.acc_val = []

    def add(self, epoch, loss_train=None, loss_val=None, acc_train=None, acc_val=None):
        self.epochs.append(epoch)
        self.loss_train.append(loss_train)
        self.loss_val.append(loss_val)
        self.acc_train.append(acc_train)
        self.acc_val.append(acc_val)
        self.first_epoch = min(self.first_epoch, epoch)
        self.last_epoch = max(self.last_epoch, epoch)

    def add_all(self, start_epoch, loss_train, loss_val, acc_train, acc_val):
        last_epoch = start_epoch + len(loss_train)
        for epoch, lt, lv, at, av in zip(range(start_epoch, last_epoch+1), loss_train, loss_val, acc_train, acc_val):
            self.add(epoch, loss_train=lt, loss_val=lv, acc_train=at, acc_val=av)

    def save_to_filepath(self, csv_filepath):
        with open(csv_filepath, "w") as fp:
            csvw = csv.writer(fp, delimiter=",")
            # header row
            rows = [["epoch", "train_loss", "val_loss", "train_acc", "val_acc"]]
            
            #data = data + [[r_e, r_tl, r_vl, r_ta, r_va] for r_e, r_tl, r_vl, r_ta, r_va in zip(range(epoch+1), stats_train_loss, stats_val_loss, stats_train_acc, stats_val_acc)]
            rows.extend(zip(range(epoch+1), stats_train_loss, stats_val_loss, stats_train_acc, stats_val_acc))
            csvw.writerows(rows)

    def load_from_file(self, csv_filepath, last_epoch=None):
        # load previous loss/acc values per epoch from csv file
        csv_lines = open(csv_filepath, "r").readlines()
        csv_lines = csv_lines[1:] # no header
        csv_cells = [line.strip().split(",") for line in csv_lines]
        epochs = [int(cells[0]) for cells in csv_cells]
        stats_loss_train = [float(cells[1]) for cells in csv_cells]
        stats_loss_val = [float(cells[2]) for cells in csv_cells]
        stats_acc_train = [float(cells[3]) for cells in csv_cells]
        stats_acc_val = [float(cells[4]) for cells in csv_cells]
        
        if last_epoch is not None and last_epoch is not "last":
            epochs = epochs[0:last_epoch+1]
            stats_loss_train = stats_loss_train[0:last_epoch+1]
            stats_loss_val = stats_loss_val[0:last_epoch+1]
            stats_acc_train = stats_acc_train[0:last_epoch+1]
            stats_acc_val = stats_acc_val[0:last_epoch+1]
        
        self.epochs = epochs
        self.loss_train = stats_loss_train
        self.loss_val = stats_loss_val
        self.acc_train = stats_acc_train
        self.acc_val = stats_acc_val

def train_loop(identifier, model, optimizer, epoch_start, history, la_plotter):
    # Loop over each epoch, i.e. executes 20 times if epochs set to 20
    # start_epoch is not 0 if we continue an older model.
    for epoch in range(epoch_start, EPOCHS):
        print("Epoch", epoch)
        
        # Variables to collect the sums for loss and accuracy (for training and
        # validation dataset). We will use them to calculate the loss/acc per
        # example (which will be ploted and added to the history).
        loss_train_sum = 0
        loss_val_sum = 0
        acc_train_sum = 0
        acc_val_sum = 0
        
        # Training loop
        progbar = generic_utils.Progbar(n_examples_train)
        
        for X_batch, Y_batch in flow_batches(X_train, y_train, pca, embedder, batch_size=cfg["batch_size"], shuffle=True, train=True):
            loss, acc = model.train_on_batch(X_batch, Y_batch, accuracy=True)
            progbar.add(len(X_batch), values=[("train loss", loss), ("train acc", acc)])
            loss_train_sum += (loss * len(X_batch))
            acc_train_sum += (acc * len(X_batch))
        
        # Validation loop
        progbar = generic_utils.Progbar(n_examples_val)
        
        # Iterate over each batch in the validation data
        # and calculate loss and accuracy for each batch
        for X_batch, Y_batch in flow_batches(X_val, y_val, pca, embedder, batch_size=cfg["batch_size"], shuffle=False, train=False):
            loss, acc = model.test_on_batch(X_batch, Y_batch, accuracy=True)
            progbar.add(len(X_batch), values=[("val loss", loss), ("val acc", acc)])
            loss_val_sum += loss
            acc_val_sum += acc

        # Calculate the loss and accuracy for this epoch
        # (averaged over all training data batches)
        loss_train = loss_train_sum / len(X_train)
        acc_train = acc_train_sum / len(X_train)
        loss_val = loss_val_sum / len(X_val)
        acc_val = acc_val_sum / len(X_val)
        
        history.add(epoch, loss_train=loss_train, loss_val=loss_val, acc_train=acc_train, acc_val=acc_val)
        
        # Update plots with new data from this epoch
        # We start plotting _after_ the first epoch as the first one usually contains
        # a huge fall in loss (increase in accuracy) making it harder to see the
        # minor swings at epoch 1000 and later.
        if epoch > 0:
            la_plotter.add_values(epoch, loss_train=loss_train, loss_val=loss_val, acc_train=acc_train, acc_val=acc_val)
        
        # Save the history to a csv file
        if SAVE_CSV_FILEPATH is not None:
            csv_filepath = SAVE_CSV_FILEPATH.format(identifier=identifier)
            history.save_to_filepath(csv_filepath)
        
        # Save the weights and optimizer state to files
        swae = SAVE_WEIGHTS_AFTER_EPOCHS
        if swae and swae > 0 and (epoch+1) % swae == 0:
            print("Saving model...")
            #save_model_weights(model, cfg["save_weights_dir"], model_name + ".at" + str(epoch) + ".weights")
            #save_optimizer_state(optimizer, cfg["save_optimizer_state_dir"], model_name + ".at" + str(epoch) + ".optstate", overwrite=True)
            save_model_weights(model, SAVE_WEIGHTS_DIR, "{}.last.weights".format(model_name), overwrite=True)
            save_optimizer_state(optimizer, SAVE_OPTIMIZER_STATE_DIR, "{}.last.optstate".format(model_name), overwrite=True)

if __name__ == "__main__":
    main()
