#!/bin/bash
# ops-v2-arm.sh - switch one engine environment variable for an A/B arm, through the config's env block
# (the engine's env comes from the server config, not the launcher), a restart via use-config.sh, and a
# read-back from the running process.  Dry-run unless --go.
#
#   ops-v2-arm.sh show                                   the config's env block, sorted
#   ops-v2-arm.sh set NAME VALUE [--go]                  set NAME=VALUE (VALUE "off" or empty removes it)
#   ops-v2-arm.sh set NAME VALUE --dry-config            only print the edited JSON path (no restart)
#
# Why the config: /opt/ai/src/strata-0.2.2/strata-022.json carries a 17-key "env" block; the server hands
# those to the engine, so an arm change is one JSON edit + one use-config.sh restart.  The launcher's own
# exports (STRATA_MP_STAGED_READ*) are the server's and are not touched here.
set -u

CFG=/opt/ai/src/strata-0.2.2/strata-022.json
USE_CONFIG=/opt/ai/bin/use-config.sh
GO=0
CMD=""
NAME=""
VALUE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --go) GO=1 ;;
    --dry-config) GO=2 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    show|set) CMD=$1 ;;
    *) if [ -z "$NAME" ]; then NAME=$1; elif [ -z "$VALUE" ]; then VALUE=$1; else echo "unexpected: $1" >&2; exit 2; fi ;;
  esac
  shift
done

show() {
  python3 - "$CFG" <<'PY'
import json, sys
env = json.load(open(sys.argv[1])).get("env") or {}
for k in sorted(env):
    print(f"{k}={env[k]}")
print(f"({len(env)} keys)")
PY
}

case "${CMD:-show}" in
  show) show ;;
  set)
    [ -n "$NAME" ] || { echo "set needs NAME [VALUE]" >&2; exit 2; }
    out="/tmp/v2-arm-$NAME-${VALUE:-off}.json"
    echo "== edited config -> $out"
    python3 - "$CFG" "$NAME" "$VALUE" "$out" <<'PY'
import json, sys
cfg, name, value, out = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
c = json.load(open(cfg))
env = c.setdefault("env", {})
if value in ("", "off"):
    env.pop(name, None)
else:
    env[name] = value
json.dump(c, open(out, "w"), indent=1)
print(f"{name}={value or '(removed)'}; env now {len(env)} keys")
PY
    if [ "$GO" -eq 2 ]; then
      echo "dry config only; nothing restarted"
      exit 0
    fi
    if [ "$GO" -eq 0 ]; then
      echo "(dry run: would run $USE_CONFIG $out; add --go)"
      exit 0
    fi
    "$USE_CONFIG" "$out" || { echo "RESTART FAILED - restore with ops-v2-window.sh restore --go" >&2; exit 1; }
    pid=$(pgrep -f '[s]trata --serve' | head -1)
    echo "== running engine pid ${pid:-none}; $NAME in its environment:"
    [ -n "$pid" ] && tr '\0' '\n' < "/proc/$pid/environ" | grep -E "^$NAME=" | sed 's/^/  /' || echo "  (not set)"
    ;;
esac
