#!/bin/bash
# Restore the production config and restart, then verify what is running.
set -u
cd /opt/ai/src/strata-0.2.2 || exit 1
cp -p strata-022.prod.json strata-022.json
echo "restored strata-022.prod.json -> strata-022.json"
echo 'p99832586' | sudo -S systemctl restart strata.service 2>/dev/null
echo "restart rc=$?"
for i in $(seq 1 60); do
  H=$(curl -s -m 5 http://127.0.0.1:8080/health 2>/dev/null)
  case "$H" in *'"loaded": true'*) echo "READY after ~$((i*3))s"; break;; esac
  sleep 3
done
curl -s -m 8 http://127.0.0.1:8080/health; echo
E=$(pgrep -f '[s]trata --serve' | head -1)
echo "engine pid=${E:-none}"
[ -n "${E:-}" ] && tr '\0' '\n' < "/proc/$E/environ" | grep -E 'STRATA_SM75_PERF|STRATA_IQ_MT_MIN|STRATA_KV_PREFETCH' | sort
[ -n "${E:-}" ] && tr '\0' ' ' < "/proc/$E/cmdline" | grep -o -- '--parallel [0-9]*\|--batch [0-9]*\|--batch-groups [0-9]*\|--spec [0-9]*\|--pcie-frac [0-9.]*' | tr '\n' ' '
echo
python3 -c "import json;c=json.load(open('strata-022.json'));print('parallel:',c.get('parallel'))"
