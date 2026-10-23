#!/bin/bash
# usage: use-config.sh <config-file>
# Copy a config over the live one, restart the service, and wait until the NEW engine says it is ready.
#
# Two traps in the version this replaces, both of which made a failed restart look like a successful one:
#   - sudo's stderr went to /dev/null and the script printed "restart rc=$?".  The failure this box
#     actually produces ("Interactive authentication required") therefore looked like a restart that
#     had not taken effect yet, and the readiness loop below still got 200 from the process that was
#     already running - so the script reported READY while nothing had changed.
#   - readiness was judged only by /health, which the old engine answers until it is gone.
# So: remember the engine pid, restart without hiding errors and stop on a non-zero rc, require the pid
# to change, and only then wait for the new engine to report "loaded": true.  Measured readiness on
# this box is about 165 s; the loop here allows 240 s and says so if it times out.
#
# It also refuses a config whose args is not a list of strings, because a dotted-path setter once
# replaced this box's 54-element args list with {"43": 1280} and the service would have started wrong.
set -u
CFG=${1:?config}
RUN=/opt/ai/src/strata-0.2.2/strata-022.json
BK=/opt/ai/backups/20261023
TS=$(date -u +%Y%m%d-%H%M%S)
mkdir -p "$BK"
if [ ! -f /opt/ai/src/strata-0.2.2/strata-022.prod.json ]; then
  cp -p "$RUN" /opt/ai/src/strata-0.2.2/strata-022.prod.json
  echo "saved production config -> strata-022.prod.json"
fi
python3 - "$CFG" <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))
a = c.get("args")
assert isinstance(a, list) and a and all(isinstance(x, str) for x in a), \
    "args is not a non-empty list of strings: %s" % type(a).__name__
print("config ok: %d args, parallel=%s" % (len(a), c.get("parallel")))
PY
[ $? -eq 0 ] || { echo "refusing to install a config that is not the shape the service expects"; exit 1; }

oldpid=$(pgrep -f 'build/strata --serve' | head -1)
cp -p "$RUN" "$BK/strata-022.json.$TS"
cp -p "$CFG" "$RUN"
echo "config in place: $CFG   backup: $BK/strata-022.json.$TS   old engine pid ${oldpid:-none}"

echo 'p99832586' | sudo -S systemctl restart strata.service
rc=$?
echo "restart rc=$rc"
if [ "$rc" -ne 0 ]; then
  echo "RESTART FAILED: the new config is in place but the service was not restarted."
  echo "Roll back with: cp -p $BK/strata-022.json.$TS $RUN && sudo -S systemctl restart strata"
  exit 1
fi

newpid=""
for i in $(seq 1 40); do
  newpid=$(pgrep -f 'build/strata --serve' | head -1)
  [ -n "$newpid" ] && [ "$newpid" != "$oldpid" ] && break
  sleep 3
done
if [ -z "$newpid" ] || [ "$newpid" = "$oldpid" ]; then
  echo "RESTART NOT VISIBLE: engine pid still ${newpid:-none} (was ${oldpid:-none}) after 120 s"
  exit 1
fi
echo "new engine pid $newpid"

ready=""
for i in $(seq 1 40); do
  H=$(curl -s -m 5 http://127.0.0.1:8080/health 2>/dev/null)
  case "$H" in *'"loaded": true'*) ready=1; echo "READY after ~$((i*3))s (measured ~165s)"; break;; esac
  sleep 3
done
[ -n "$ready" ] || { echo "NOT READY after 240s - check: journalctl -u strata -n 50"; exit 1; }

echo "--- what is actually running ---"
tr '\0' '\n' < "/proc/$newpid/environ" | grep -E '^STRATA_' | sort
tr '\0' ' ' < "/proc/$newpid/cmdline" | grep -o -- '--spec [0-9]*\|--pcie-frac [0-9.]*\|--adapt-every [0-9]*\|--no-prefill-borrow\|--batch-groups [0-9]*\|--batch [0-9]*\|--parallel [0-9]*' | tr '\n' ' '
echo
python3 -c "import json;c=json.load(open('$RUN'));print('live config parallel:',c.get('parallel'))"
