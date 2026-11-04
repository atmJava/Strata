#!/bin/bash
# Split the fixed ~0.8 s of a small prompt read into GPU phases.  STRATA_PREFILL_TIMING=1 already does this.
#
# L-028 put the whole fixed cost inside Prefill::run (the batched blob read): a 461-token re-read is 977 ms of
# `read (batched)` against 74 ms of expert refill, 42 ms of windows and 10 ms of checkpoints.  L-029 showed that
# re-reading the same tokens is no cheaper, so it is not "stage the experts these tokens route to".  The engine has
# a facility for exactly this question (src/prefill/prefill.cpp:2500-2543): with STRATA_PREFILL_TIMING=1 it charges
# the GPU timeline between ordered CUDA events to 21 named phases - embed+steps, gdn, qsa proj/indexer/select/attn,
# router+shared, host grouping, gather, wait copy, dequant, gemm gate/up, gemm down, combine, ple, kv stage, ... -
# and prints the host's share separately (prefill.cpp:5056-5060): chunk setup (PLE rows, the expert stream plan),
# waiting for each chunk, after each chunk, PLE.
#
# Arms: B (timing on) then the config restored and A2 (timing off), so the cost of the events is measured and not
# assumed.  Both arms are new boots (it is an engine environment variable); turn-ttft's own numbers carry that.
#
# Usage: prefill_phase_timing.sh [ctx] [small,large block sizes] [reps]     defaults: 45000 300,3000 3
# Output: /opt/ai/logs/L030-prefill-timing-<UTC ts>.log
set -u
S=/opt/ai/src/strata-0.2.2
CFG=$S/strata-022.json
BIN=/opt/ai/bin
LOGS=/opt/ai/logs
TMP=/opt/ai/tmp
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT=$LOGS/L030-prefill-timing-$TS.log
CTX=${1:-45000}
SIZES=${2:-300,3000}
REPS=${3:-3}
ELOG=$S/strata-022.log

say() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$OUT"; }

turns() {   # one DSH-shaped run, then the engine's own phase lines for it
  local tag=$1 before
  before=$(wc -l < "$ELOG")            # engine lines carry no timestamp, so window by log offset
  say "--- turn-ttft $tag (engine log was at line $before) ---"
  python3 "$BIN/turn-ttft.py" "$tag" "$SIZES" "$REPS" "$CTX" 300 >>"$OUT" 2>&1
  say "--- what the engine said about those reads ---"
  tail -n +"$((before + 1))" "$ELOG" \
    | grep -aE "strata prefill timing|strata serve: prompt [0-9]+ tokens" \
    | cut -c1-460 | tee -a "$OUT"
}

say "=== L030: split the fixed cost of a read into GPU phases (STRATA_PREFILL_TIMING=1) ==="
cp -p "$CFG" "$TMP/timing-off-$TS.json"
say "config snapshotted -> $TMP/timing-off-$TS.json"

python3 - "$CFG" "$TMP/timing-on-$TS.json" <<'PY' || { say "FAILED to build the timing variant"; exit 1; }
import json, sys
cfg = json.load(open(sys.argv[1]))
before = cfg["env"].get("STRATA_PREFILL_TIMING")
cfg["env"]["STRATA_PREFILL_TIMING"] = "1"
json.dump(cfg, open(sys.argv[2], "w"), indent=1, ensure_ascii=False)
print("timing variant: STRATA_PREFILL_TIMING was %r -> 1; args untouched (%d strings)" % (before, len(cfg["args"])))
PY

say "--- restart with STRATA_PREFILL_TIMING=1 ---"
"$BIN/use-config.sh" "$TMP/timing-on-$TS.json" >>"$OUT" 2>&1 || { say "FAILED to install the timing variant"; exit 1; }
turns "timing"

say "--- restore (timing off) and re-run, so the cost of the events is measured ---"
"$BIN/use-config.sh" "$TMP/timing-off-$TS.json" >>"$OUT" 2>&1 || { say "FAILED to restore"; exit 1; }
cmp -s "$CFG" "$TMP/timing-off-$TS.json" && say "config is byte-identical to the snapshot" || say "WARNING: config differs from the snapshot"
turns "plain"
say "=== done ==="
