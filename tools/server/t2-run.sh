#!/bin/bash
# usage: t2-run.sh <off|on> <label> [reps]
set -u
ARM=${1:?}; LABEL=${2:?}; REPS=${3:-2}
cd /opt/ai/src/strata-0.2.2 || exit 1
python3 /opt/ai/bin/prepare-t2.py "$ARM" || exit 1
bash /opt/ai/bin/use-config.sh strata-022.t2.json || exit 1
E=$(pgrep -f '[s]trata --serve' | head -1)
echo "--- engine env ---"
[ -n "${E:-}" ] && tr '\0' '\n' < "/proc/$E/environ" | grep -E 'STRATA_SM75_PERF|STRATA_IQ_MT_MIN' | sort
echo "--- check ---"
python3 /opt/ai/bin/t2check.py "$LABEL" "$REPS"
