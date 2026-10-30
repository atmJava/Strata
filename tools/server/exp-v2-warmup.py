#!/usr/bin/env python3
"""V2-4: the warmup claim, measured on the live server - the first long read after a restart stalls the
neighbours for ~10 s, the second reads in 0.6 s, and warmup.py spends the first one on nothing.

The probe runs one neighbour decode (streaming, 360 tokens) and, while it decodes, two calls to the same
~22K-token read `warmup.py` uses.  For each read it prints the wall time and the neighbour's largest
inter-token gap during that window.  This is the V2-4 measurement, not a threshold: it passes when both
reads are answered and the numbers are printed.  The instrument's own cost (1.37 us/request) was measured
in V0 (ledger L-056).

Usage: exp-v2-warmup.py [base_url] [corpus_file]
"""
import json
import os
import sys
import threading
import time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.9.103:8080"
CORPUS = sys.argv[2] if len(sys.argv) > 2 else "/tmp/corpus.md"
if not BASE.startswith("http"):
    print(__doc__, file=sys.stderr)
    sys.exit(2)
KEY = "111"
MODEL = "qwen3.8-flash-next-iq3_xxs"
corpus = open(CORPUS, encoding="utf-8").read()
READ = f"（预热 {time.time_ns()}）\n" + corpus + "\n\n请用一句话回答：开头第一个标题是什么？"
NEIGHBOUR = "请写一段大约 600 字的说明文，逐层解释一个混合专家模型一次前向都做了什么。"


def read_once():
    body = {"model": MODEL, "messages": [{"role": "user", "content": READ}], "max_tokens": 16,
            "temperature": 0, "reasoning_effort": "none"}
    req = urllib.request.Request(BASE + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + KEY})
    started = time.time()
    with urllib.request.urlopen(req, timeout=600) as response:
        payload = json.loads(response.read())
    text = (payload.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    return {"wall_s": round(time.time() - started, 2), "chars": len(text.strip())}


def neighbour(times, errors):
    body = {"model": MODEL, "messages": [{"role": "user", "content": NEIGHBOUR}], "max_tokens": 600,
            "temperature": 0, "reasoning_effort": "none", "stream": True}
    req = urllib.request.Request(BASE + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + KEY})
    try:
        with urllib.request.urlopen(req, timeout=600) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("data:"):
                    times.append(time.time())
    except Exception as error:  # noqa: BLE001 - the neighbour ending early is a result
        errors.append(f"{type(error).__name__}: {error}")


def gap_starting_in(times, lo, hi):
    """The largest gap whose first token lies in [lo, hi+2s]: the stall a read causes starts during it."""
    pairs = [(a, b) for a, b in zip(times, times[1:]) if lo <= a <= hi + 2.0]
    return round(max((b - a for a, b in pairs), default=0.0), 2)


times, errors = [], []
worker = threading.Thread(target=neighbour, args=(times, errors))
worker.start()
time.sleep(2.0)                       # the neighbour is decoding now

reads = []
for i in range(2):
    before = len(times)
    lo = time.time()
    try:
        row = read_once()
    except Exception as error:  # noqa: BLE001
        row = {"error": f"{type(error).__name__}: {error}"}
    hi = time.time()
    row["_window"] = (lo, hi)
    row["neighbour_tokens_in_window"] = len(times) - before
    row["read"] = i + 1
    reads.append(row)

worker.join(timeout=600)
for row in reads:
    if "_window" in row:
        lo, hi = row.pop("_window")
        row["neighbour_gap_s"] = gap_starting_in(times, lo, hi)
print(json.dumps({"base": BASE, "reads": reads, "neighbour_error": errors[0] if errors else None,
                  "neighbour_tokens": len(times)}, indent=1))
ok = all("error" not in row for row in reads) and len(times) > 0
sys.exit(0 if ok else 1)
