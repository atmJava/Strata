#!/bin/bash
# Verify that the RUNNING engine process was started with exactly the config on disk.
# Reading the config file is not proof: the process may predate the edit.  The authoritative
# sources are /proc/<pid>/cmdline and /proc/<pid>/environ.
#
# Paths and the engine process pattern are overridable, because this box has been re-deployed
# twice and the script kept pointing at whichever tree was current when it was written (it was
# reading /opt/ai/src/strata-0.1.40.1 while production had moved to strata-0.2.2).  Defaults now:
#   CFG=/opt/ai/src/strata-0.2.2/strata-022.json   LOG=/opt/ai/src/strata-0.2.2/strata-022.log
#   ENGINE_PATTERN='build/strata --serve'
# The engine is a CHILD of the python server: systemd's MainPID is the server, and its environ
# holds no STRATA_* at all.  pgrep for the engine binary, not for MainPID.
set -u
S=${S:-/opt/ai/src/strata-0.2.2}
CFG=${CFG:-$S/strata-022.json}
L=${LOG:-$S/strata-022.log}
ENGINE_PATTERN=${ENGINE_PATTERN:-build/strata --serve}
PID=$(pgrep -f "$ENGINE_PATTERN" | head -1)

echo "engine pid    = ${PID:-NONE}"
echo "process start = $(ps -o lstart= -p "$PID" 2>/dev/null)"
echo "config        = $CFG"
echo "config mtime  = $(stat -c %y "$CFG")"
echo "config sha256 = $(sha256sum "$CFG" | cut -d' ' -f1)"
echo
echo "session start line (last):"
grep -a 'engine started' "$L" | tail -1 | cut -c1-120

echo
echo "=== STRATA_* in the RUNNING engine process environment ==="
tr '\0' '\n' < "/proc/$PID/environ" | grep '^STRATA_' | sort

echo
echo "=== cmdline of the RUNNING process vs config args ==="
export CFG ENGINE_PATTERN
tr '\0' '\n' < "/proc/$PID/cmdline" | tail -n +2 | grep -v '^$' > /tmp/run-args.txt
echo "running args count: $(wc -l < /tmp/run-args.txt)"
python3 - <<'PY'
import json, os
cfg = json.load(open(os.environ['CFG']))
args = cfg['args']
run = [l for l in open('/tmp/run-args.txt').read().split('\n') if l]
print('config  args count:', len(args))
if run == args:
    print('ARGS  : MATCH (running cmdline identical to config, same order)')
else:
    sa, sr = set(args), set(run)
    # serve/server.py injects --serve, an auto-computed --layer-split <n>, and --batch <parallel>
    # (verified on this box 2026-10-24: the running engine carries 59 args, the config lists 54, and
    # the three extras are exactly these).  A gate that calls that "DIFFERENT" is a false alarm.
    injected = {'--serve', '--layer-split', '--batch'}
    nums = {x for x in (sr - sa) if x.isdigit()}
    unexpected = (sr - sa) - injected - nums
    missing = sa - sr
    if not missing and not unexpected:
        print('ARGS  : MATCH (all %d config args present; extra %s injected by serve/server.py)'
              % (len(args), sorted(sr - sa)))
    else:
        print('ARGS  : DIFFERENT')
        print('  only in config  :', sorted(missing))
        print('  unexpected extra:', sorted(unexpected))
        if sa == sr:
            print('  (same set, different order)')

pid = os.popen("pgrep -f \"%s\" | head -1" % os.environ['ENGINE_PATTERN']).read().strip()
penv = {}
for entry in open('/proc/%s/environ' % pid, 'rb').read().split(b'\0'):
    if b'=' in entry:
        k, v = entry.split(b'=', 1)
        penv[k.decode()] = v.decode()
cfg_env = cfg.get('env') or {}
bad = []
for k, v in cfg_env.items():
    if penv.get(k) != str(v):
        bad.append('%s: config=%r process=%r' % (k, v, penv.get(k)))
extra = sorted(set(k for k in penv if k.startswith('STRATA_')) - set(cfg_env))
print('ENV   :', 'MATCH' if not bad and not extra else 'DIFFERENT')
for b in bad:
    print('  mismatch:', b)
if extra:
    print('  extra STRATA_* in process (not in config):', extra)
PY

echo
echo "=== is the process newer than the config edit? ==="
python3 - <<PY
import os, subprocess
pid = subprocess.check_output("pgrep -f \"$ENGINE_PATTERN\" | head -1", shell=True).decode().strip()
pstart = float(open('/proc/%s/stat' % pid).read().split()[21]) / os.sysconf('SC_CLK_TCK')
boot = float(open('/proc/stat').read().split('btime')[1].split()[0])
start_wall = boot + pstart
mtime = os.path.getmtime("$CFG")
print('process started (unix):', int(start_wall))
print('config written (unix):', int(mtime))
print('VERDICT:', 'process started AFTER the config edit -> it loaded this config'
      if start_wall >= mtime else '*** process is OLDER than the config -> NOT running it ***')
PY
