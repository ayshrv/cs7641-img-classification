#!/bin/sh

source activate img-classifcation

EXP_NAME=threelayernn-49k

flags="--model threelayernn \
       --train-data-size 49000 \
       --batch-size 64 \
       --epochs 80 \
       --data-aug \
       --optimiser adam \
       --learning-rate 0.001 \
       --lr-reducer \
       --weight-decay 5e-4 \
       --exp-name ${EXP_NAME} \
       --tensorboard \
       --filelogger "

unbuffer python train.py $flags | tee checkpoints/${EXP_NAME}.log
