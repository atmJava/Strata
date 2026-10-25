#!/usr/bin/env python3
"""V2-5: the VRAM account after the install - what the six-slot config actually holds (live server).

Reads /metrics (hardware + engine blocks) and nvidia-smi, prints the per-GPU used/total, the engine's
own arena/expert-cache numbers and the free reserve.  A measurement, not a threshold: it passes when
the server answers and both cards are reported.

Usage: exp-v2-vram.py [base_url]
"""
import json
import subprocess
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.3.14:8080"
if not BASE.startswith("http"):
    print(__doc__, file=sys.stderr)
    sys.exit(2)
KEY = "111"

out = {"base": BASE}
try:
    req = urllib.request.Request(BASE + "/metrics", headers={"Authorization": "Bearer " + KEY})
    with urllib.request.urlopen(req, timeout=20) as response:
        metrics = json.loads(response.read())
except Exception as error:  # noqa: BLE001
    print(json.dumps({"base": BASE, "error": f"{type(error).__name__}: {error}"}, indent=1))
    sys.exit(1)

engine = metrics.get("engine", {})
hardware = metrics.get("hardware", {})
out["engine"] = {k: engine.get(k) for k in (
    "version", "batch_slots", "expert_slots", "expert_cache_mib", "arena_mib", "vram_free_mib",
    "max_context", "kv", "kv_resident", "prefill_stages")}
out["gpus"] = hardware.get("gpus")
out["gpu_mem_used"] = hardware.get("gpu_mem_used")
out["gpu_mem_total"] = hardware.get("gpu_mem_total")
out["ram_used"] = hardware.get("ram_used")
out["slots"] = (metrics.get("live") or {}).get("slots")

try:
    smi = subprocess.run(["nvidia-smi", "--query-gpu=index,memory.used,memory.total",
                          "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=20)
    out["nvidia_smi"] = smi.stdout.strip().splitlines()
except Exception as error:  # noqa: BLE001
    out["nvidia_smi_error"] = f"{type(error).__name__}: {error}"

ok = isinstance(out.get("gpus"), list) and len(out["gpus"]) == 2
out["pass"] = ok
print(json.dumps(out, indent=1))
sys.exit(0 if ok else 1)
