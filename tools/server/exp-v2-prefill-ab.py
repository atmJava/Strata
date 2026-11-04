#!/usr/bin/env python3
"""V2b arm probe: the prompt-side cost of one ~22K-token read, N times, with the server's own numbers.

One long read per run (the shape the fused prefill kernels serve: many streamed experts in a prompt);
for each run it records the client wall time and the server's /metrics entry for that request
(prompt_ms, prompt_tokens, prompt_read, first_engine_token_s).  The A/B is: run this whole script under
one arm (env value), restart into the other arm, run it again, and compare with exp-v2-ab-summary.py.

Usage: exp-v2-prefill-ab.py [base_url] [runs] [corpus_file] [--tag TEXT]

--tag puts the same text in front of every prompt.  Leave it empty for the parity check: the two arms
then read the identical prompt, and the greedy answer text (and its sha256, printed per row) must match.
"""
import hashlib
import json
import sys
import time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.3.14:8080"
RUNS = int(sys.argv[2]) if len(sys.argv) > 2 else 3
CORPUS = sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith("--") else "/tmp/corpus.md"
TAG = ""
if "--tag" in sys.argv:
    TAG = sys.argv[sys.argv.index("--tag") + 1]
if not BASE.startswith("http"):
    print(__doc__, file=sys.stderr)
    sys.exit(2)
KEY = "111"
MODEL = "qwen3.8-flash-next-iq3_xxs"
corpus = open(CORPUS, encoding="utf-8").read()


def metrics():
    req = urllib.request.Request(BASE + "/metrics", headers={"Authorization": "Bearer " + KEY})
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read())


def read_once(tag):
    prompt = f"（A/B {tag}）\n" + corpus + "\n\n请用一句话回答：开头第一个标题是什么？"
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 16,
            "temperature": 0, "reasoning_effort": "none"}
    req = urllib.request.Request(BASE + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + KEY})
    started = time.time()
    with urllib.request.urlopen(req, timeout=600) as response:
        payload = json.loads(response.read())
    wall = round(time.time() - started, 3)
    text = (payload.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    return wall, text.strip()


rows = []
failures = 0
for i in range(RUNS):
    try:
        wall, text = read_once(TAG or f"第 {i + 1} 次")
    except Exception as error:  # noqa: BLE001
        failures += 1
        rows.append({"run": i + 1, "error": f"{type(error).__name__}: {error}"})
        continue
    entry = {}
    try:
        newest = max(metrics().get("requests") or [], key=lambda r: r.get("time") or 0)
        entry = {k: newest.get(k) for k in ("prompt_ms", "prompt_tokens", "prompt_read", "first_engine_token_s",
                                            "decode_ms", "duration_s", "finish", "id")}
    except Exception as error:  # noqa: BLE001 - metrics is a side channel; the wall time still counts
        entry = {"metrics_error": f"{type(error).__name__}: {error}"}
    rows.append({"run": i + 1, "wall_s": wall, "chars": len(text),
                 "text_sha256": hashlib.sha256(text.encode()).hexdigest(), "text": text[:120], **entry})

print(json.dumps({"base": BASE, "corpus": CORPUS, "tag": TAG, "rows": rows, "failures": failures}, indent=1))
sys.exit(0 if failures == 0 else 1)
