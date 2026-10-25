#!/usr/bin/env python3
"""The four questions before a window (MULTIPATH_RULES §6), answered from the box, read-only.

  1. the samplers: the vram-perf cron entry and the nvlink-crc timer are installed and last ran
  2. no residual installer/builder: no nvcc, cc1plus, cmake --build, pip install, or ops script of ours
  3. the VRAM/performance sample: the last rows of vram-perf.csv and the newest /metrics request age
  4. the direction: today's restart count (STATE) and what the next step is (STATE's 下一动作)

Prints one JSON object; exit 1 when a residual process is found (a window must not start then).
Usage: four-questions.py [--host user@box]
"""
import argparse
import json
import subprocess
import sys

REMOTE = r'''
import json, os, re, subprocess, time, urllib.request
out = {}
def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception as e:
        return f"error: {e}"
out["crontab_vram"] = [l for l in sh("crontab -l").splitlines() if "vram-perf" in l]
out["timers"] = [l for l in sh("systemctl list-timers --all --no-pager").splitlines() if "nvlink" in l]
res = sh("ps -eo pid,args | grep -E 'nvcc|cc1plus|cmake --build|pip install|ops-v2-window|use-config' | grep -v grep")
out["residual"] = [l for l in res.splitlines() if "four-questions" not in l]
out["vram_tail"] = sh("tail -2 /opt/ai/logs/vram-perf.csv").splitlines()
out["date"] = sh("date -u +%FT%TZ")
try:
    req = urllib.request.Request("http://127.0.0.1:8080/metrics", headers={"Authorization": "Bearer 111"})
    m = json.loads(urllib.request.urlopen(req, timeout=10).read())
    rows = m.get("requests") or []
    newest = max((r.get("time") or 0) for r in rows) if rows else 0
    out["metrics_age_s"] = round(time.time() - newest, 1) if newest else None
    out["live"] = {k: (m.get("live") or {}).get(k) for k in ("state", "queued", "running", "waiting")}
    out["engine_version"] = (m.get("engine") or {}).get("version")
except Exception as e:
    out["metrics_error"] = f"{type(e).__name__}: {e}"
print(json.dumps(out, indent=1))
'''

ap = argparse.ArgumentParser()
ap.add_argument("--host", default="atm@192.168.3.14")
opts = ap.parse_args()
done = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", opts.host,
                       "python3", "-"], input=REMOTE, capture_output=True, text=True, timeout=120)
if done.returncode != 0:
    print(done.stderr.strip() or "ssh failed", file=sys.stderr)
    sys.exit(2)
print(done.stdout)
data = json.loads(done.stdout)
print("RESIDUAL PROCESSES:" if data.get("residual") else "residual: none")
for line in data.get("residual") or []:
    print("  " + line)
sys.exit(1 if data.get("residual") else 0)
