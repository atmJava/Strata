#!/usr/bin/env python3
"""V2-1: the staged read runs, and a cancelled reader leaves nothing behind (live server).

Two checks, both against the running server:

1. `probe_cancel_staged.py` (S0d's live scenario): six long decodes fill the six slots (parallel=6 in
   this config; four used to be enough when parallel was 4), a cold ~22K-token read joins - that is when
   the server stages it instead of taking a slot - and the client goes away.  Its JSON is judged here, not
   by eye: the neighbours kept decoding after the close (gap < 2 s, tokens > 0) and the server is still
   healthy.  Whether the reader itself got a token first is printed, not required: in the waiting-for-a-slot
   case it is closed before one arrives, and that is the case this drill is about.
2. a plain ~22K-token read with a short answer completes (the staged path's happy case), and a short
   request right after it is served inside 30 s (no wedged slot).

Exit 0 only when every check passes.  Prints one JSON object.
Usage: exp-v2-staged-drill.py [base_url] [neighbours]      (neighbours = parallel slots to fill, default 6)
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.3.14:8080"
if not BASE.startswith("http"):
    print(__doc__, file=sys.stderr)
    sys.exit(2)
N = sys.argv[2] if len(sys.argv) > 2 else "6"
KEY = "111"
MODEL = "qwen3.8-flash-next-iq3_xxs"
PROBE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", "probe_cancel_staged.py")
CORPUS = "/tmp/corpus.md"

LONG_PROMPT = (open(CORPUS, encoding="utf-8").read()
               + "\n\n请用一句话回答：以上文档的第一个小标题是什么？")
SHORT_PROMPT = "用一句话说明什么是 MoE。"


def chat(text, max_tokens, stream=False, timeout=600):
    body = {"model": MODEL, "messages": [{"role": "user", "content": text}], "max_tokens": max_tokens,
            "temperature": 0, "reasoning_effort": "none", "stream": stream}
    req = urllib.request.Request(BASE + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + KEY})
    started = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
        return time.time() - started, json.loads(raw)


def health():
    with urllib.request.urlopen(BASE + "/health", timeout=15) as response:
        return response.status


def main():
    out = {"base": BASE, "checks": {}}
    ok = True

    # 1. the cancel drill (its own scenario, judged here)
    started = time.time()
    try:
        done = subprocess.run([sys.executable, PROBE, BASE, N], capture_output=True, text=True, timeout=900)
        probe = json.loads(done.stdout)
    except Exception as error:  # noqa: BLE001 - a drill that will not run is a failed drill
        out["checks"]["cancel_drill"] = {"pass": False, "error": f"{type(error).__name__}: {error}"}
        ok = False
    else:
        gap = probe.get("neighbour_gap_after_close_s")
        after = probe.get("neighbour_tokens_after_close")
        passed = (probe.get("health_after") == 200
                  and isinstance(gap, (int, float)) and gap < 2.0
                  and isinstance(after, int) and after > 0)
        out["checks"]["cancel_drill"] = {"pass": passed, "seconds": round(time.time() - started, 1),
                                         "reader_got_first_token": probe.get("reader_got_first_token"),
                                         "probe": probe}
        ok = ok and passed

    # 2. the happy path, then a short request
    try:
        seconds, body = chat(LONG_PROMPT, 16)
        text = (body.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        long_ok = bool(text.strip())
        out["checks"]["long_read"] = {"pass": long_ok, "seconds": round(seconds, 2),
                                      "prompt_tokens": body.get("usage", {}).get("prompt_tokens")}
        ok = ok and long_ok
    except Exception as error:  # noqa: BLE001
        out["checks"]["long_read"] = {"pass": False, "error": f"{type(error).__name__}: {error}"}
        ok = False

    try:
        started = time.time()
        seconds, body = chat(SHORT_PROMPT, 16)
        text = (body.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        short_ok = bool(text.strip()) and seconds < 30
        out["checks"]["short_after"] = {"pass": short_ok, "seconds": round(seconds, 2)}
        ok = ok and short_ok
    except Exception as error:  # noqa: BLE001
        out["checks"]["short_after"] = {"pass": False, "error": f"{type(error).__name__}: {error}"}
        ok = False

    try:
        out["checks"]["health"] = {"pass": health() == 200}
    except Exception as error:  # noqa: BLE001
        out["checks"]["health"] = {"pass": False, "error": f"{type(error).__name__}: {error}"}
    ok = ok and out["checks"]["health"]["pass"]

    out["pass"] = ok
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
