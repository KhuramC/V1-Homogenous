#!/bin/bash

#You may need to do chmod 705 "filename" if it says Permission denied.
# Usage: START=1 RUNS=10 ./batch_run_multiple.sh 
# Will kick off 10 runs for each starting at #1.

START="${START:-1}"

RUNS="${RUNS:-1}"

for ((i = $START ; i < $RUNS+$START ; i++)); do
	RUN_NUM=$i ./batch_all.sh
done
