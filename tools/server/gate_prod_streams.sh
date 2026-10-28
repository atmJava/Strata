#!/bin/bash
# Does the concurrent-read gate still pay off when MORE THAN ONE stream is decoding beside the cold read?
#
# Why this exists: L-019 measured the trade with exactly two streams (one decoding, one reading), but the
# production config runs `parallel: 4`.  The decision "leave the gate open" should not rest on a shape the
# box is rarely in.  This script runs the same scenario with N decode streams and one cold reader, open
# against closed.
#
# Two arms, not three: the open baseline is already known to be stable across boots for this measurement -
# L-019 ran it on two different open boots and got 14.4 / 14.4 / 14.2 / 14.3 s walls and 43-49 chars/s -
# so a third restart would buy nothing here.  A one-rep open confirmation runs at the end anyway, after the
# restore, to prove the box came back the way it went in.
#
# Usage: gate_prod_streams.sh [peers] [reps] [chars]        defaults: 3 3 60000
# Output: /opt/ai/logs/L020-gate-streams-<UTC ts>.log
set -u
S=/opt/ai/src/strata-0.2.2
CFG=$S/strata-022.json
ENGINE_LOG=$S/strata-022.log
BIN=/opt/ai/bin
LOGS=/opt/ai/logs
TMP=/opt/ai/tmp
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT=$LOGS/L020-gate-streams-$TS.log
PEERS=${1:-3}
REPS=${2:-3}
CHARS=${3:-60000}

say() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$OUT"; }

snapshot() {
  local pid gate
  pid=$(pgrep -f "[s]trata --serve" | head -1)
  gate=$(tr '\0' '\n' < "/proc/${pid:-1}/environ" 2>/dev/null | grep '^STRATA_PIPED_CONCURRENT' || echo MISSING)
  say "engine pid=${pid:-none}  $gate  live config sha=$(sha256sum < "$CFG" | cut -c1-12)"
}

measure() {
  local label=$1 since=$2
  say "=== arm $label ($PEERS 条解码流 + 1 条冷读), log windows filtered at $since ==="
  snapshot
  say "--- control: $PEERS 条流各自 solo ---"
  "$BIN/piped_scenario.py" "$REPS" "$CHARS" solo "$PEERS" >>"$OUT" 2>&1
  say "--- coexistence: $PEERS 条流在解码, B 读 $CHARS 字符冷提示词 ---"
  "$BIN/piped_scenario.py" "$REPS" "$CHARS" coexist "$PEERS" >>"$OUT" 2>&1
  say "--- engine side: concurrent reads in this window ---"
  "$BIN/concurrent_reads.py" "$ENGINE_LOG" --since "$since" >>"$OUT" 2>&1
  say "=== arm $label done ==="
}

say "=== L020: gate open vs closed with $PEERS decode streams (reps=$REPS chars=$CHARS) ==="
cp -p "$CFG" "$TMP/gate-streams-on-$TS.json"
say "production config snapshotted -> $TMP/gate-streams-on-$TS.json"

measure open-now "$(date -u +"%Y-%m-%d %H:%M:%S")"

python3 - "$CFG" "$TMP/gate-streams-off-$TS.json" <<'PY' || { say "FAILED to build the OFF variant"; exit 1; }
import json, sys
cfg = json.load(open(sys.argv[1]))
before = cfg["env"].get("STRATA_PIPED_CONCURRENT")
cfg["env"]["STRATA_PIPED_CONCURRENT"] = "0"
json.dump(cfg, open(sys.argv[2], "w"), indent=1, ensure_ascii=False)
print("OFF variant: env was %r -> '0'; args untouched (%d strings)" % (before, len(cfg["args"])))
PY

say "--- restart into the closed arm ---"
PRE=$(date -u +"%Y-%m-%d %H:%M:%S")
"$BIN/use-config.sh" "$TMP/gate-streams-off-$TS.json" >>"$OUT" 2>&1 || { say "FAILED to install the closed arm"; exit 1; }
measure closed "$PRE"

say "--- restart back to the snapshot ---"
PRE2=$(date -u +"%Y-%m-%d %H:%M:%S")
"$BIN/use-config.sh" "$TMP/gate-streams-on-$TS.json" >>"$OUT" 2>&1 || { say "FAILED to restore production"; exit 1; }
say "--- one rep of the open shape after restore (proof the box came back) ---"
"$BIN/piped_scenario.py" 1 "$CHARS" coexist "$PEERS" >>"$OUT" 2>&1
"$BIN/concurrent_reads.py" "$ENGINE_LOG" --since "$PRE2" >>"$OUT" 2>&1

say "=== finished ==="
snapshot
cmp -s "$CFG" "$TMP/gate-streams-on-$TS.json" && say "live config byte-identical to the snapshot" \
  || say "WARNING: live config differs from the snapshot"
say "=== summary ==="
grep -aE "中位|=== arm|engine pid=|READY after|OFF variant|reads " "$OUT" | tail -30
