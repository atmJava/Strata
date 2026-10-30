#!/usr/bin/env python3
"""Warm the engine after a restart: one long read so the first real request is not the cold one.

The first long read after a restart stalls the neighbours for ~10 s (the per-window host time goes
from 0.3 ms to ~32 ms while the expert and page caches fill); the second read on is 0.6 s.  Running
this right after `use-config.sh` spends that one cold read on nothing.

Usage: warmup.py [base_url] [corpus_file]
"""
import json
import os
import sys
import time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.9.103:8080"
CORPUS = sys.argv[2] if len(sys.argv) > 2 else "/tmp/corpus.md"
KEY = os.environ.get("STRATA_KEY", "111")
MODEL = os.environ.get("STRATA_MODEL", "qwen3.8-flash-next-iq3_xxs")
corpus = open(CORPUS, encoding="utf-8").read()
prompt = f"（预热 {time.time_ns()}）\n" + corpus + "\n\n请用一句话回答：开头第一个标题是什么？"
body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 16,
        "temperature": 0, "reasoning_effort": "none"}
request = urllib.request.Request(BASE + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + KEY})
start = time.time()
with urllib.request.urlopen(request, timeout=600) as response:
    answer = json.loads(response.read())
print(json.dumps({"wall_s": round(time.time() - start, 1), "chars": len(answer["choices"][0]["message"].get("content") or "")}))
