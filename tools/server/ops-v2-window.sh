#!/bin/bash
# ops-v2-window.sh - the V2 window on the production box: install the V1-built engine + server tree, or
# put the pre-window snapshot back.  Dry-run unless --go is given.
#
#   ops-v2-window.sh snapshot          take /opt/ai/tmp/pre-V2-<ts>/ (engine, serve/, tools/strata_tokenizer.py,
#                                      launcher, config) and verify it with md5sum -c
#   ops-v2-window.sh install --go ...  install, then restart via /opt/ai/bin/use-config.sh (the only entry),
#                                      then verify (md5, markers, health, verify-running-config)
#   ops-v2-window.sh restore  --go ... copy the newest pre-V2 snapshot back, restart, verify
#   ops-v2-window.sh check [--snap D]  read-only: what is deployed vs what the snapshot holds
#
# Why a script: the install is four copies and one restart; done by hand at 00:05 it is where a typo lands.
# The launcher and the config are NOT replaced: the launcher's STRATA_* exports are production's and the
# config's 54 engine args do not change with the engine version.  (Checked 2026-10-25: the 0.2.2 config's
# exe/cwd point at this tree and the 0.2.3 server reads the same keys.)
set -u

PROD=/opt/ai/src/strata-0.2.2
NEW=/opt/ai/src/strata-port-v1
SNAP_ROOT=/opt/ai/tmp
USE_CONFIG=/opt/ai/bin/use-config.sh

GO=0
CMD=""
SNAP=""
while [ $# -gt 0 ]; do
  case "$1" in
    --go) GO=1 ;;
    --snap) SNAP=${2:?--snap needs a directory}; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
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

# --- read-only check ---------------------------------------------------------------------------------
do_check() {
  local want="${SNAP:-$(newest_snapshot)}"
  say "== production tree: $PROD"
  say "engine  $(md5_of "$PROD/build/strata")  $(stat -c%s "$PROD/build/strata" 2>/dev/null) B"
  say "server  $(md5_of "$PROD/serve/server.py")"
  say "tokenizer $(md5_of "$PROD/tools/strata_tokenizer.py")"
  say "launcher $(md5_of "$PROD/run-022.sh")"
  say "config  $(md5_of "$PROD/strata-022.json")"
  say "== markers in the deployed server"
  for m in 'BCANCEL_DRAIN_S' 'malformed staged-read' 'request_started = time.time()'; do
    printf '  %-28s %s\n' "$m" "$(grep -c -- "$m" "$PROD/serve/server.py" 2>/dev/null)"
  done
  say "== what is running"
  local pid; pid=$(pgrep -f 'build/strata --serve' | head -1)
  say "engine pid ${pid:-none}"
  [ -n "$pid" ] && tr '\0' '\n' < "/proc/$pid/environ" | grep -E '^STRATA_' | sort | sed 's/^/  /'
  say "health: $(health | cut -c1-120)"
  if [ -n "$want" ] && [ -d "$want" ]; then
    say "== against snapshot $want"
    cmp -s "$want/strata" "$PROD/build/strata" && say "  engine SAME" || say "  engine DIFFERENT"
    ( cd "$want" && tar -tzf serve-tools.tgz >/dev/null 2>&1 ) && say "  snapshot tarball ok"
  else
    say "== no pre-V2 snapshot yet"
  fi
}

# --- snapshot ----------------------------------------------------------------------------------------
do_snapshot() {
  local ts snap
  ts=$(date -u +%Y%m%dT%H%M%SZ)
  snap="$SNAP_ROOT/pre-V2-$ts"
  say "== snapshot -> $snap"
  run mkdir -p "$snap"
  run cp -p "$PROD/build/strata" "$snap/strata"
  run cp -p "$PROD/run-022.sh" "$snap/run-022.sh"
  run cp -p "$PROD/strata-022.json" "$snap/strata-022.json"
  run cp -p "$PROD/tools/strata_tokenizer.py" "$snap/strata_tokenizer.py"
  run tar -C "$PROD" -czf "$snap/serve.tgz" serve
  if [ "$GO" -eq 1 ]; then
    ( cd "$snap" && md5sum strata run-022.sh strata-022.json strata_tokenizer.py > MANIFEST.md5 && md5sum -c MANIFEST.md5 ) \
      || { echo "snapshot verification failed" >&2; exit 1; }
    echo "snapshot ready: $snap"
  else
    say "  (dry run: no files written)"
  fi
}

# --- install -----------------------------------------------------------------------------------------
do_install() {
  local snap; snap="${SNAP:-$(newest_snapshot)}"
  [ -n "$snap" ] && [ -d "$snap" ] || { echo "no pre-V2 snapshot found; run: $0 snapshot --go" >&2; exit 1; }
  for f in "$NEW/build75/strata" "$NEW/serve/server.py" "$NEW/tools/strata_tokenizer.py"; do
    [ -f "$f" ] || { echo "missing build artifact: $f" >&2; exit 1; }
  done
  health | grep -q '"status": "ok"' || { echo "production is not healthy before the install; refusing" >&2; exit 1; }

  say "== install from $NEW (snapshot $snap)"
  say "engine   $(md5_of "$NEW/build75/strata")  <- $NEW/build75/strata"
  say "server   $(md5_of "$NEW/serve/server.py")  <- $NEW/serve/ (whole tree)"
  say "tokenizer $(md5_of "$NEW/tools/strata_tokenizer.py")  <- $NEW/tools/strata_tokenizer.py"
  # The running process keeps its old inode; the swap is atomic per file.
  run cp "$NEW/build75/strata" "$PROD/build/strata.new"
  run mv -f "$PROD/build/strata.new" "$PROD/build/strata"
  run rsync -a --delete "$NEW/serve/" "$PROD/serve.new/"
  run mv "$PROD/serve" "$PROD/serve.pre-$(date -u +%Y%m%dT%H%M%SZ)"
  run mv "$PROD/serve.new" "$PROD/serve"
  run cp "$NEW/tools/strata_tokenizer.py" "$PROD/tools/strata_tokenizer.py.new"
  run mv -f "$PROD/tools/strata_tokenizer.py.new" "$PROD/tools/strata_tokenizer.py"

  # use-config.sh needs a config path distinct from the live one; the content is production's, unchanged.
  run cp -p "$PROD/strata-022.json" /tmp/v2-window-config.json
  say "== restart through the only entry"
  run "$USE_CONFIG" /tmp/v2-window-config.json
  if [ "$GO" -eq 1 ]; then do_verify_install; fi
}

do_verify_install() {
  say "== verify"
  local pid; pid=$(pgrep -f 'build/strata --serve' | head -1)
  say "engine pid ${pid:-none}"
  for m in BCANCEL_DRAIN_S 'malformed staged-read'; do
    printf '  %-24s %s\n' "$m" "$(grep -c -- "$m" "$PROD/serve/server.py")"
  done
  say "health: $(health | cut -c1-160)"
  say "== verify-running-config"
  /opt/ai/bin/verify-running-config.sh 2>&1 | grep -E 'ARGS MATCH|ENV DIFFERENT|MISSING|EXTRA|DIFFERENT' | sed 's/^/  /'
  say "== installed hashes"
  say "engine   $(md5_of "$PROD/build/strata")"
  say "server   $(md5_of "$PROD/serve/server.py")"
  say "tokenizer $(md5_of "$PROD/tools/strata_tokenizer.py")"
}

# --- restore -----------------------------------------------------------------------------------------
do_restore() {
  local snap; snap="${SNAP:-$(newest_snapshot)}"
  [ -n "$snap" ] && [ -d "$snap" ] || { echo "no snapshot to restore from" >&2; exit 1; }
  say "== restore from $snap"
  run cp "$snap/strata" "$PROD/build/strata.new"
  run mv -f "$PROD/build/strata.new" "$PROD/build/strata"
  run cp "$snap/strata_tokenizer.py" "$PROD/tools/strata_tokenizer.py.new"
  run mv -f "$PROD/tools/strata_tokenizer.py.new" "$PROD/tools/strata_tokenizer.py"
  run cp "$snap/run-022.sh" "$PROD/run-022.sh.new"
  run mv -f "$PROD/run-022.sh.new" "$PROD/run-022.sh"
  run mv "$PROD/serve" "$PROD/serve.pre-restore-$(date -u +%Y%m%dT%H%M%SZ)"
  run tar -C "$PROD" -xzf "$snap/serve.tgz"
  run cp -p "$snap/strata-022.json" /tmp/v2-window-config.json
  say "== restart through the only entry"
  run "$USE_CONFIG" /tmp/v2-window-config.json
  if [ "$GO" -eq 1 ]; then
    say "health: $(health | cut -c1-160)"
    say "engine $(md5_of "$PROD/build/strata") (want $(md5_of "$snap/strata"))"
    say "server $(md5_of "$PROD/serve/server.py")"
  fi
}

say "ops-v2-window: $CMD$([ "$GO" -eq 1 ] && echo '' || echo ' (dry run; add --go)')"
case "$CMD" in
  check) do_check ;;
  snapshot) do_snapshot ;;
  install) do_install ;;
  restore) do_restore ;;
esac
