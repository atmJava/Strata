#!/bin/bash
set -u
cd /opt/ai/src/strata-v0.2.7-tag
OUT=/opt/ai/logs/sm75-baseline-20261107
export STRATA_KEY=111
{
  echo "date_utc: $(date -u +%FT%TZ)"
  echo "engine_sha256: $(sha256sum build-sm75/strata | cut -d' ' -f1)"
  nvidia-smi --query-gpu=index,name,driver_version,clocks.sm,power.draw,temperature.gpu --format=csv,noheader
} | tee "$OUT/machine.txt"
echo "=== cold reads (calibrated targets) ==="
python3 /tmp/cold-prefill-18080.py cold-32k  23000  2 2>&1 | tee "$OUT/cold-32k.txt"
python3 /tmp/cold-prefill-18080.py cold-64k  46000  2 2>&1 | tee "$OUT/cold-64k.txt"
python3 /tmp/cold-prefill-18080.py cold-128k 92000  2 2>&1 | tee "$OUT/cold-128k.txt"
python3 /tmp/cold-prefill-18080.py cold-150k 108000 2 2>&1 | tee "$OUT/cold-150k.txt"
echo "=== concurrency 1/2/4 ==="
python3 tools/server/probe-decode-concurrency.py --base-url http://127.0.0.1:18080 --config strata-production-tag.json --levels 1,2,4 --waves 3 --max-tokens 256 --output "$OUT/decode-concurrency.json" 2>&1 | tee "$OUT/decode-concurrency.txt"
echo "=== long compaction ==="
python3 tools/server/probe-long-compaction.py --base-url http://127.0.0.1:18080 --config strata-production-tag.json --history-words 145000 --output "$OUT/long-compaction.json" 2>&1 | tee "$OUT/long-compaction.txt"
echo "=== turn-ttft (DSH shape) ==="
TTFT_LOG=/opt/ai/logs/strata-engine-tag.log TTFT_CFG=/opt/ai/src/strata-v0.2.7-tag/strata-production-tag.json python3 /tmp/turn-ttft-18080.py run 300,1200,3000 3 45000 300 2>&1 | tee "$OUT/turn-ttft.txt"
echo "=== BASELINE DONE ==="
