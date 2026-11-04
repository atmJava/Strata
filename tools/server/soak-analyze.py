#!/usr/bin/env python3
"""Summarise a soak log: request count/errors, RSS and MemAvailable drift, GPU power/temperature.

Usage: soak-analyze.py <log>
"""
import re
import sys

path = sys.argv[1]
lines = open(path, encoding="utf-8", errors="replace").read().splitlines()

req = err = 0
samples = []
times = []
for line in lines:
    if " req=" in line:
        req += 1
        if "ERROR" in line:
            err += 1
        m = re.match(r"(\d\d:\d\d:\d\d) req=(\d+)", line)
        if m:
            times.append(m.group(1))
    elif " sample " in line:
        m = re.search(r"rss_mib=(\d+) mem_avail_mib=(\d+) gpu=\[([^\]]*)\]", line)
        if m:
            t = line.split()[0]
            gpus = []
            for g in m.group(3).split("|"):
                parts = [p.strip() for p in g.split(",")]
                if len(parts) >= 4:
                    gpus.append({"idx": parts[0], "w": float(parts[1]), "t": float(parts[2]), "mem": int(parts[3])})
            samples.append({"t": t, "rss": int(m.group(1)), "avail": int(m.group(2)), "gpus": gpus})

print(f"requests: {req}  errors: {err}")
if times:
    print(f"first request {times[0]}  last request {times[-1]}")
if samples:
    s0, s1 = samples[0], samples[-1]
    print(f"samples: {len(samples)}  ({s0['t']} .. {s1['t']})")
    print(f"RSS MiB     first {s0['rss']:>9} last {s1['rss']:>9} drift {s1['rss']-s0['rss']:+}")
    av = [s["avail"] for s in samples]
    print(f"MemAvailMiB first {s0['avail']:>9} last {s1['avail']:>9} min {min(av)} max {max(av)}")
    for i in range(min(len(s0["gpus"]), 2)):
        ws = [s["gpus"][i]["w"] for s in samples if i < len(s["gpus"])]
        ts = [s["gpus"][i]["t"] for s in samples if i < len(s["gpus"])]
        ms = [s["gpus"][i]["mem"] for s in samples if i < len(s["gpus"])]
        print(f"GPU{i}: power {min(ws):.0f}-{max(ws):.0f} W (last {ws[-1]:.0f}); temp {min(ts):.0f}-{max(ts):.0f} C "
              f"(last {ts[-1]:.0f}); mem {min(ms)}-{max(ms)} MiB")
    # temperature trend in thirds
    n = len(samples)
    for i in range(min(len(s0["gpus"]), 2)):
        third = max(1, n // 3)
        a = sum(s["gpus"][i]["t"] for s in samples[:third]) / third
        b = sum(s["gpus"][i]["t"] for s in samples[-third:]) / third
        print(f"GPU{i}: temp first third {a:.1f} C, last third {b:.1f} C")
