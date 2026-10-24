#!/bin/bash
# Only judge low-VRAM warnings from the CURRENT engine session.  The log is append-only, so a
# whole-file grep counts historical lines from before whatever the reserve was set to.
#
# Paths are overridable (this script was written against /opt/ai/src/strata-0.1.40.1 while
# production had moved to strata-0.2.2):
#   LOG=/opt/ai/src/strata-0.2.2/strata-022.log
#
# Trap: logrotate cuts the engine log at 00:00, and a session that started before the cut leaves
# no 'engine started' line in the current file.  Then the whole current file is the session tail -
# this script says so instead of silently reporting zero.
set -u
S=${S:-/opt/ai/src/strata-0.2.2}
L=${LOG:-$S/strata-022.log}
PAT=${PAT:-'below 512 MiB\|WARNING: only\|LOW:'}
MARK=$(grep -an 'engine started' "$L" | tail -1 | cut -d: -f1)
if [ -z "$MARK" ]; then
  MARK=1
  echo "no 'engine started' line in $L - the file was rotated mid-session, so this is the session tail"
fi
echo "current session judged from log line $MARK (of $(wc -l < "$L"))"

echo
echo "=== whole-file count (misleading, includes history from older reserves) ==="
grep -ac "$PAT" "$L" || true

echo
echo "=== CURRENT session only ==="
NEW=$(awk -v m="$MARK" 'NR>=m' "$L")
echo "low-VRAM warning lines: $(echo "$NEW" | grep -ac "$PAT" || true)"
echo "$NEW" | grep -a "$PAT" | head -8

echo
echo "=== VRAM free lines (current session) ==="
echo "$NEW" | grep -a 'MiB of VRAM free' | tail -4

echo
echo "=== expert-cache hit lines (current session; for the real distribution use passive_windows.py) ==="
echo "$NEW" | grep -a 'decode expert cache hit rate' | tail -3

echo
echo "=== concurrent-read lines (current session; see concurrent_reads.py) ==="
echo "$NEW" | grep -ac 'on a thread of its own' || true

echo
echo "=== nvidia-smi ==="
nvidia-smi --query-gpu=index,memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw --format=csv,noheader
