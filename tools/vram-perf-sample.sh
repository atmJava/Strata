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
# (a jump between two samples means a request started below the line in between).
# The two engine columns are whatever the engine printed last for real traffic, so they
# describe the workload, not a controlled arm - use them as a trend, never as a verdict.

LOG="${VRAM_PERF_LOG:-/opt/ai/src/strata-0.2.2/strata-022.log}"
SLOG="${VRAM_PERF_SLOG:-/opt/ai/logs/strata-service.log}"
OUT="${VRAM_PERF_OUT:-/opt/ai/logs/vram-perf.csv}"

ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
free=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>/dev/null | tr '\n' ' ')
f0=$(echo "$free" | awk '{print $1}')
f1=$(echo "$free" | awk '{print $2}')
warn=$(grep -c 'WARNING: only' "$LOG" 2>/dev/null || echo 0)
# The engine log carries the VRAM warnings; the speed and hit-rate lines land in the
# service log, so the two columns read two different files on purpose.
tail40=$(tail -40 "$SLOG" 2>/dev/null)
tok=$(echo "$tail40" | grep -oE '[0-9.]+ tok/s\) \(stop' | tail -1 | grep -oE '^[0-9.]+')
hit=$(echo "$tail40" | grep -oE 'expert cache [0-9.]+% hit' | tail -1 | grep -oE '[0-9.]+')

echo "$ts,${f0:-NA},${f1:-NA},${warn},${tok:-NA},${hit:-NA}" >> "$OUT"
