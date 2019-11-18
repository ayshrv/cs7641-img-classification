import os
import numpy as np

import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from sklearn import mixture
from scipy.stats import mode

from torch.utils.tensorboard import SummaryWriter

import params
import utils

from dataloader import DataLoader
from models.models import Model


class ModelTrainer:
    """Class for training and testing of model"""

    def __init__(self, args):
        self.epoch_timer = utils.TimeIt(print_str="Epoch")
        self.args = args

        if self.args.training_mode == "gmm":
            self.dataloader = DataLoader(self.args)
        else:
            if self.args.eval:
                if self.args.eval_checkpoint == "":
                    raise ValueError(
                        "Eval mode is set, but no checkpoint path is provided!"
                    )
                self.loader = torch.load(self.args.eval_checkpoint)

            self.dataloader = DataLoader(self.args)

            # Load the model
            self.model = Model(self.args)

            if self.args.eval:
                self.model.load_state_dict(self.loader)

            if self.args.cuda:
                self.model.cuda()

            self.best_test_accuracy = 0.0
            self.best_test_epoch = 0

            if self.args.eval is False:

                if self.args.optimiser == "sgd":
                    self.opt = optim.SGD(
                        self.model.parameters(),
                        lr=self.args.learning_rate,
                        momentum=self.args.momentum,
                        weight_decay=self.args.weight_decay,
                    )
                elif self.args.optimiser == "adam":
                    self.opt = optim.Adam(
                        self.model.parameters(),
                        lr=self.args.learning_rate,
                        weight_decay=self.args.weight_decay,
                    )
                else:
                    raise Exception("Unknown optimiser {}".format(self.args.optim))

                if self.args.lr_scheduler:
                    self.lr_scheduler = optim.lr_scheduler.MultiStepLR(
                        self.opt,
                        milestones=self.args.lr_schedule,
                        gamma=self.args.lr_decay_factor,
                    )
                if self.args.lr_reducer:
                    self.lr_reducer = torch.optim.lr_scheduler.ReduceLROnPlateau(
                        self.opt,
                        factor=np.sqrt(0.1),
                        cooldown=0,
                        patience=5,
                        min_lr=0.5e-6,
                    )

                # Loss function
                self.criterion = nn.CrossEntropyLoss()

                self.args.logdir = os.path.join("checkpoints", self.args.exp_name)
                utils.create_dir(self.args.logdir)

                if self.args.filelogger:
                    self.logger_path = os.path.join(
                        "checkpoints",
                        self.args.exp_name,
                        "%s_values.log" % self.args.exp_name,
                    )
                    self.logger = {
                        "train_loss_per_iter": [],
                        "train_loss_per_epoch": [],
                        "val_loss_per_iter": [],
                        "val_loss_per_epoch": [],
                        "val_accuracy_per_iter": [],
                        "val_accuracy_per_epoch": [],
                        "test_loss": [],
                        "test_accuracy": [],
                        "best_epoch": 0,
                        "best_test_accuracy": 0.0,
                        "ssl_loss": [],
                        "ssl_accuracy": [],
                        "ssl_correct": [],
                    }
                if self.args.tensorboard:
                    self.writer = SummaryWriter(log_dir=self.args.logdir, flush_secs=30)
                    self.writer.add_text("Arguments", params.print_args(self.args))

    def train_val(self, epoch):
        """Train the model for one epoch and evaluate on val split if log_intervals have passed"""

        for batch_idx, batch in enumerate(self.dataloader.train_loader):
            self.model.train()
            self.opt.zero_grad()

            self.iter += 1

            images, targets, indices = batch
            if self.args.cuda:
                images, targets = images.cuda(), targets.cuda()

            logits, unnormalised_scores = self.model(images)
            loss = self.criterion(unnormalised_scores, targets)
            loss.backward()
            self.opt.step()

            if batch_idx % self.args.log_interval == 0:
                val_loss, val_acc = self.evaluate("Val", n_batches=4)

                train_loss, val_loss, val_acc = utils.convert_for_print(
                    loss, val_loss, val_acc
                )

                if self.args.filelogger:
                    self.logger["train_loss_per_iter"].append([self.iter, train_loss])
                    self.logger["val_loss_per_iter"].append([self.iter, val_loss])
                    self.logger["val_accuracy_per_iter"].append([self.iter, val_acc])

                if self.args.tensorboard:
                    self.writer.add_scalar("Loss_at_Iter/Train", train_loss, self.iter)
                    self.writer.add_scalar("Loss_at_Iter/Val", val_loss, self.iter)
                    self.writer.add_scalar("Accuracy_at_Iter/Val", val_acc, self.iter)

                examples_this_epoch = batch_idx * len(images)
                epoch_progress = 100.0 * batch_idx / len(self.dataloader.train_loader)
                print(
                    "Train Epoch: %3d [%5d/%5d (%5.1f%%)]\t "
                    "Train Loss: %0.6f\t Val Loss: %0.6f\t Val Acc: %0.1f"
                    % (
                        epoch,
                        examples_this_epoch,
                        len(self.dataloader.train_loader.dataset),
                        epoch_progress,
                        train_loss,
                        val_loss,
                        val_acc,
                    )
                )
        if self.args.lr_reducer:
            val_loss, val_acc = self.evaluate("Val", n_batches=None)
            self.lr_reducer.step(val_loss)

        val_loss, val_acc = utils.convert_for_print(val_loss, val_acc)

        if self.args.filelogger:
            self.logger["train_loss_per_epoch"].append([epoch, train_loss])
            self.logger["val_loss_per_epoch"].append([epoch, val_loss])
            self.logger["val_accuracy_per_epoch"].append([epoch, val_acc])

        if self.args.tensorboard:
            self.writer.add_scalar("Loss_at_Epoch/Train", train_loss, epoch)
            self.writer.add_scalar("Loss_at_Epoch/Val", val_loss, epoch)
            self.writer.add_scalar("Accuracy_at_Epoch/Val", val_acc, epoch)

    def evaluate(self, split, epoch=None, verbose=False, n_batches=None):
        """Evaluate model on val or test data"""

        self.model.eval()
        with torch.no_grad():
            loss = 0
            correct = 0
            n_examples = 0

            if split == "Val":
                loader = self.dataloader.val_loader
            elif split == "Test":
                loader = self.dataloader.test_loader

            for batch_idx, batch in enumerate(loader):
                images, targets, _ = batch
                if args.cuda:
                    images, targets = images.cuda(), targets.cuda()

                logits, unnormalised_scores = self.model(images)
                loss += F.cross_entropy(unnormalised_scores, targets, reduction="sum")
                pred = logits.max(1, keepdim=False)[1]
                correct += pred.eq(targets).sum()
                n_examples += pred.shape[0]
                if n_batches and (batch_idx >= n_batches):
                    break

            loss /= n_examples
            acc = 100.0 * correct / n_examples

            if split == "Test" and acc >= self.best_test_accuracy:
                self.best_test_accuracy = utils.convert_for_print(acc)
                self.best_test_epoch = epoch
                if self.args.filelogger:
                    self.logger["best_epoch"] = self.best_test_epoch
                    self.logger["best_test_accuracy"] = self.best_test_accuracy
            if verbose:
                if epoch is None:
                    epoch = 0
                    self.best_test_epoch = 0
                loss, acc = utils.convert_for_print(loss, acc)
                print(
                    "\n%s set Epoch: %2d \t Average loss: %0.4f, Accuracy: %d/%d (%0.1f%%)"
                    % (split, epoch, loss, correct, n_examples, acc)
                )
                print(
                    "Best %s split Performance: Epoch %d - Accuracy: %0.1f%%"
                    % (split, self.best_test_epoch, self.best_test_accuracy)
                )

                if self.args.filelogger:
                    self.logger["test_loss"].append([epoch, loss])
                    self.logger["test_accuracy"].append([epoch, acc])
                if self.args.tensorboard:
                    self.writer.add_scalar("Loss_at_Epoch/Test", loss, epoch)
                    self.writer.add_scalar("Accuracy_at_Epoch/Test", acc, epoch)
                    self.writer.add_scalar(
                        "Accuracy_at_Epoch/Best_Test_Accuracy",
                        self.best_test_accuracy,
                        self.best_test_epoch,
                    )

        return loss, acc

    def generate_labels_for_ssl(self, epoch, n_batches=None, verbose=False):

        self.model.eval()
        with torch.no_grad():
            loss = 0
            correct = 0
            n_examples = 0

            predictions_indices = []
            predictions_labels = []

            loader = self.dataloader.unsupervised_train_loader

            for batch_idx, batch in enumerate(loader):
                images, targets, indices = batch
                if args.cuda:
                    images, targets = images.cuda(), targets.cuda()

                logits, unnormalised_scores = self.model(images)
                loss += F.cross_entropy(unnormalised_scores, targets, reduction="sum")
                pred = logits.max(1, keepdim=False)[1]
                correct += pred.eq(targets).sum()
                n_examples += pred.shape[0]

                predictions_indices.extend(indices.tolist())
                predictions_labels.extend(pred.tolist())

                if n_batches and (batch_idx >= n_batches):
                    break

            loss /= n_examples
            acc = 100.0 * correct / n_examples

            if verbose:
                loss, acc, correct = utils.convert_for_print(loss, acc, correct)
                print(
                    "\nLabel Generation Performance Average loss: %0.4f, Accuracy: %d/%d (%0.1f%%)"
                    % (loss, correct, n_examples, acc)
                )

                # TODO: Add logging
                if self.args.filelogger:
                    self.logger["ssl_loss"].append([epoch, loss])
                    self.logger["ssl_accuracy"].append([epoch, acc])
                    self.logger["ssl_correct"].append([epoch, correct])
                if self.args.tensorboard:
                    self.writer.add_scalar("SSL_Loss_at_Epoch", loss, epoch)
                    self.writer.add_scalar("SSL_Accuracy_at_Epoch", acc, epoch)
                    self.writer.add_scalar(
                        "SSL_Correct_Labels_at_Epoch", correct, epoch
                    )

        return predictions_indices, predictions_labels

    def train_val_test(self):
        """ Function to train, validate and evaluate the model"""
        self.iter = 0
        for epoch in range(1, self.args.epochs + 1):
            self.train_val(epoch)
            self.evaluate("Test", epoch, verbose=True)
            if self.args.lr_scheduler:
                self.lr_scheduler.step()
            if epoch % self.args.checkpoint_save_interval == 0:
                print(
                    "Saved %s/%s_epoch%d.pt\n"
                    % (self.args.logdir, self.args.exp_name, epoch)
                )
                torch.save(
                    self.model.state_dict(),
                    "%s/%s_epoch%d.pt" % (self.args.logdir, self.args.exp_name, epoch),
                )
            self.epoch_timer.tic(verbose=True)

        if self.args.tensorboard:
            if self.args.filelogger:
                text = "Epoch: %d Test Accuracy:%0.1f" % (
                    self.logger["best_epoch"],
                    self.logger["best_test_accuracy"],
                )
                self.writer.add_text("Best Test Performance", text)
            self.writer.close()
        if self.args.filelogger:
            utils.write_log_to_json(self.logger_path, self.logger)
        self.epoch_timer.time_since_init(print_str="Total")

    def ssl_train_val_test(self):
        """ Function to train, validate and evaluate the model"""
        self.iter = 0
        predictions_indices, predictions_labels = [], []
        self.dataloader.stop_label_generation = False

        for epoch in range(1, self.args.epochs + 1):
            if not self.dataloader.stop_label_generation:
                self.dataloader.ssl_init_epoch(predictions_indices, predictions_labels)

            self.train_val(epoch)
            self.evaluate("Test", epoch, verbose=True)

            if not self.dataloader.stop_label_generation:
                predictions_indices, predictions_labels = self.generate_labels_for_ssl(
                    epoch, n_batches=4, verbose=True
                )

            if self.args.lr_scheduler:
                self.lr_scheduler.step()
            if epoch % self.args.checkpoint_save_interval == 0:
                print(
                    "Saved %s/%s_epoch%d.pt\n"
                    % (self.args.logdir, self.args.exp_name, epoch)
                )
                torch.save(
                    self.model.state_dict(),
                    "%s/%s_epoch%d.pt" % (self.args.logdir, self.args.exp_name, epoch),
                )
            self.epoch_timer.tic(verbose=True)

        if self.args.tensorboard:
            if self.args.filelogger:
                text = "Epoch: %d Test Accuracy:%0.1f" % (
                    self.logger["best_epoch"],
                    self.logger["best_test_accuracy"],
                )
                self.writer.add_text("Best Test Performance", text)
            self.writer.close()
        if self.args.filelogger:
            utils.write_log_to_json(self.logger_path, self.logger)
        self.epoch_timer.time_since_init(print_str="Total")

    def gmm_train_val_test(self):
        train_data = self.dataloader.full_supervised_train_dataset.train_data
        train_labels = self.dataloader.full_supervised_train_dataset.train_labels

        train_data = train_data / 255

        mean = np.array(self.args.cifar10_mean_color)[np.newaxis][np.newaxis][
            np.newaxis
        ]
        std = np.array(self.args.cifar10_std_color)[np.newaxis][np.newaxis][np.newaxis]

        train_data = (train_data - mean) / std

        train_data = train_data.reshape(train_data.shape[0], -1)

        cv_types = ["spherical", "diag", "full", "tied"]
        for cv_type in cv_types:
            gmm = mixture.GaussianMixture(n_components=10, covariance_type=cv_type)
            gmm.fit(train_data)
            clusters = gmm.predict(train_data)
            labels = np.zeros_like(clusters)
            for i in range(10):
                mask = clusters == i
                labels[mask] = mode(train_labels[mask])[0]

            correct1 = np.equal(clusters, train_labels).sum()
            correct2 = np.equal(labels, train_labels).sum()
            print("%d/49000 (%0.2f%%)" % (correct1, correct1 / 49000))
            print("%d/49000 (%0.2f%%)" % (correct2, correct2 / 49000))



if __name__ == "__main__":
    args = params.parse_args()
    utils.set_random_seed(args.seed, args.cuda)
    trainer = ModelTrainer(args=args)
    if args.eval is False:
        if args.training_mode == "supervised":
            trainer.train_val_test()
        elif args.training_mode == "semi-supervised":
            trainer.ssl_train_val_test()
        elif args.training_mode == "gmm":
            trainer.gmm_train_val_test()
    if args.eval is True:
        trainer.evaluate("Test", verbose=True)
