#!/usr/bin/env python3
"""R5 long-read probe: four long decodes fill the slots, then a 22.7K-token cold read joins.

Measures the reader's first token (TTFT), second token (the stall a slot must fill), total wall,
and the neighbours' totals.  Prints one JSON object.
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
NONCE = str(__import__("time").time_ns())
READER = CORPUS + f"\n\n（本次探针唯一标识 {NONCE}）请用一句话回答：以上文档开头第一个标题是什么？"


def open_stream(text, max_tokens):
    body = {"model": MODEL, "messages": [{"role": "user", "content": text}], "max_tokens": max_tokens,
            "temperature": 0, "reasoning_effort": "none", "stream": True}
    req = urllib.request.Request(BASE + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + KEY})
    return urllib.request.urlopen(req, timeout=600)


def run(text, max_tokens, out):
    t0 = time.time()
    first = second = None
    chars = 0
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
            now = time.time() - t0
            if first is None:
                first = now
            elif second is None:
                second = now
            chars += len(content)
    out.update(first=first, second=second, total=time.time() - t0, chars=chars)


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
threading.Thread(target=run, args=(READER, 16, read)).start()
while read.get("total") is None:
    time.sleep(0.05)
for t in threads:
    t.join()
print(json.dumps({
    "slots_busy": busy, "slots": states,
    "reader_prompt_tokens_hint": "~22.7K",
    "reader_first_s": round(read["first"], 2) if read.get("first") else None,
    "reader_second_s": round(read["second"], 2) if read.get("second") else None,
    "reader_total_s": round(read["total"], 2),
    "reader_chars": read["chars"],
    "neighbour_total_max_s": round(max(n["total"] for n in neigh), 2),
}, indent=1))
