#!/bin/bash
# A2: hourly NVLink error-counter sample.
#
# Why: the 2026-10-13 state check found CRC errors accumulating on GPU0 only, and
# a controlled idle test showed the counters are LOAD-related (frozen at 417/300
# over 4 idle minutes) with Replay/Recovery at 0 -> no hardware action needed, but
# worth a trend line.  Replay/Recovery staying 0 is what keeps this "watch" rather
# than "act"; if they start moving, power down and reseat the bridge.
#
# See docs/strata-state-check-20261013.md §3.2 and §5 A2.
set -u
OUT=/opt/ai/logs/nvlink-crc.log

if [ ! -f "$OUT" ]; then
  {
    echo "# NVLink error counters, sampled hourly by nvlink-crc-sample.timer"
    echo "# columns: ts_utc gpu link crc replay recovery   (one row per link)"
    echo "# GPU0 CRC growth is the signal; Replay/Recovery > 0 means reseat the bridge"
  } > "$OUT"
fi

TS=$(date -u +%FT%TZ)
nvidia-smi nvlink -e 2>/dev/null | awk -v ts="$TS" '
  /^GPU [0-9]+:/            { gpu = $2; sub(/:$/, "", gpu) }
  /Link [0-9]+: CRC Errors:/      { link = $2; sub(/:$/, "", link); crc[gpu "/" link]  = $5 }
  /Link [0-9]+: Replay Errors:/   { link = $2; sub(/:$/, "", link); rp[gpu "/" link]   = $5 }
  /Link [0-9]+: Recovery Errors:/ { link = $2; sub(/:$/, "", link); rc[gpu "/" link]   = $5 }
  END {
    for (k in crc) {
      split(k, p, "/")
      printf "%s gpu=%s link=%s crc=%s replay=%s recovery=%s\n", ts, p[1], p[2],
             crc[k], (k in rp ? rp[k] : "?"), (k in rc ? rc[k] : "?")
    }
  }' | sort -k2,2 -k3,3 >> "$OUT"

# keep the file bounded even if logrotate is not configured for it
if [ "$(wc -l < "$OUT")" -gt 4000 ]; then
  tail -3000 "$OUT" > "$OUT.tmp" && mv "$OUT.tmp" "$OUT"
fi
