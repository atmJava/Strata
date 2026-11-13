#!/bin/sh
set -eu

cd /opt/ai/src/strata-two-stream-serial-20261031

# the server's own decisions are code defaults now (AGENTS.md); this launcher sets only what the process needs
export PYTHONUNBUFFERED=1

exec /opt/ai/venvs/test-027/bin/python -u strata.py serve \
    --config /opt/ai/etc/strata-production.json
