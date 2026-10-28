#!/bin/bash
# usage: run-arm.sh <label> [context-lines]
# One arm of a same-boot A/B: what the config says, what the process actually has, the speed bench,
# the bitwise gate, and a cold prefill.  Writes /opt/ai/logs/arm-<label>.log.
#
# The bitwise step used to call /tmp/parity.py.  /tmp gets cleared on this box, and when the file was
# gone this script still ran the arm to the end and left a log that looked complete: a correctness
# gate that silently stops running is worse than one that fails loudly.  It now calls the versioned
# /opt/ai/bin/parity_window.py and refuses to run an arm without it.
#
# The engine environment is printed from /proc/<pid>/environ rather than from the config, because a
# config that was edited but not restarted looks exactly like a config that took effect otherwise.
set -u
LABEL=${1:?label}
LINES=${2:-200}
CFG=/opt/ai/src/strata-0.2.2/strata-022.json
LOG=/opt/ai/logs/arm-$LABEL.log
BIN=/opt/ai/bin
for f in parity_window.py bench-strata.py cold-prefill.py; do
  [ -f "$BIN/$f" ] || { echo "missing $BIN/$f - this arm cannot be completed"; exit 1; }
done
KEY=$(python3 -c "import json;print(json.load(open('$CFG')).get('api_key',''))")
[ -n "$KEY" ] || { echo "no api_key in $CFG"; exit 1; }
{
  echo "===== arm $LABEL   $(date -u +%FT%TZ) ====="
  echo "--- config env ---"
  python3 -c "import json;c=json.load(open('$CFG'));print(json.dumps(c.get('env'),indent=1,sort_keys=True))"
  echo "--- engine env (actual process) ---"
  E=$(pgrep -f '[s]trata --serve' | head -1)
  if [ -n "${E:-}" ]; then
    tr '\0' '\n' < "/proc/$E/environ" | grep -E '^STRATA_' | sort
    echo "engine pid $E"
  else
    echo "NO ENGINE PROCESS - nothing to measure against"
  fi
  echo "--- bench (single + 4 concurrent, 3 reps) ---"
  STRATA_API_KEY="$KEY" python3 "$BIN/bench-strata.py" "$LABEL" 3
  echo "--- bitwise gate (answer only, then with thinking) ---"
  python3 "$BIN/parity_window.py" "$LABEL" "$LINES" 512
  PARITY_EFFORT=high python3 "$BIN/parity_window.py" "$LABEL-withthinking" "$LINES" 512
  echo "--- cold prefill 13.2K ---"
  python3 "$BIN/cold-prefill.py" "$LABEL" 13252 3
  echo "===== done $(date -u +%FT%TZ) ====="
} 2>&1 | tee "$LOG"
