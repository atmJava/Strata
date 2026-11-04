#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Conversation-cache A/B: alternate two long conversations and report, per request, how much
of the prompt the engine actually had to read.

The engine keeps ONE conversation in its session.  Sending conversation B after A therefore
throws A away -- unless `--conversation-cache-mib` parks it.  Conversations are matched by
exact token prefix (no session id), so two fixed prompt bodies are enough to model a
controller/worker alternation.

Plan:
    A fresh          first request, nothing to reuse
    A control        same conversation again -> the ordinary prompt cache (must be ~100%)
    B fresh          switching conversation
    A switch         <- the measurement: ~0% without the cache, ~100% with it
    B switch
    A switch
    B switch

Usage:  conv_cache_ab.py <label> [outdir]
Env:    STRATA_URL, STRATA_MODEL, CONV_LINES (default 600 -> ~18k prompt tokens)
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("STRATA_URL", "http://127.0.0.1:8080")
MODEL = os.environ.get("STRATA_MODEL", "qwen3.8-flash-next-iq2_xs")

SENT = ("Alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima "
        "mike november oscar papa quebec romeo sierra tango uniform victor whiskey "
        "xray yankee zulu. The report summarises the current state of the pipeline "
        "and lists every outstanding item with its owner and its due date. ")


def body(tag, n):
    return "\n".join("[%s-%04d] %s" % (tag, i, SENT) for i in range(n))


def messages(tag, n):
    return [{"role": "user",
             "content": ("Conversation %s. Read the log below, then answer with the "
                         "single word READY.\n\n" % tag) + body(tag, n)}]


def get(path, timeout=60):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def chat(msgs, max_tokens=4, timeout=3600):
    payload = {"model": MODEL, "messages": msgs, "max_tokens": max_tokens,
               "stream": False, "temperature": 0.0}
    req = urllib.request.Request(BASE + "/v1/chat/completions",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return {"error": "HTTP %s: %s" % (e.code,
                                          e.read().decode("utf-8", "replace")[:400])}, time.time() - t0
    except Exception as e:                                    # noqa: BLE001 - report, do not abort the run
        return {"error": "%s: %s" % (type(e).__name__, e)}, time.time() - t0
    return d, time.time() - t0


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "run"
    outdir = sys.argv[2] if len(sys.argv) > 2 else "/opt/ai/logs"
    n = int(os.environ.get("CONV_LINES", "600"))

    msgs = {"A": messages("ALPHA", n), "B": messages("BRAVO", n)}
    plan = [("A", "fresh"), ("A", "control"), ("B", "fresh"),
            ("A", "switch"), ("B", "switch"), ("A", "switch"), ("B", "switch")]

    cc0 = get("/metrics").get("conversation_cache", {})
    print("start: enabled=%s budget_mib=%s slots=%s" % (cc0.get("enabled"), cc0.get("budget_mib"),
                                                        cc0.get("slots")))
    print("%-3s %-5s %-8s %8s %9s %8s %7s  %s" %
          ("#", "conv", "kind", "secs", "prompt", "cached", "reuse%", "cache state"))
    rows = []
    for i, (tag, kind) in enumerate(plan, 1):
        resp, secs = chat(msgs[tag])
        try:
            cc = get("/metrics").get("conversation_cache", {})
        except Exception:                                     # noqa: BLE001
            cc = {}
        usage = (resp or {}).get("usage") or {}
        det = usage.get("prompt_tokens_details") or {}
        prompt = usage.get("prompt_tokens")
        cached = det.get("cached_tokens")
        if cached is None:                                    # fall back to the engine's own counter
            cached = cc.get("last_reused")
            prompt = prompt or cc.get("last_prompt")
        pct = (100.0 * cached / prompt) if (prompt and cached is not None) else None
        state = "parked=%s parks=%s restores=%s evict=%s last=%s(%s)" % (
            cc.get("parked"), cc.get("parks"), cc.get("restores"), cc.get("evictions"),
            cc.get("last_event"), cc.get("last_tokens"))
        print("%-3d %-5s %-8s %8.2f %9s %8s %7s  %s" %
              (i, tag, kind, secs, prompt, cached,
               "-" if pct is None else "%.1f" % pct, state))
        if resp.get("error"):
            print("    ERROR: %s" % resp["error"])
        rows.append({"i": i, "conv": tag, "kind": kind, "secs": round(secs, 3),
                     "prompt_tokens": prompt, "cached_tokens": cached,
                     "reuse_pct": None if pct is None else round(pct, 2),
                     "completion_tokens": usage.get("completion_tokens"),
                     "finish": ((resp.get("choices") or [{}])[0].get("finish_reason")),
                     "error": resp.get("error"),
                     "cache": {k: cc.get(k) for k in
                               ("enabled", "budget_mib", "slots", "parked", "bytes",
                                "evictions", "parks", "restores", "last_event",
                                "last_tokens", "last_at")}})
        sys.stdout.flush()

    cc1 = get("/metrics").get("conversation_cache", {})
    out = {"label": label, "lines": n, "rows": rows, "start": cc0, "end": cc1}
    path = os.path.join(outdir, "conv-cache-%s.json" % label)
    with open(path, "w") as f:
        json.dump(out, f, indent=1)
    print("\nwrote %s" % path)

    # the switches are the measurement
    sw = [r for r in rows if r["kind"] == "switch" and r["reuse_pct"] is not None]
    if sw:
        print("switch reuse: mean %.1f%%  min %.1f%%  max %.1f%%   (n=%d)" %
              (sum(r["reuse_pct"] for r in sw) / len(sw),
               min(r["reuse_pct"] for r in sw), max(r["reuse_pct"] for r in sw), len(sw)))


if __name__ == "__main__":
    main()
