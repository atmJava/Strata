#!/usr/bin/env python3
"""S0d live check: cancel a staged read in the window where it has read its prompt and waits for a slot.

The read runs in the main session; when it is done the server emits the prefill token and then waits for a
decoding slot (`holding = False` in `serve/server.py`'s staged branch).  Neighbours are longer than the read
(2500 tokens ~ 33 s vs a 22.7K read ~ 17 s) so no slot frees during the check: any adoption would be visible
as a neighbour finishing early.

Output: one JSON object, plus the reader's fate.  Run: probe_cancel_wait.py [base] [neighbours]
"""
import json
import sys
import threading
import time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.9.103:8080"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 6
KEY = "111"
MODEL = "qwen3.8-flash-next-iq3_xxs"
CORPUS = open("/tmp/corpus.md", encoding="utf-8").read()
NEIGH_BODY = ("写一篇大约 2500 字的技术说明，逐节解释：MoE 路由、专家容量、KV 缓存、投机解码、"
              "批处理、显存分级、PCIe 带宽、页缓存。每一节都要给出数字。")
READER = f"（等槽窗口 {time.time_ns()}）\n" + CORPUS + "\n\n请用一句话回答：开头第一个标题是什么？"


def post(text, max_tokens, stream):
    body = {"model": MODEL, "messages": [{"role": "user", "content": text}], "max_tokens": max_tokens,
            "temperature": 0, "reasoning_effort": "none", "stream": stream}
    request = urllib.request.Request(BASE + "/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + KEY})
    return urllib.request.urlopen(request, timeout=900)


def neighbour(out):
    out["times"] = []
    try:
        with post(NEIGH_BODY, 2500, True) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = (json.loads(data).get("choices") or [{}])[0].get("delta") or {}
                except Exception:  # noqa: BLE001
                    continue
                if delta.get("content"):
                    out["times"].append(time.time())
    except Exception as error:  # noqa: BLE001
        out["error"] = f"{type(error).__name__}: {error}"
    out["done"] = time.time()


def gaps(times, lo):
    picked = [t for t in times if t >= lo]
    return max((b - a for a, b in zip(picked, picked[1:])), default=0.0)


def health():
    with urllib.request.urlopen(BASE + "/health", timeout=15) as response:
        return response.status


neigh = [{} for _ in range(N)]
threads = [threading.Thread(target=neighbour, args=(neigh[i],)) for i in range(N)]
for t in threads:
    t.start()
time.sleep(2.0)                                   # the neighbours are decoding

read = {}
reader_out = {}


def reader():
    reader_out["times"] = []
    reader_out["start"] = time.time()
    try:
        with post(READER, 16, True) as response:
            reader_out["resp"] = response
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = (json.loads(data).get("choices") or [{}])[0].get("delta") or {}
                except Exception:  # noqa: BLE001
                    continue
                if delta.get("content"):
                    reader_out["times"].append(time.time())
    except Exception as error:  # noqa: BLE001
        reader_out["error"] = f"{type(error).__name__}: {error}"


reader_thread = threading.Thread(target=reader)
reader_thread.start()
deadline = time.time() + 120
while time.time() < deadline and not reader_out.get("times"):
    time.sleep(0.1)
first_at = time.time()
read_done_at = first_at                           # the prefill token marks the end of the read
# the reader now waits for a slot (holding=False): go away here
if reader_out.get("resp") is not None:
    # a real hang-up: close() alone leaves the TCP socket open while the reader thread holds it (S7's lesson),
    # and the server never sees an EOF.
    try:
        raw = getattr(getattr(reader_out["resp"], "fp", None), "raw", None)
        sock = getattr(raw, "_sock", None)
        if sock is not None:
            import socket as _socket
            sock.shutdown(_socket.SHUT_RDWR)
    except OSError:
        pass
    reader_out["resp"].close()
for t in threads:
    t.join()
reader_thread.join(timeout=20)
result = {
    "neighbours": N,
    "reader_first_token_s": round(reader_out["times"][0] - reader_out["start"], 2) if reader_out.get("times") else None,
    "reader_tokens": len(reader_out.get("times", [])),
    "reader_error_after_close": reader_out.get("error"),
    "neighbour_gap_max_s": round(max(gaps(n["times"], 0.0) for n in neigh), 2),
    "neighbour_gap_after_read_done_s": round(max(gaps(n["times"], read_done_at) for n in neigh), 2),
    "neighbour_tokens_after_read_done": sum(1 for n in neigh for t in n["times"] if t >= read_done_at),
    "neighbour_errors": [n.get("error") for n in neigh if n.get("error")],
    "health_after": health(),
}
print(json.dumps(result, indent=1))
