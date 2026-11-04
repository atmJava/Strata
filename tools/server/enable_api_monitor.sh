#!/bin/bash
# Turn on the server's request monitor (config key "api_monitor": true) and read the queue directly.
#
# Why: serve/server.py already times, for every request, the three phases we keep arguing about from the
# outside - queue_s (waiting for the control lines and a batch slot), load_s (the prompt read), and
# first_token_s - and exposes them per request on GET /api/requests.  It is off by default, so we have been
# reconstructing queueing from client walls and engine log lines (L-020).  With it on, real agent traffic
# carries its own phase breakdown, which is the primary metric this project is supposed to optimise.
#
# It is opt-in upstream and off by default, so this is a config change on our box, not a code change.  The
# monitor keeps the last 100 requests in memory (input truncated at 262144 chars each), so the dump has to
# happen right after the run: nothing is persisted.
#
# Usage: enable_api_monitor.sh [peers] [peer-max-tokens] [reps]     defaults: 4 1200 2
# Output: /opt/ai/logs/L021-api-monitor-<UTC ts>.log
set -u
S=/opt/ai/src/strata-0.2.2
CFG=$S/strata-022.json
BIN=/opt/ai/bin
LOGS=/opt/ai/logs
TMP=/opt/ai/tmp
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT=$LOGS/L021-api-monitor-$TS.log
PEERS=${1:-4}
TOKS=${2:-1200}
REPS=${3:-2}
KEY=111

say() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$OUT"; }

peek() {   # the monitor's own table, trimmed of the prompt text
  python3 - "$KEY" <<'PY' 2>&1 | tee -a "$OUT"
import json, sys, urllib.request
req = urllib.request.Request("http://127.0.0.1:8080/api/requests",
                             headers={"Authorization": "Bearer " + sys.argv[1]})
try:
    body = json.load(urllib.request.urlopen(req, timeout=10))
except urllib.error.HTTPError as e:
    print(f"  /api/requests -> HTTP {e.code}: {e.read().decode(errors='replace')[:120]}")
    raise SystemExit(1)
rows = body.get("requests", [])
print(f"  {len(rows)} requests kept (newest first); columns: state stream queue_s load_s first_token_s wallclock_s")
for r in rows[:40]:
    print("  %-10s %-5s %8s %8s %14s %11s  %s" % (
        r.get("state"), str(r.get("stream")), r.get("queue_s"), r.get("load_s"),
        r.get("first_token_s"), r.get("wallclock_s"),
        str(r.get("path")) + " in=" + str(r.get("input_tokens", ""))))
json.dump(rows, open("/opt/ai/tmp/L021-requests-%s.json" % sys.argv[1], "w"))
PY
}

say "=== L021: enable api_monitor and read the queue the server already times ==="
cp -p "$CFG" "$TMP/monitor-off-$TS.json"
say "live config snapshotted -> $TMP/monitor-off-$TS.json (restore this to turn the monitor off)"

python3 - "$CFG" "$TMP/monitor-on-$TS.json" <<'PY' || { say "FAILED to build the monitor variant"; exit 1; }
import json, sys
cfg = json.load(open(sys.argv[1]))
before = cfg.get("api_monitor")
cfg["api_monitor"] = True
json.dump(cfg, open(sys.argv[2], "w"), indent=1, ensure_ascii=False)
print("monitor variant: api_monitor was %r -> True; args untouched (%d strings); gate=%s"
      % (before, len(cfg["args"]), cfg["env"].get("STRATA_PIPED_CONCURRENT")))
PY

say "--- restart with the monitor on ---"
PRE=$(date -u +"%Y-%m-%d %H:%M:%S")
"$BIN/use-config.sh" "$TMP/monitor-on-$TS.json" >>"$OUT" 2>&1 || { say "FAILED to install the monitor variant"; exit 1; }

say "--- the shape L-020 measured from outside: $PEERS streams x $TOKS tokens + one cold read ---"
PEER_MAX_TOKENS=$TOKS "$BIN/piped_scenario.py" "$REPS" 60000 coexist "$PEERS" >>"$OUT" 2>&1
say "--- what the server itself timed for those requests ---"
peek
say "--- and the bench, to see whether the monitor costs anything (compare with L-019: 73.0/74.4/74.2 single, 125.0/124.4/121.2 aggregate) ---"
"$BIN/bench-strata.py" "L021-monitor-on" 3 >>"$OUT" 2>&1
peek
say "=== done; the monitor is ON.  Restore with: $BIN/use-config.sh $TMP/monitor-off-$TS.json ==="
"$BIN/concurrent_reads.py" "$S/strata-022.log" --since "$PRE" >>"$OUT" 2>&1
