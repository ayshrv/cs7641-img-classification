#!/bin/bash
#SBATCH --job-name=resnet-4k
#SBATCH --output=sbatch_logs/run-%j-resnet-4k.out
#SBATCH --error=sbatch_logs/run-%j-resnet-4k.err
#SBATCH --gres gpu:1
#SBATCH --nodes 1
#SBATCH --ntasks-per-node 1
#SBATCH --partition=short

set -x
sacct -j ${SLURM_JOB_ID} --format=User%20,JobID,Jobname%40,partition,state,time,start,nodelist
bash scripts/run_resnet-4k.sh