#!/usr/bin/env python3
"""Cancel probe: 4 long decodes fill the slots, a cold 22.7K read joins, then the reader goes away.

S0d's live check: when a staged read that waits for a slot is cancelled, the neighbours must keep
decoding (no lost tokens, no stall) and the server must stay healthy.  The reader is closed right
after its first token - the read is done and it waits for a slot, i.e. `holding = False` in the
server, the exact window the fix is about.  Prints one JSON object.

Usage: probe_cancel_staged.py [base_url] [neighbours]
"""
import json
import sys
import threading
import time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.9.103:8080"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 4
KEY = "111"
MODEL = "qwen3.8-flash-next-iq3_xxs"
CORPUS = open("/tmp/corpus.md", encoding="utf-8").read()
PROMPT = "写一篇大约 1200 字的说明文，解释混合专家（MoE）模型的路由机制、负载均衡与专家容量。"
READER = CORPUS + f"\n\n（取消探针 {time.time_ns()}）请用一句话回答：以上文档开头第一个标题是什么？"


def stream(text, max_tokens):
    body = {"model": MODEL, "messages": [{"role": "user", "content": text}], "max_tokens": max_tokens,
            "temperature": 0, "reasoning_effort": "none", "stream": True}
    req = urllib.request.Request(BASE + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + KEY})
    return urllib.request.urlopen(req, timeout=600)


def run(text, max_tokens, out):
    out["times"] = []
    try:
        with stream(text, max_tokens) as response:
            out["resp"] = response
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
                if delta.get("content"):
                    out["times"].append(time.time())
    except Exception as error:  # noqa: BLE001 - a closed stream is an outcome, not a crash
        out["error"] = f"{type(error).__name__}: {error}"
    out["done"] = time.time()


def gaps(times, lo):
    picked = [t for t in times if t >= lo]
    return max((b - a for a, b in zip(picked, picked[1:])), default=0.0)


def health():
    with urllib.request.urlopen(BASE + "/health", timeout=15) as response:
        return response.status


neigh = [{} for _ in range(N)]
threads = [threading.Thread(target=run, args=(PROMPT, 1200, neigh[i])) for i in range(N)]
for t in threads:
    t.start()
time.sleep(2.0)                                  # the neighbours are decoding now

read = {}
reader = threading.Thread(target=run, args=(READER, 16, read))
reader.start()
deadline = time.time() + 90
while time.time() < deadline and not read.get("times"):
    time.sleep(0.1)
closed_at = time.time()
if read.get("resp") is not None:
    read["resp"].close()                         # the client goes away while the read waits for a slot
time.sleep(15.0)                                 # a heartbeat makes the server notice the disconnect
for t in threads:
    t.join()
reader.join(timeout=10)
print(json.dumps({
    "reader_got_first_token": bool(read.get("times")),
    "reader_error_after_close": read.get("error"),
    "reader_tokens": len(read.get("times", [])),
    "neighbour_gap_max_s": round(max(gaps(n["times"], 0.0) for n in neigh), 2),
    "neighbour_gap_after_close_s": round(max(gaps(n["times"], closed_at) for n in neigh), 2),
    "neighbour_tokens_after_close": sum(1 for n in neigh for t in n["times"] if t >= closed_at),
    "health_after": health(),
}, indent=1))
