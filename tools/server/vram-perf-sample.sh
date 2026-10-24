#!/bin/sh
# Passive sampler: VRAM headroom + the engine's own speed / hit readings.
#
# It adds no load: it only reads nvidia-smi and the tail of the engine log. A concurrent
# benchmark on this box halves single-stream speed, so a clean A/B needs a maintenance
# window; between windows this script is what tells us whether the box is drifting toward
# the #2065 stall (free VRAM under 512 MiB) and whether the expert cache is losing hits.
#
# Run from cron every 5 minutes as the service user:
#   */5 * * * * /opt/ai/bin/vram-perf-sample.sh
#
# Output: /opt/ai/logs/vram-perf.csv, one line per sample:
#   utc,free_mib_gpu0,free_mib_gpu1,vram_warning_count,last_tok_s,last_hit_pct
# The warning count is cumulative over the current log file, so read it as a trend
# (a jump between two samples means a request started below the line in between).  logrotate rotates
# strata-022.log to strata-022.log.1 at 00:00, which resets this column to 0 every night: a 0 after
# midnight means "fresh file", not "no warnings" - the previous day's count is in strata-022.log.1.
# The two engine columns are whatever the engine printed last for real traffic, so they
# describe the workload, not a controlled arm - use them as a trend, never as a verdict.

LOG="${VRAM_PERF_LOG:-/opt/ai/src/strata-0.2.2/strata-022.log}"
SLOG="${VRAM_PERF_SLOG:-/opt/ai/logs/strata-service.log}"
OUT="${VRAM_PERF_OUT:-/opt/ai/logs/vram-perf.csv}"

ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
free=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>/dev/null | tr '\n' ' ')
f0=$(echo "$free" | awk '{print $1}')
f1=$(echo "$free" | awk '{print $2}')
# "grep -c" already prints 0 when nothing matches and then exits 1, so the obvious-looking "|| echo 0"
# appended a SECOND zero: the warn field became "0\n0" and split every CSV record across two physical
# lines.  That is exactly what happens after the nightly logrotate empties the log, so the monitor went
# semi-blind at midnight - a reader filtering on NF>=6 dropped every sample after it.  Verified on the
# box: strata-022.log held 0 matches and the field came out as two lines.
warn=$(grep -c 'WARNING: only' "$LOG" 2>/dev/null || true)
[ -n "$warn" ] || warn=0
# The engine log carries the VRAM warnings; the speed and hit-rate lines land in the
# service log, so the two columns read two different files on purpose.
tail40=$(tail -40 "$SLOG" 2>/dev/null)
tok=$(echo "$tail40" | grep -oE '[0-9.]+ tok/s\) \(stop' | tail -1 | grep -oE '^[0-9.]+')
hit=$(echo "$tail40" | grep -oE 'expert cache [0-9.]+% hit' | tail -1 | grep -oE '[0-9.]+')

echo "$ts,${f0:-NA},${f1:-NA},${warn},${tok:-NA},${hit:-NA}" >> "$OUT"

# Early warning, ahead of the engine's own 512 MiB line (#2065): a request that starts that close to
# full can page GPU memory to RAM and never finish. 700 MiB leaves room for one more verify window.
# The flag file is what a later round reads; it is not deleted here on purpose, so a dip is not missed.
if [ -n "$f0" ] && [ "$f0" -lt "${VRAM_PERF_WARN_MIB:-700}" ]; then
    echo "$ts gpu0_free=$f0 gpu1_free=${f1:-NA} below ${VRAM_PERF_WARN_MIB:-700} MiB" >> "$OUT.warn"
fi
