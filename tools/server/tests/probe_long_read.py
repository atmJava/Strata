#!/usr/bin/env python3
"""Long-read probe: N long decodes fill the slots, then a cold ~22.7K-token read joins.

Measures the reader's first/second/total, the neighbours' totals, and - the important part -
the neighbours' worst token gap *while the reader is being read*, which is what a staged read
must not blow up (a 17 s read that pauses every decode by 17 s is not "multi-path usable").

Prints one JSON object.  Usage: probe_long_read.py [base_url] [N]
"""
import json
import sys
import threading
import time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.3.14:8080"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 4
KEY = "111"
MODEL = "qwen3.8-flash-next-iq3_xxs"
CORPUS = open("/tmp/corpus.md", encoding="utf-8").read()
PROMPT = "写一篇大约 1200 字的说明文，解释混合专家（MoE）模型的路由机制、负载均衡与专家容量。"
NONCE = str(time.time_ns())
READER = CORPUS + f"\n\n（本次探针唯一标识 {NONCE}）请用一句话回答：以上文档开头第一个标题是什么？"


def open_stream(text, max_tokens):
    body = {"model": MODEL, "messages": [{"role": "user", "content": text}], "max_tokens": max_tokens,
            "temperature": 0, "reasoning_effort": "none", "stream": True}
    req = urllib.request.Request(BASE + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + KEY})
    return urllib.request.urlopen(req, timeout=600)


def run(text, max_tokens, out):
    start = time.time()          # wall clock: gaps are compared across streams
    first = second = None
    chars = 0
    times: list[float] = []
    with open_stream(text, max_tokens) as response:
        for raw in response:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                delta = (json.loads(data).get("choices") or [{}])[0].get("delta") or {}
            except Exception:
                continue
            content = delta.get("content")
            if not content:
                continue
            now = time.time()
            if first is None:
                first = now - start
            elif second is None:
                second = now - start
            chars += len(content)
            times.append(now)
    out.update(first=first, second=second, total=time.time() - start, chars=chars, start=start, times=times)


def worst_gap(times, lo, hi):
    picked = [t for t in times if lo <= t <= hi]
    return max((b - a for a, b in zip(picked, picked[1:])), default=0.0)


def busy_slots():
    request = urllib.request.Request(BASE + "/metrics", headers={"Authorization": "Bearer " + KEY})
    with urllib.request.urlopen(request, timeout=30) as response:
        live = json.load(response)["live"]
    return [s.get("state", "?") for s in live.get("slots", [])]


neigh = [{} for _ in range(N)]
threads = [threading.Thread(target=run, args=(PROMPT, 1200, neigh[i])) for i in range(N)]
for t in threads:
    t.start()
deadline = time.time() + 30
states = busy_slots()
while time.time() < deadline and sum(1 for s in states if s != "idle") < N:
    time.sleep(0.2)
    states = busy_slots()
busy = sum(1 for s in states if s != "idle")

read = {}
reader = threading.Thread(target=run, args=(READER, 16, read))
reader.start()
while read.get("total") is None:
    time.sleep(0.05)
for t in threads + [reader]:
    t.join()

reader_from = read["start"]
reader_to = read["start"] + (read.get("second") or read["total"])
gaps = [worst_gap(n["times"], reader_from, reader_to) for n in neigh]
overall = [worst_gap(n["times"], n["start"], n["start"] + n["total"]) for n in neigh]
print(json.dumps({
    "slots_busy": busy, "slots": states, "streams": N,
    "reader_first_s": round(read["first"], 2) if read.get("first") else None,
    "reader_second_s": round(read["second"], 2) if read.get("second") else None,
    "reader_total_s": round(read["total"], 2),
    "reader_chars": read["chars"],
    "neighbour_total_max_s": round(max(n["total"] for n in neigh), 2),
    "neighbour_gap_max_in_reader_window_s": round(max(gaps), 2),
    "neighbour_gap_max_overall_s": round(max(overall), 2),
}, indent=1))
