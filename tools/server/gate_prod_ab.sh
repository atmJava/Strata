#!/bin/bash
# Production-shape A-B-A of the concurrent prompt read (STRATA_PIPED_CONCURRENT).
#
# Why no recompile: the two source lines that make it possible are already in the running engine
# binary (L-013 proved that by reverse-patching and comparing hashes), and the gate is toggled by an
# environment variable in the config's env key, so an arm is a config swap plus a restart.
#
# Arms: ON first (the boot we are already in - measured without touching the service), then OFF
# (restart with STRATA_PIPED_CONCURRENT=0), then ON again (restart with the config file that was live
# when this script started, snapshotted below so the restore is byte-identical).  Two restarts,
# ~165 s of service downtime each on a cold load; a restart right after a restart is faster because
# the model is still in the page cache.  A1 and A2 are different boots: if they agree, the ON baseline
# is stable across boots and the comparison against B stands; if they disagree, nothing here resolves
# it and the run is a warm-up, not a result.
#
# Why a second stream is built on purpose (L-015): on an otherwise idle box 97% of the concurrent
# reads had nothing to overlap with, while with another stream decoding beside them that fell to 46%
# and the slots advanced a median of 57 rows per read.  Switching the gate while the box is idle
# measures the idle box, not the gate.  So each arm runs the coexistence scenario (one stream decoding
# while another reads a cold prompt) and the four-way concurrent bench before anything is read off
# the logs.
#
# Usage: gate_prod_ab.sh [cold-chars] [scenario-reps] [bench-reps]     defaults: 60000 2 3
# Everything is appended to /opt/ai/logs/L018-gate-ab-<UTC ts>.log.
set -u
S=/opt/ai/src/strata-0.2.2
CFG=$S/strata-022.json
ENGINE_LOG=$S/strata-022.log
BIN=/opt/ai/bin
LOGS=/opt/ai/logs
TMP=/opt/ai/tmp
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT=$LOGS/L018-gate-ab-$TS.log
CHARS=${1:-60000}
SREP=${2:-2}
BREP=${3:-3}

say() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$OUT"; }

snapshot() {
  local pid gate
  pid=$(pgrep -f "[s]trata --serve" | head -1)
  gate=$(tr '\0' '\n' < "/proc/${pid:-1}/environ" 2>/dev/null | grep '^STRATA_PIPED_CONCURRENT' || echo MISSING)
  say "engine pid=${pid:-none}  $gate  live config sha=$(sha256sum < "$CFG" | cut -c1-12)"
}

measure() {
  local label=$1 since=$2 t0
  t0=$(date -u +"%Y-%m-%d %H:%M:%S")
  say "=== arm $label: measuring from $t0 (log windows filtered at $since) ==="
  snapshot
  say "--- control: the decode stream alone, no cold read beside it ($SREP reps) ---"
  "$BIN/piped_scenario.py" "$SREP" "$CHARS" solo >>"$OUT" 2>&1
  say "--- coexistence scenario: $SREP reps, cold prompt ~$CHARS chars ---"
  "$BIN/piped_scenario.py" "$SREP" "$CHARS" coexist >>"$OUT" 2>&1
  say "--- bench: $BREP reps ---"
  "$BIN/bench-strata.py" "L018-$label" "$BREP" >>"$OUT" 2>&1
  say "--- what the engine measured during this arm: concurrent reads ---"
  "$BIN/concurrent_reads.py" "$ENGINE_LOG" --since "$since" >>"$OUT" 2>&1
  say "--- what the engine measured during this arm: acceptance / hit / truncation ---"
  "$BIN/passive_windows.py" "$ENGINE_LOG" --since "$since" >>"$OUT" 2>&1
  say "=== arm $label done ==="
}

say "=== L018: production-shape gate A-B-A (chars=$CHARS scenario_reps=$SREP bench_reps=$BREP) ==="
cp -p "$CFG" "$TMP/gate-ab-on-$TS.json"
say "production config snapshotted -> $TMP/gate-ab-on-$TS.json (this exact file is restored at the end)"

measure on-a1 "$(date -u +"%Y-%m-%d %H:%M:%S")"

python3 - "$CFG" "$TMP/gate-ab-off-$TS.json" <<'PY' || { say "FAILED to build the OFF variant"; exit 1; }
import json, sys
cfg = json.load(open(sys.argv[1]))
before = cfg["env"].get("STRATA_PIPED_CONCURRENT")
cfg["env"]["STRATA_PIPED_CONCURRENT"] = "0"
json.dump(cfg, open(sys.argv[2], "w"), indent=1, ensure_ascii=False)
print("OFF variant: env was %r -> '0'; args untouched (%d strings)" % (before, len(cfg["args"])))
PY

say "--- restart into the OFF arm ---"
PRE_OFF=$(date -u +"%Y-%m-%d %H:%M:%S")
"$BIN/use-config.sh" "$TMP/gate-ab-off-$TS.json" >>"$OUT" 2>&1 || { say "FAILED to install the OFF arm"; exit 1; }
measure off-b "$PRE_OFF"

say "--- restart back into the snapshotted production config (ON again) ---"
PRE_ON=$(date -u +"%Y-%m-%d %H:%M:%S")
"$BIN/use-config.sh" "$TMP/gate-ab-on-$TS.json" >>"$OUT" 2>&1 || { say "FAILED to restore production"; exit 1; }
measure on-a2 "$PRE_ON"

say "=== finished; what is running now ==="
snapshot
cmp -s "$CFG" "$TMP/gate-ab-on-$TS.json" && say "live config is byte-identical to the snapshot taken at the start" \
  || say "WARNING: live config differs from the snapshot taken at the start"
say "=== summary lines ==="
grep -aE "中位|投机 draft|refusals|=== arm|engine pid=|READY after|new engine pid|OFF variant" "$OUT" | tail -40
