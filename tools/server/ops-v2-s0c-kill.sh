#!/bin/bash
# ops-v2-s0c-kill.sh - S0c's live crash test: kill the engine while a staged read waits, then the next
# request must be admitted after the service comes back.  Dry-run unless --go.
#
# What it does with --go:
#   1. starts tools/server/tests/probe_cancel_staged.py (6 long decodes fill the six slots, a 7th ~22K read
#      is staged and its client goes away) in the background;
#   2. ~8 s in, `kill -9`s the engine pid (the server exits with EngineDied and systemd restarts the unit);
#   3. waits for /health "loaded": true (RestartSec 15 + cold load, allow 420 s);
#   4. sends one short chat request and requires a 200 with an answer - that is "the next request is
#      admitted", i.e. the staged state did not survive the crash in a way that blocks admissions;
#   5. prints the timeline, the probe's JSON (its gaps/errors during the crash are expected) and the health.
#
# It is the window's last step because it costs a restart; recovery beats the budget (RULES §9.6).
set -u

HOST=atm@192.168.3.14
BASE=http://192.168.3.14:8080
KEY=111
GO=0
[ "${1:-}" = "--go" ] && GO=1
[ "${1:-}" = "-h" ] && { sed -n '2,20p' "$0"; exit 0; }

say() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*"; }
ssh_() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" "$@"; }

pid=$(ssh_ "pgrep -f '[s]trata --serve' | head -1")
[ -n "$pid" ] || { echo "no engine process found" >&2; exit 1; }
say "engine pid=$pid"
if [ "$GO" -eq 0 ]; then
  say "dry run: would launch the staged drill, wait for a 'staged read sent' line (<=25 s), kill -9 $pid," \
      "then wait for health and one request"
  exit 0
fi

staged_count() { ssh_ "grep -c 'staged read sent' /opt/ai/logs/strata-service.log" 2>/dev/null || echo 0; }
before=$(staged_count)
say "staged-read lines before: $before; launching the staged drill in the background"
( python3 tools/server/tests/probe_cancel_staged.py "$BASE" 6 > /tmp/v2-s0c-probe.json 2>/tmp/v2-s0c-probe.err ) &
probe_pid=$!
seen=""
for i in $(seq 1 25); do
  sleep 1
  now_count=$(staged_count)
  if [ "${now_count:-0}" -gt "${before:-0}" ] 2>/dev/null; then seen=1; say "staged read seen ($before -> $now_count)"; break; fi
done
if [ -z "$seen" ]; then
  say "no staged read appeared in 25 s; NOT killing (the scenario did not form)"
  wait "$probe_pid" 2>/dev/null
  exit 1
fi

now=$(ssh_ "pgrep -f '[s]trata --serve' | head -1")
if [ -z "$now" ] || [ "$now" != "$pid" ]; then
  say "engine pid changed before the kill ($now vs $pid); aborting the kill"
  wait "$probe_pid" 2>/dev/null
  exit 1
fi
say "kill -9 $pid (the staged read is pending)"
ssh_ "kill -9 $pid"
killed_at=$(date +%s)

say "waiting for the service to report loaded:true (<=420 s)"
ready=""
for i in $(seq 1 140); do
  h=$(curl -s -m 5 "$BASE/health" 2>/dev/null)
  case "$h" in *'"loaded": true'*) ready=1; say "READY after ~$((i*3))s"; break;; esac
  if ! ssh_ "systemctl is-active --quiet strata.service" 2>/dev/null; then
    say "unit is not active; will keep waiting up to 420 s"
  fi
  sleep 3
done
if [ -z "$ready" ]; then
  say "NOT READY after 420 s - recover with: ssh $HOST 'sh /opt/ai/tmp/ops-v2-window.sh restore --go'"
  exit 1
fi

say "one short request (the admission check)"
body='{"model":"qwen3.8-flash-next-iq3_xxs","messages":[{"role":"user","content":"用一句话说明什么是 MoE。"}],"max_tokens":24,"temperature":0,"reasoning_effort":"none"}'
answer=$(curl -s -m 120 -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" -d "$body" "$BASE/v1/chat/completions")
text=$(printf '%s' "$answer" | python3 -c 'import json,sys;d=json.load(sys.stdin);print((d.get("choices") or [{}])[0].get("message",{}).get("content") or "")' 2>/dev/null)
say "recovery took $(( $(date +%s) - killed_at )) s; answer chars=${#text}"
wait "$probe_pid" 2>/dev/null
say "probe JSON (/tmp/v2-s0c-probe.json):"
head -20 /tmp/v2-s0c-probe.json 2>/dev/null
[ -n "$text" ] && { say "S0C PASS: next request admitted after the crash"; exit 0; }
say "S0C FAIL: no answer after recovery"
exit 1
