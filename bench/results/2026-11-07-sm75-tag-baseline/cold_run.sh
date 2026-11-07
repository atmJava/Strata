#!/bin/bash
set -u
OUT=/opt/ai/logs/sm75-baseline-20261107
python3 /tmp/cold-prefill-18080.py cold-32k  32768  2 2>&1 | tee "$OUT/cold-32k.txt"
python3 /tmp/cold-prefill-18080.py cold-64k  65536  2 2>&1 | tee "$OUT/cold-64k.txt"
python3 /tmp/cold-prefill-18080.py cold-128k 131072 2 2>&1 | tee "$OUT/cold-128k.txt"
python3 /tmp/cold-prefill-18080.py cold-150k 151552 2 2>&1 | tee "$OUT/cold-150k.txt"
echo COLD_DONE
