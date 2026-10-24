#!/bin/bash
# Where does a small read's fixed ~1 s go?  STRATA_TRACE=1 says so per admission.
#
# turn-ttft.py (the DSH-shaped probe) measured that a re-read of 460 tokens costs the engine ~1.1 s while a
# re-read of 4535 tokens costs ~4.1 s: the line is roughly 0.75 s + 1330 tok/s, so the fixed part is most of a
# normal turn's time-to-first-token.  The engine can already break that number apart - with STRATA_TRACE=1 it
# prints, per admission (src/program/generate.cpp:15470-15477):
#
#   strata trace: admission of slot N: drain, prompt (read, windows, checkpoints = sync + save + evict, rest),
#                 first window, slot copy, slot freed, checkpoint copy
#
# plus "the request first waited N ms for the adaptive tier's round in flight" (15502), which is printed anyway.
#
# Arms: A (no trace) was the run just done in the previous boot; this script runs B (trace on) and then restores
# the config and re-runs A2, so boot-to-boot drift is visible instead of assumed.  A and B are different boots by
# necessity - trace is an engine environment variable - which the log says out loud.
#
# Usage: trace_admission_phases.sh [ctx] [turn-ttft tag suffix]      defaults: 45000 (no suffix)
# Output: /opt/ai/logs/L027-trace-<UTC ts>.log
set -u
S=/opt/ai/src/strata-0.2.2
CFG=$S/strata-022.json
BIN=/opt/ai/bin
LOGS=/opt/ai/logs
TMP=/opt/ai/tmp
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT=$LOGS/L027-trace-$TS.log
CTX=${1:-45000}
SUF=${2:-}
ELOG=$S/strata-022.log

say() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$OUT"; }

turns() {   # one DSH-shaped run, then the engine's own phase lines for it
  local tag=$1 before
  before=$(wc -l < "$ELOG")            # engine lines carry no timestamp, so window by log offset
  say "--- turn-ttft $tag (engine log was at line $before) ---"
  python3 "$BIN/turn-ttft.py" "$tag" 300,1200,3000 3 "$CTX" 300 >>"$OUT" 2>&1
  say "--- what the engine said about those admissions ---"
  tail -n +"$((before + 1))" "$ELOG" \
    | grep -aE "strata trace: admission|adaptive tier|strata serve: prompt [0-9]+ tokens" \
    | cut -c1-330 | tee -a "$OUT"
}

say "=== L027: decompose the fixed cost of a read (STRATA_TRACE=1) ==="
say "arm A (no trace) was measured in the previous boot; B below is a new boot, A2 after the restore brackets the drift"
cp -p "$CFG" "$TMP/trace-off-$TS.json"
say "config snapshotted -> $TMP/trace-off-$TS.json"

python3 - "$CFG" "$TMP/trace-on-$TS.json" <<'PY' || { say "FAILED to build the trace variant"; exit 1; }
import json, sys
cfg = json.load(open(sys.argv[1]))
before = cfg["env"].get("STRATA_TRACE")
cfg["env"]["STRATA_TRACE"] = "1"
json.dump(cfg, open(sys.argv[2], "w"), indent=1, ensure_ascii=False)
print("trace variant: STRATA_TRACE was %r -> 1; args untouched (%d strings)" % (before, len(cfg["args"])))
PY

say "--- restart with STRATA_TRACE=1 ---"
"$BIN/use-config.sh" "$TMP/trace-on-$TS.json" >>"$OUT" 2>&1 || { say "FAILED to install the trace variant"; exit 1; }
turns "trace$SUF"

say "--- restore (trace off) and re-run, so drift is measured rather than assumed ---"
"$BIN/use-config.sh" "$TMP/trace-off-$TS.json" >>"$OUT" 2>&1 || { say "FAILED to restore"; exit 1; }
cmp -s "$CFG" "$TMP/trace-off-$TS.json" && say "config is byte-identical to the snapshot" || say "WARNING: config differs from the snapshot"
turns "restored$SUF"
say "--- how many trace lines the trace arm printed (log volume) ---"
grep -ac "strata trace: admission" "$ELOG" | tee -a "$OUT"
say "=== done ==="
