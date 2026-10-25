#!/usr/bin/env python3
"""V2-4: the first requests after a restart - the cold/warm warmup series (live server).

Sends N identical short prompts one after another and prints per-request time-to-first-token and
decode speed: the first one pays for whatever the load left cold (page cache, kernel selection,
the expert cache's first touches).  This is a measurement, not a threshold: it passes when every
request is answered and the series is printed.

Usage: exp-v2-warmup.py [base_url] [requests]
"""
import json
import sys
import time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.3.14:8080"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 5
if not BASE.startswith("http"):
    print(__doc__, file=sys.stderr)
    sys.exit(2)
KEY = "111"
MODEL = "qwen3.8-flash-next-iq3_xxs"
PROMPT = ("请逐条列出这五行文字里的数字：甲 3、乙 7、丙 11、丁 19、戊 23。"
          "然后用一句话说明它们是否都是质数。")


def once():
    body = {"model": MODEL, "messages": [{"role": "user", "content": PROMPT}], "max_tokens": 64,
            "temperature": 0, "reasoning_effort": "none", "stream": True}
    req = urllib.request.Request(BASE + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + KEY})
    started = time.time()
    first = None
    chunks = 0
    with urllib.request.urlopen(req, timeout=300) as response:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            chunks += 1
            if first is None and chunks > 1:
                first = time.time() - started
    total = time.time() - started
    return {"ttft_s": round(first, 3) if first else None, "total_s": round(total, 3)}


rows = []
failures = 0
for i in range(N):
    try:
        row = once()
    except Exception as error:  # noqa: BLE001 - a failed request is a result
        failures += 1
        row = {"error": f"{type(error).__name__}: {error}"}
    row["request"] = i + 1
    rows.append(row)

print(json.dumps({"base": BASE, "requests": rows, "failures": failures}, indent=1))
sys.exit(0 if failures == 0 else 1)
