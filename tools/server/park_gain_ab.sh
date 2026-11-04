#!/bin/bash
# Is conversation parking on a request's hot path?  A-B-A with --conversation-cache-park-gain.
#
# The engine parks a conversation (writes a whole snapshot of it) when a request needs the room, and the log says how
# long that took: "strata serve: conversation cache: parked 45021 tokens in 351.2 ms".  351 ms is a third of the whole
# fixed cost this ledger has been chasing, and it is written for every turn by default
# (--conversation-cache-park-gain 0): a turn that grew by a few hundred tokens overwrites the snapshot the next turn
# drops anyway.  The knob parks only when the conversation advanced N tokens past the copy it holds
# (src/program/generate.cpp:12740-12750), so set it and a normal turn skips the write entirely - if the cost is on the
# path, time to first token and wall clock per turn should drop by it.
#
# Arms: A (deployed config, gain 0) -> B (gain 4096) -> A2 (restored), one restart each way, and the engine's own
# park lines are counted per arm so "the knob did what it says" is measured, not assumed.
#
# Usage: park_gain_ab.sh [ctx] [sizes] [reps]        defaults: 45000 300,3000 3
# Output: /opt/ai/logs/L032-park-gain-<UTC ts>.log
set -u
S=/opt/ai/src/strata-0.2.2
CFG=$S/strata-022.json
BIN=/opt/ai/bin
LOGS=/opt/ai/logs
TMP=/opt/ai/tmp
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT=$LOGS/L032-park-gain-$TS.log
CTX=${1:-45000}
SIZES=${2:-300,3000}
REPS=${3:-3}
ELOG=$S/strata-022.log

say() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$OUT"; }

arm() {   # one DSH-shaped run, then what the engine said about parking and reads in that window
  local tag=$1 before
  before=$(wc -l < "$ELOG")
  say "--- turn-ttft $tag (engine log was at line $before) ---"
  python3 "$BIN/turn-ttft.py" "$tag" "$SIZES" "$REPS" "$CTX" 300 >>"$OUT" 2>&1
  say "--- parking and reads the engine reported for $tag ---"
  tail -n +"$((before + 1))" "$ELOG" | grep -aE "conversation cache: parked|strata serve: prompt [0-9]+ tokens" \
    | cut -c1-200 | tee -a "$OUT"
  say "parks in this arm: $(tail -n +"$((before + 1))" "$ELOG" | grep -ac 'conversation cache: parked')  |  total parked ms: $(tail -n +"$((before + 1))" "$ELOG" | grep -ao 'parked [0-9]* tokens in [0-9.]* ms' | grep -o 'in [0-9.]* ms' | grep -o '[0-9.]*' | paste -sd+ - | bc 2>/dev/null || echo 0)"
}

say "=== L032: is conversation parking on the hot path? (--conversation-cache-park-gain) ==="
cp -p "$CFG" "$TMP/park-base-$TS.json"
say "config snapshotted -> $TMP/park-base-$TS.json"
say "ARM A: the deployed config (gain 0 = park every turn) is the boot running now"
arm "parkA"

python3 - "$CFG" "$TMP/park-gain-$TS.json" <<'PY' || { say "FAILED to build the park-gain variant"; exit 1; }
import json, sys
cfg = json.load(open(sys.argv[1]))
args = list(cfg["args"])
if "--conversation-cache-park-gain" in args:
    i = args.index("--conversation-cache-park-gain")
    before = args[i + 1]
    args[i + 1] = "4096"
else:
    before = "(default 0)"
    args += ["--conversation-cache-park-gain", "4096"]
cfg["args"] = args
json.dump(cfg, open(sys.argv[2], "w"), indent=1, ensure_ascii=False)
print("variant B: --conversation-cache-park-gain %s -> 4096 (%d args)" % (before, len(args)))
PY
say "--- restart with --conversation-cache-park-gain 4096 ---"
"$BIN/use-config.sh" "$TMP/park-gain-$TS.json" >>"$OUT" 2>&1 \
  || { say "FAILED to install variant B - restoring"; "$BIN/use-config.sh" "$TMP/park-base-$TS.json" >>"$OUT" 2>&1; exit 1; }
arm "parkB"

say "--- restore the deployed config and re-run, so drift is measured ---"
"$BIN/use-config.sh" "$TMP/park-base-$TS.json" >>"$OUT" 2>&1 || { say "FAILED to restore"; exit 1; }
cmp -s "$CFG" "$TMP/park-base-$TS.json" && say "config is byte-identical to the snapshot" || say "WARNING: config differs from the snapshot"
arm "parkA2"
say "=== done ==="
