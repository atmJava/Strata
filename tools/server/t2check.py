#!/usr/bin/env python3
"""T2 arm: the same greedy prompt twice; prints one sha256 per run.

Usage: t2check.py <label> [reps]
"""
import hashlib
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8080"
AUTH = {"Authorization": "Bearer 111", "Content-Type": "application/json"}
MODEL = "qwen3.8-flash-next-iq3_xxs-abliterated"
LABEL = sys.argv[1] if len(sys.argv) > 1 else "run"
REPS = int(sys.argv[2]) if len(sys.argv) > 2 else 2

PARA = ("Routing in a mixture-of-experts layer keeps the top few experts per token; the gated combination is "
        "what the network learns to weight. A router that is trained without a load-balancing term collapses "
        "onto a handful of experts, so the auxiliary loss matters as much as the routing itself. ")
PROMPT = ("Read the note and explain mixture-of-experts routing in detail.\n" + PARA * 12)


def run() -> dict:
    body = {"model": MODEL, "temperature": 0, "top_p": 1, "max_tokens": 512,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": PROMPT}]}
    req = urllib.request.Request(BASE + "/v1/chat/completions",
                                 data=json.dumps(body).encode(), headers=AUTH)
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=1800) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    text = d["choices"][0].get("message", {}).get("content") or ""
    return {"sha": hashlib.sha256(text.encode()).hexdigest(),
            "wall": round(time.perf_counter() - t0, 1),
            "prompt_tokens": d.get("usage", {}).get("prompt_tokens"),
            "timings": d.get("timings", {}),
            "head": text[:60], "tail": text[-60:]}


print(f"  T2 arm {LABEL}: {REPS} runs, greedy, 512 tokens")
shas = []
for i in range(1, REPS + 1):
    r = run()
    shas.append(r["sha"])
    print(f"  {i}: {r['sha'][:16]}  prompt={r['prompt_tokens']}  wall={r['wall']}s  "
          f"decode={r['timings'].get('predicted_per_second')}")
    print(f"     head: {r['head']!r}")
print(f"  same within arm: {len(set(shas)) == 1}  ({len(set(shas))} distinct)")
