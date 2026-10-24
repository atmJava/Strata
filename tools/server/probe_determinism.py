#!/usr/bin/env python3
"""Is the run-to-run spread sampling, or the engine?

The self-scoring eval's header says it runs greedy because it sends no temperature / top_p / top_k.
That is only true if the server does not fall back to its configured sampling when a request leaves
those out - and this box's config has sampling {temperature 0.6, top_p 0.95, top_k 20}. Two baseline
runs of that eval, same boot, same config, differed by 24.5% in per-task completion tokens
(docs/EVIDENCE_LEDGER.md L-002), which is what an un-pinned sampler looks like.

So: send one fixed prompt N times, twice over - once omitting the sampling keys the way the eval does,
once with temperature 0 pinned - and compare completion tokens and the answer's sha256. If the pinned
arm gives one identical hash and one token count, the spread is sampling and a greedy A/B is cheap.
If it still varies, the engine really is non-reproducible in this shape and every comparison needs reps.

Usage: probe_determinism.py [reps] [mode: omit|greedy|both]   (default 3 both)
"""
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8080"
CFG = "/opt/ai/src/strata-0.2.2/strata-022.json"
MODEL = "qwen3.8-flash-next-iq3_xxs-abliterated"

# A prompt long enough to make thinking vary: the eval's two Chinese prose tasks were the only ones
# whose length swung between runs, so use the same shape.
PROMPT = (
    "用中文解释：一块长期运行的服务器磁盘可用空间突然减少，通常有哪些原因？"
    "请分点说明每类原因如何确认，以及对应的处理办法。"
)


def key():
    try:
        return json.load(open(CFG)).get("api_key", "")
    except Exception:
        return os.environ.get("QE_KEY", "")


def ask(prompt, mode, budget=0):
    payload = {"model": MODEL, "messages": [{"role": "user", "content": prompt}],
               "max_tokens": 2200, "reasoning_effort": "high", "stream": True}
    if mode == "greedy":
        # Pinned: temperature 0 is the one knob every server honours. top_p / top_k left out on purpose
        # so we learn whether the server's own defaults leak in around an explicit temperature.
        payload["temperature"] = 0
    if budget:
        payload["reasoning_budget_tokens"] = budget
    body = json.dumps(payload).encode()
    req = urllib.request.Request(BASE + "/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + key()})
    t0 = time.time()
    reasoning = []
    content = []
    usage = {}
    finish = None
    with urllib.request.urlopen(req, timeout=900) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            if obj.get("usage"):
                usage = obj["usage"]
            for ch in obj.get("choices") or []:
                d = ch.get("delta") or {}
                if d.get("reasoning_content"):
                    reasoning.append(d["reasoning_content"])
                if d.get("content"):
                    content.append(d["content"])
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
    return {"secs": round(time.time() - t0, 2), "finish": finish,
            "completion_tokens": usage.get("completion_tokens"),
            "rsnCh": len("".join(reasoning)), "ansCh": len("".join(content)),
            "sha": hashlib.sha256("".join(content).encode()).hexdigest()[:16]}


def main():
    reps = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    modes = ["omit", "greedy"]
    if len(sys.argv) > 2 and sys.argv[2] != "both":
        modes = [sys.argv[2]]
    api = subprocess.run(["python3", "-c",
                          "import json;print(json.load(open('%s')).get('sampling'))" % CFG],
                         capture_output=True, text=True).stdout.strip()
    print("server sampling config (applied when a request omits the keys?):", api)
    out = pathlib.Path("/opt/ai/runs/probe-determinism.json")
    results = {}
    for mode in modes:
        rows = [ask(PROMPT, mode) for _ in range(reps)]
        results[mode] = rows
        shas = {r["sha"] for r in rows}
        toks = [r["completion_tokens"] for r in rows]
        print("\n=== mode %s, %d reps ===" % (mode, reps))
        for i, r in enumerate(rows, 1):
            print("  rep %d  finish=%s  compl=%s  rsnCh=%d  ansCh=%d  %5.1fs  sha=%s"
                  % (i, r["finish"], r["completion_tokens"], r["rsnCh"], r["ansCh"], r["secs"], r["sha"]))
        print("  distinct answer hashes: %d   token counts: %s" % (len(shas), toks))
        if len(toks) > 1 and min(toks):
            print("  token spread: %.1f%%" % ((max(toks) - min(toks)) / statistics_mean(toks) * 100))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1))
    print("\nsaved", out)


def statistics_mean(xs):
    return sum(xs) / len(xs)


if __name__ == "__main__":
    main()
