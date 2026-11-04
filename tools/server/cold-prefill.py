#!/usr/bin/env python3
"""Cold prefill arm: a prompt the engine has never seen, so every token is read.

Measures the engine's own line:
  strata serve: prompt N tokens = 0 reused + N read in M ms (X tok/s)

Usage: cold_prefill.py <label> [target_tokens] [reps]
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
import uuid

BASE = "http://127.0.0.1:8080"
AUTH = {"Authorization": "Bearer 111", "Content-Type": "application/json"}
LOG = "/opt/ai/src/strata-0.2.2/strata-022.log"
MODEL = "qwen3.8-flash-next-iq3_xxs-abliterated"

LABEL = sys.argv[1] if len(sys.argv) > 1 else "run"
TARGET = int(sys.argv[2]) if len(sys.argv) > 2 else 13252
REPS = int(sys.argv[3]) if len(sys.argv) > 3 else 3

LINE = re.compile(r"prompt (\d+) tokens = (\d+) reused \+ (\d+) read in (\d+) ms \(([\d.]+) tok/s\)")


def cold_prompt(tag: str, tokens: int) -> str:
    # ~3.2 characters per token for this filler; the reported count is what is used.
    words = [f"{tag}{i:06d}" for i in range(tokens // 4)]
    return "Read the notes below, then answer in one short sentence.\n" + " ".join(words)


def send(prompt: str, max_tokens: int = 8) -> dict:
    body = {
        "model": MODEL,
        "temperature": 0,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(BASE + "/v1/chat/completions",
                                 data=json.dumps(body).encode(), headers=AUTH)
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=1800) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    return {"wall": time.perf_counter() - t0, "usage": d.get("usage", {})}


def last_line() -> str:
    import subprocess
    out = subprocess.run(["grep", "-a", "read in", LOG], capture_output=True, text=True).stdout
    rows = [l for l in out.splitlines() if LINE.search(l)]
    return rows[-1] if rows else ""


print(f"  cold prefill  label={LABEL}  target≈{TARGET} tokens  reps={REPS}")
rates = []
for i in range(1, REPS + 1):
    tag = "p" + uuid.uuid4().hex[:8]
    r = send(cold_prompt(tag, TARGET))
    pt = r["usage"].get("prompt_tokens")
    line = last_line()
    m = LINE.search(line)
    if m and int(m.group(3)) > TARGET // 2:
        rate = float(m.group(5))
        reused = int(m.group(2))
        if reused == 0:
            rates.append(rate)
            print(f"  {i:>2}  {pt} tok  {int(m.group(4))/1000:.2f} s  {rate:.0f} tok/s")
        else:
            print(f"  {i:>2}  {pt} tok  reused {reused} — cache hit, skipped")
    else:
        print(f"  {i:>2}  {pt} tok  (no engine line; wall {r['wall']:.1f}s)")

if rates:
    rates.sort()
    med = rates[len(rates) // 2]
    print(f"  prefill median: {med:.0f} tok/s   ({len(rates)} valid: {[round(x) for x in rates]})")
    with open("/opt/ai/logs/cold-prefill.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"label": LABEL, "target": TARGET, "rates": rates, "median": med}) + "\n")
else:
    print("  no valid sample")
