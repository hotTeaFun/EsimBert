import sys
sys.path.append("/home/y24/毕设/EsimBert/")
from functools import partial
from util.data import pack_data
from util.path import get_root_path
from torch.utils.data import DataLoader
import torch
from model.dataset.snli_dataset import SNLIDataset
from transformers import AdamW, get_linear_schedule_with_warmup
from scripts.train.utils import collate_to_max_length, train, validate
from torch import nn
import os
from model.esim_bert import ExplainableModel
import argparse
import json



def main(target_dir,
         dropout=0.5,
         num_classes=3,
         epochs=64,
         batch_size=32,
         lr=0.0004,
         patience=5,
         max_grad_norm=10.0,
         checkpoint=None,
         adam_epsilon=1e-9,
         warmup_steps=0,
         weight_decay=0.0,
         max_epochs=32,
         accumulate_grad_batches=2,):
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    train_loader = DataLoader(SNLIDataset(
        "train"), shuffle=True, batch_size=batch_size, collate_fn=partial(collate_to_max_length, fill_values=[1, 0, 0]))

    valid_loader = DataLoader(SNLIDataset(
        "dev"), shuffle=False, batch_size=batch_size, collate_fn=partial(collate_to_max_length, fill_values=[1, 0, 0]))
    model = ExplainableModel(
        "roberta-base", num_labels=num_classes).to(device)
    criterion = nn.CrossEntropyLoss()
    """  optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer,
                                                           mode="max",
                                                           factor=0.5,
                                                           patience=0) """
    no_decay = ["bias", "LayerNorm.weight"]
    optimizer_grouped_parameters = [
        {
            "params": [p for n, p in model.named_parameters() if not any(nd in n for nd in no_decay)],
            "weight_decay": weight_decay,
        },
        {
            "params": [p for n, p in model.named_parameters() if any(nd in n for nd in no_decay)],
            "weight_decay": 0.0,
        },
    ]
    optimizer = AdamW(optimizer_grouped_parameters,
                      betas=(0.9, 0.98),  # according to RoBERTa paper
                      lr=lr,
                      eps=adam_epsilon)
    t_total = len(train_loader) // accumulate_grad_batches * max_epochs
    scheduler = get_linear_schedule_with_warmup(optimizer, num_warmup_steps=warmup_steps,
                                                num_training_steps=t_total)

    best_score = 0.0
    start_epoch = 1

    epochs_count, train_losses, valid_losses = [], [], []
    # Continuing training from a checkpoint if one was given as argument.
    if checkpoint:
        checkpoint = torch.load(checkpoint)
        start_epoch = checkpoint["epoch"] + 1
        best_score = checkpoint["best_score"]

        print("\tTraining will continue on existing model from epoch {}..."
              .format(start_epoch))

        model.load_state_dict(checkpoint["model"])
        optimizer.load_state_dict(checkpoint["optimizer"])
        epochs_count = checkpoint["epochs_count"]
        train_losses = checkpoint["train_losses"]
        valid_losses = checkpoint["valid_losses"]
    # Compute loss and accuracy before starting (or resuming) training.
    _, valid_loss, valid_accuracy = validate(model,
                                             valid_loader,
                                             criterion)
    print("\tValidation loss before training: {:.4f}, accuracy: {:.4f}%"
          .format(valid_loss, (valid_accuracy*100)))
    # -------------------- Training epochs ------------------- #
    print("\n",
          20 * "=",
          "Training Esim_BERT model on device: {}".format(device),
          20 * "=")

    patience_counter = 0
    for epoch in range(start_epoch, epochs+1):
        epochs_count.append(epoch)

        print("Training epoch {}:".format(epoch))
        epoch_time, epoch_loss, epoch_accuracy = train(model,
                                                       train_loader,
                                                       optimizer,
                                                       criterion,
                                                       epoch,
                                                       max_grad_norm)

        train_losses.append(epoch_loss)
        print("-> Training time: {:.4f}s, loss = {:.4f}, accuracy: {:.4f}%"
              .format(epoch_time, epoch_loss, (epoch_accuracy*100)))

        print("Validation for epoch {}:".format(epoch))
        epoch_time, epoch_loss, epoch_accuracy = validate(model,
                                                          valid_loader,
                                                          criterion)

        valid_losses.append(epoch_loss)
        print("-> Valid. time: {:.4f}s, loss: {:.4f}, accuracy: {:.4f}%\n"
              .format(epoch_time, epoch_loss, (epoch_accuracy*100)))
        scheduler.step(epoch_accuracy)
        # Early stopping on validation accuracy.
        if epoch_accuracy < best_score:
            patience_counter += 1
        else:
            best_score = epoch_accuracy
            patience_counter = 0
            # Save the best model. The optimizer is not saved to avoid having
            # a checkpoint file that is too heavy to be shared. To resume
            # training from the best model, use the 'esim_*.pth.tar'
            # checkpoints instead.
            torch.save({"epoch": epoch,
                        "model": model.state_dict(),
                        "best_score": best_score,
                        "epochs_count": epochs_count,
                        "train_losses": train_losses,
                        "valid_losses": valid_losses},
                       os.path.join(target_dir, "best.pth.tar"))

        # Save the model at each epoch.
        torch.save({"epoch": epoch,
                    "model": model.state_dict(),
                    "best_score": best_score,
                    "optimizer": optimizer.state_dict(),
                    "epochs_count": epochs_count,
                    "train_losses": train_losses,
                    "valid_losses": valid_losses},
                   os.path.join(target_dir, "esim_bert_newest.pth.tar"))

        if patience_counter >= patience:
            print("-> Early stopping: patience limit reached, stopping...")
            break


if __name__ == "__main__":
    default_config = "config/train/snli.json"

    parser = argparse.ArgumentParser(
        description="Train the ESIM model on SNLI")
    parser.add_argument("--config",
                        default=default_config,
                        help="Path to a json configuration file")
    parser.add_argument("--checkpoint",
                        default=None,
                        help="Path to a checkpoint file to resume training")
    args = parser.parse_args()

    root_dir = get_root_path()

    checkpoint = os.path.join(
        root_dir, args.checkpoint) if args.checkpoint else None
    with open(os.path.join(get_root_path(), args.config), 'r') as config_file:
        config = json.load(config_file)

    main(os.path.join(get_root_path(), config["target_dir"]),
         config["dropout"],
         config["num_classes"],
         config["epochs"],
         config["batch_size"],
         config["lr"],
         config["patience"],
         config["max_gradient_norm"],
         checkpoint)
