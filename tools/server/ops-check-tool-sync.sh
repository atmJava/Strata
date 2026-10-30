#!/bin/bash
# Are the scripts in git the scripts the box runs?  One line per file, and a non-zero exit on any drift.
#
# /opt/ai/bin is what cron and the experiment drivers actually execute; tools/server is the source of truth.
# They drift silently - a scp that half happened, an edit made straight on the box - so every session should be
# able to answer the question in one command instead of comparing md5s by hand.
#
# Usage: ops-check-tool-sync.sh [user@host]        default: atm@192.168.9.103
# Exit: 0 every deployed file matches, 1 drift or a missing deployed file, 2 ssh failed.
set -u
HOST=${1:-atm@192.168.9.103}
SRC=$(cd "$(dirname "$0")" && pwd)
REMOTE=/opt/ai/bin

[ -d "$SRC" ] || { echo "cannot find the source folder"; exit 2; }

# a subdirectory in /opt/ai/bin makes "md5sum *" exit non-zero after printing the files it did read, so the
# listing is taken as it comes and only "did ssh work at all" is fatal.
remote_md5=$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" "md5sum $REMOTE/* 2>/dev/null || true")
[ -n "$remote_md5" ] || { echo "ssh to $HOST failed, or $REMOTE is unreadable"; exit 2; }

printf '%-34s %-10s %-10s %s\n' FILE GIT BOX STATE
drift=0; missing=0; ok=0
for f in "$SRC"/*; do
  [ -f "$f" ] || continue
  name=$(basename "$f")
  case "$name" in
    README.md|*.md|*.pyc) continue ;;
    ops-check-tool-sync.sh) continue ;;   # runs from the repository, never deployed
  esac
  mine=$(md5sum "$f" | awk '{print $1}')
  theirs=$(printf '%s\n' "$remote_md5" | awk -v n="/$name" '{ if (substr($2, length($2) - length(n) + 1) == n) { print $1; exit } }')
  if [ -z "$theirs" ]; then
    printf '%-34s %-10s %-10s %s\n' "$name" "${mine:0:8}" "-" "NOT DEPLOYED"; missing=$((missing + 1))
  elif [ "$mine" = "$theirs" ]; then
    printf '%-34s %-10s %-10s %s\n' "$name" "${mine:0:8}" "${theirs:0:8}" "same"; ok=$((ok + 1))
  else
    printf '%-34s %-10s %-10s %s\n' "$name" "${mine:0:8}" "${theirs:0:8}" "DIFFERS"; drift=$((drift + 1))
  fi
done

echo
echo "same: $ok   differs: $drift   not deployed: $missing"
[ "$drift" -gt 0 ] && echo "deploy with: scp $SRC/<file> $HOST:$REMOTE/ && ssh $HOST 'chmod 755 $REMOTE/<file>'"
[ "$drift" -eq 0 ] && [ "$missing" -eq 0 ]
