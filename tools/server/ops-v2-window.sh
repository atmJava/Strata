#!/bin/bash
# ops-v2-window.sh - the V2a window: run the ported 0.2.3 tree's server from /opt/ai/src/strata-port-v1 and
# the ported engine as /opt/ai/src/strata-0.2.2/build/strata, with production's config and launcher-as-entry.
# Dry-run unless --go.
#
# Two files in the production tree change: build/strata (the ported engine) and run-022.sh (which now runs
# the port tree's server).  The config is NOT touched: its exe/cwd stay production's, so use-config.sh's
# restart detection (pgrep 'build/strata --serve') and verify-running-config.sh keep working.  The port tree
# is self-contained (its serve/, strata_control/, strata_cli/ and tools/ are the repo's own, as the mock
# start proved) - the first attempt failed because it mixed 0.2.3's serve/ with 0.2.2's strata_control,
# which has no tls module.
#
#   snapshot --go   run-022.sh + strata-022.json + the old engine + a serve/ tarball -> pre-V2-<ts>/, md5-checked
#   install  --go   copy the ported engine in, write the port-tree launcher, restart, verify
#   restore  --go   put the snapshot's engine and launcher back, restart, verify
#   check           read-only: what is live vs what the snapshot holds
set -u

PROD=/opt/ai/src/strata-0.2.2
NEW=/opt/ai/src/strata-port-v1
SNAP_ROOT=/opt/ai/tmp
USE_CONFIG=/opt/ai/bin/use-config.sh
PY=/opt/ai/src/strata-0.1.40.1/.venv/bin/python

GO=0
CMD=""
SNAP=""
while [ $# -gt 0 ]; do
  case "$1" in
    --go) GO=1 ;;
    --snap) SNAP=${2:?--snap needs a directory}; shift ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    snapshot|install|restore|check) CMD=$1 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
[ -n "$CMD" ] || { echo "usage: $0 snapshot|install|restore|check [--go] [--snap DIR]" >&2; exit 2; }

say() { printf '%s\n' "$*"; }
run() {
  if [ "$GO" -eq 1 ]; then
    say "+ $*"
    "$@" || { echo "FAILED: $*" >&2; exit 1; }
  else
    say "  $*"
  fi
}
md5_of() { md5sum "$1" 2>/dev/null | cut -d' ' -f1; }
newest_snapshot() { ls -1d "$SNAP_ROOT"/pre-V2-* 2>/dev/null | sort | tail -1; }
health() { curl -s -m 5 http://127.0.0.1:8080/health 2>/dev/null; }

write_launcher() {
  local out=$1
  cat > "$out" <<EOF
#!/bin/sh
# Strata 0.2.3 (engine 0.1.49.1) from $NEW; the config carries the 54 engine args and 17 env keys.
cd "$NEW"
# --- MP W3 staged read: opt-in, as before ---
export STRATA_MP_STAGED_READ=1
export STRATA_MP_STAGED_READ_MIN_TOKENS=4096
# --- end MP ---
exec "$PY" "$NEW/serve/server.py" "--engine" "strata" "--config" "$PROD/strata-022.json" "--port" "8080"
EOF
  chmod 755 "$out"
}

# the config stays production's; use-config.sh just needs a path that is not the live one
copy_config() {
  cp -p "$PROD/strata-022.json" "$1"
  python3 -c "import json,sys;c=json.load(open(sys.argv[1]));print('config copy: exe',c.get('exe'),'args',len(c['args']),'env',len(c.get('env') or {}))" "$1"
}

do_check() {
  local want="${SNAP:-$(newest_snapshot)}"
  say "== live production tree: $PROD"
  say "launcher $(md5_of "$PROD/run-022.sh")"
  say "config   $(md5_of "$PROD/strata-022.json")"
  python3 -c "import json;c=json.load(open('$PROD/strata-022.json'));print('  exe',c.get('exe'));print('  cwd',c.get('cwd'))"
  say "engine   $(md5_of "$PROD/build/strata") (deployed) / $(md5_of "$NEW/build75/strata") (ported build75)"
  say "launcher -> $(grep -o "$NEW[^\\\"]*server.py" "$PROD/run-022.sh" 2>/dev/null | head -1)"
  say "health:   $(health | cut -c1-120)"
  local pid; pid=$(pgrep -f 'strata --serve' | head -1)
  say "running engine pid ${pid:-none}: $(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | cut -c1-110)"
  if [ -n "$want" ] && [ -d "$want" ]; then
    say "== snapshot $want"
    cmp -s "$want/run-022.sh" "$PROD/run-022.sh" && say "  launcher SAME" || say "  launcher DIFFERENT"
    cmp -s "$want/strata-022.json" "$PROD/strata-022.json" && say "  config SAME" || say "  config DIFFERENT"
    cmp -s "$want/strata" "$PROD/build/strata" && say "  engine SAME" || say "  engine DIFFERENT"
  else
    say "== no pre-V2 snapshot yet"
  fi
}

do_snapshot() {
  local ts snap
  ts=$(date -u +%Y%m%dT%H%M%SZ)
  snap="$SNAP_ROOT/pre-V2-$ts"
  say "== snapshot -> $snap"
  run mkdir -p "$snap"
  run cp -p "$PROD/run-022.sh" "$snap/run-022.sh"
  run cp -p "$PROD/strata-022.json" "$snap/strata-022.json"
  run cp -p "$PROD/build/strata" "$snap/strata"
  run tar -C "$PROD" -czf "$snap/serve.tgz" serve
  if [ "$GO" -eq 1 ]; then
    ( cd "$snap" && md5sum run-022.sh strata-022.json strata > MANIFEST.md5 && md5sum -c MANIFEST.md5 ) \
      || { echo "snapshot verification failed" >&2; exit 1; }
    echo "snapshot ready: $snap"
  fi
}

do_install() {
  local snap; snap="${SNAP:-$(newest_snapshot)}"
  [ -n "$snap" ] && [ -d "$snap" ] || { echo "no pre-V2 snapshot; run: $0 snapshot --go" >&2; exit 1; }
  [ -x "$NEW/build75/strata" ] && [ -f "$NEW/serve/server.py" ] || { echo "ported tree is incomplete" >&2; exit 1; }
  health | grep -q '"status": "ok"' || { echo "production is not healthy before the install; refusing" >&2; exit 1; }

  say "== install: ported engine + launcher (snapshot $snap)"
  run cp "$NEW/build75/strata" "$PROD/build/strata.new"
  run mv -f "$PROD/build/strata.new" "$PROD/build/strata"
  write_launcher /tmp/run-022.new
  run cp /tmp/run-022.new "$PROD/run-022.sh.new"
  run mv -f "$PROD/run-022.sh.new" "$PROD/run-022.sh"
  copy_config /tmp/v2-window-config.json
  say "== restart through the only entry"
  run "$USE_CONFIG" /tmp/v2-window-config.json
  if [ "$GO" -eq 1 ]; then do_verify; fi
}

do_verify() {
  say "== verify"
  say "engine on disk (deployed): $(md5_of "$PROD/build/strata")  (want 84a0d410367abfb3a8bcb9422a3b10ee)"
  local pid; pid=$(pgrep -f 'strata --serve' | head -1)
  say "running engine pid ${pid:-none}: $(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | cut -c1-140)"
  say "health: $(health | cut -c1-160)"
  say "== verify-running-config"
  /opt/ai/bin/verify-running-config.sh 2>&1 | grep -E 'ARGS MATCH|ENV DIFFERENT|MISSING|EXTRA|DIFFERENT|engine pid' | sed 's/^/  /'
}

do_restore() {
  local snap; snap="${SNAP:-$(newest_snapshot)}"
  [ -n "$snap" ] && [ -d "$snap" ] || { echo "no snapshot to restore" >&2; exit 1; }
  say "== restore from $snap"
  run cp "$snap/strata" "$PROD/build/strata.new"
  run mv -f "$PROD/build/strata.new" "$PROD/build/strata"
  run cp "$snap/run-022.sh" "$PROD/run-022.sh.new"
  run mv -f "$PROD/run-022.sh.new" "$PROD/run-022.sh"
  run cp -p "$snap/strata-022.json" /tmp/v2-restore-config.json
  say "== restart through the only entry"
  run "$USE_CONFIG" /tmp/v2-restore-config.json
  if [ "$GO" -eq 1 ]; then
    say "health: $(health | cut -c1-160)"
    say "engine   $(md5_of "$PROD/build/strata") (want $(md5_of "$snap/strata"))"
    say "launcher $(md5_of "$PROD/run-022.sh") (want $(md5_of "$snap/run-022.sh"))"
  fi
}

say "ops-v2-window: $CMD$([ "$GO" -eq 1 ] && echo '' || echo ' (dry run; add --go)')"
case "$CMD" in
  check) do_check ;;
  snapshot) do_snapshot ;;
  install) do_install ;;
  restore) do_restore ;;
esac
