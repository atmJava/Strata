#!/bin/sh
set -eu

cd /opt/ai/src/strata-two-stream-serial-20261031

export STRATA_ADMIT_SHORT_FIRST=2048
export STRATA_DEFER_TITLE_REQUESTS=1
export STRATA_MP_STAGED_READ_MIN_TOKENS=2048
export STRATA_PIN_DSH_TOOL_PREFIX=1
export PYTHONUNBUFFERED=1

exec /opt/ai/venvs/test-027/bin/python -u strata.py serve \
    --config /opt/ai/etc/strata-production.json
