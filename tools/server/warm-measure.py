#!/usr/bin/env python3
"""Warm the server, then measure cold reads and print the engine's own split.

Usage: warm-measure.py [chars] [reps]
"""
import json
import re
import sys
import time
import urllib.request
import uuid

LOG = "/opt/ai/src/strata-0.2.2/strata-022.log"
CORPUS = "/opt/ai/docs/strata-parameters-0.2.2-20261022.md"
URL = "http://127.0.0.1:8080/v1/chat/completions"
AUTH = {"Authorization": "Bearer 111", "Content-Type": "application/json"}
MODEL = "qwen3.8-flash-next-iq3_xxs-abliterated"
CHARS = int(sys.argv[1]) if len(sys.argv) > 1 else 12000
REPS = int(sys.argv[2]) if len(sys.argv) > 2 else 3

text = open(CORPUS, encoding="utf-8", errors="replace").read()

TL = re.compile(r"timing: (\d+) tokens, GPU timeline (\d+) ms, wall (\d+) ms, host staging (\d+) ms")
HOST = re.compile(r"chunk setup \(PLE rows, the expert stream plan\) (\d+) ms, waiting for each chunk (\d+) ms, "
                  r"after each chunk \(the draft layer, progress\) (\d+) ms, PLE (\d+) ms")


def send(prompt: str, max_tokens: int) -> tuple:
    body = {"model": MODEL, "temperature": 0, "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers=AUTH)
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=1800) as r:
        d = json.loads(r.read().decode())
    return time.perf_counter() - t0, d["usage"]["prompt_tokens"]


def log_len() -> int:
    return sum(1 for _ in open(LOG, encoding="utf-8", errors="replace"))


print("warming ...")
for i in range(6):
    send(f"Reply with one word: OK ({uuid.uuid4().hex[:4]})", 8)

for rep in range(1, REPS + 1):
    start = (int(uuid.uuid4().hex[:6], 16) * 7) % max(1, len(text) - CHARS)
    prompt = text[start:start + CHARS]
    before = log_len()
    t0 = time.perf_counter()
    wall, pt = send(f"[{uuid.uuid4().hex[:8]}] " + prompt, 8)
    time.sleep(0.8)
    lines = open(LOG, encoding="utf-8", errors="replace").read().splitlines()[before:]
    tl = [TL.search(l) for l in lines if TL.search(l)]
    host = [HOST.search(l) for l in lines if HOST.search(l)]
    print(f"\nrep {rep}: prompt={pt} client_wall={wall:.2f}s")
    for m in tl:
        print(f"  timing: {m.group(1)} tok, GPU {m.group(2)} ms, wall {m.group(3)} ms, host staging {m.group(4)} ms")
    for m in host:
        print(f"  host: setup {m.group(1)} ms, wait-chunks {m.group(2)} ms, after-chunk {m.group(3)} ms, PLE {m.group(4)} ms")
