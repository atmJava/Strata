#!/usr/bin/env python3
"""Build the T2 (bitwise) run config from the production one.

Usage: prepare-t2.py <on|off>   -- writes strata-022.t2.json and prints its env.

What changes vs strata-022.json:
  parallel 1                 one slot, so the solo path is used
  --batch-groups 1           groups need at least two slots
  --spec 0 --suffix-draft 0  no drafts: their count depends on timing
  --pcie-frac 0              the PCIe share is chosen per window
  --adapt-every 1000000      the VRAM tier is fixed
  --no-prefill-borrow        the prompt path lends no expert slot
  STRATA_IQ_MT_MIN=1         multi-token CPU expert kernels for every group
  STRATA_SM75_PERF=1 when "on" (removed when "off")
"""
import json
import pathlib
import sys

SRC = pathlib.Path("/opt/ai/src/strata-0.2.2/strata-022.json")
PROD = pathlib.Path("/opt/ai/src/strata-0.2.2/strata-022.prod.json")
if PROD.exists():
    SRC = PROD                       # build every arm from the untouched production config
DST = pathlib.Path("/opt/ai/src/strata-0.2.2/strata-022.t2.json")
arm = sys.argv[1] if len(sys.argv) > 1 else "off"

cfg = json.loads(SRC.read_text(encoding="utf-8-sig"))
cfg["parallel"] = 1

args = list(cfg.get("args", []))


def drop(flag: str) -> None:
    while flag in args:
        i = args.index(flag)
        if i + 1 < len(args) and not args[i + 1].startswith("--"):
            del args[i:i + 2]
        else:
            del args[i]


for f in ("--pcie-frac", "--adapt-every", "--batch-groups"):
    drop(f)
drop("--no-prefill-borrow")

# --spec stays: a native pack requires --spec T with T >= 2. Greedy verification of
# a draft cannot change the token stream, so the comparison stays exact.
args += ["--pcie-frac", "0", "--adapt-every", "1000000", "--batch-groups", "1", "--no-prefill-borrow"]
cfg["args"] = args

env = dict(cfg.get("env") or {})
env["STRATA_IQ_MT_MIN"] = "1"
if arm == "on":
    env["STRATA_SM75_PERF"] = "1"
else:
    env.pop("STRATA_SM75_PERF", None)
cfg["env"] = env

DST.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
print("wrote", DST)
print("parallel:", cfg["parallel"])
print("env:", json.dumps(env, sort_keys=True))
