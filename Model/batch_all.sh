#!/bin/bash

#You may need to do chmod 705 "filename" if it says Permission denied.
#Usage: RUN_NUM=1 ./batch_all.sh 
#does a run of long/short/baseline titled (run_type)_1

RUN_NUM="${RUN_NUM:-1}"
echo $RUN_NUM

OUTPUT_DIR=../Analysis/simulation_results/baseline/baseline_$RUN_NUM sbatch batch_baseline.sh
OUTPUT_DIR=../Analysis/simulation_results/short/short_$RUN_NUM sbatch batch_short.sh
OUTPUT_DIR=../Analysis/simulation_results/long/long_$RUN_NUM sbatch batch_long.sh