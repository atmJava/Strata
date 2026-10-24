#!/usr/bin/env python3
"""One cold 8K prompt, then print the engine lines it produced (read time vs prefill timeline)."""
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

text = open(CORPUS, encoding="utf-8", errors="replace").read()
start = (int(uuid.uuid4().hex[:6], 16) * 7) % max(1, len(text) - CHARS)
prompt = text[start:start + CHARS]

before = sum(1 for _ in open(LOG, encoding="utf-8", errors="replace"))
body = {"model": MODEL, "temperature": 0, "max_tokens": 8,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [{"role": "user", "content": f"[{uuid.uuid4().hex[:8]}] " + prompt}]}
req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers=AUTH)
t0 = time.perf_counter()
with urllib.request.urlopen(req, timeout=1800) as r:
    d = json.loads(r.read().decode())
wall = time.perf_counter() - t0
print(f"client wall {wall:.2f} s, prompt {d['usage']['prompt_tokens']} tokens")
time.sleep(1.0)
lines = open(LOG, encoding="utf-8", errors="replace").read().splitlines()
for line in lines[before:]:
    if "prefill timing" in line or " read in " in line or "strata batch: read" in line:
        print(" |", line[:400])
