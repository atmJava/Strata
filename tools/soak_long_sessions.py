#!/usr/bin/env python3
"""Long-session concurrency soak against a live Strata server's HTTP path.

Every session grows a conversation turn by turn (the prefix is reused, as in a
real long session); several sessions run at the same time through a barrier.
Deterministic text: a fixed seed gives the same words on every run, so two
builds are compared by reading the same table.

Usage (on the machine that serves, so the network stays out of the numbers):

    python tools/soak_long_sessions.py --sessions 3 --turns 8 \
        --start-words 2048 --growth-words 1024 --max-tokens 64

Rows go to --out (jsonl) and the summary is printed: per turn the engine's own
timings (cache_n = prompt tokens served from cache, prompt_ms, ttft_ms,
predicted_per_second) and the client's wall time.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
import urllib.request

VOCAB = ("river stone market lantern copper winter garden signal harbor needle orbit velvet canyon ember "
         "ledger quartz meadow anchor violet thunder archive bridge cipher delta fabric glacier helmet "
         "island journal kernel lattice mirror nectar object packet quiver ribbon shelter timber vector "
         "harbor canyon ledger meadow orbit quartz shelter thunder velvet winter copper ember fabric").split()


def make_words(n: int, seed: int) -> str:
    """`n` deterministic words (prose-like: no pathological tokenization)."""
    import random
    rng = random.Random(seed)
    return " ".join(rng.choice(VOCAB) for _ in range(n))


def call(url, key, model, messages, max_tokens):
    body = json.dumps({"model": model, "messages": messages, "max_tokens": max_tokens,
                       "temperature": 0, "seed": 7}).encode()
    req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + key})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=900) as r:
        data = json.load(r)
    wall_ms = 1000.0 * (time.time() - t0)
    return data, wall_ms


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8080")
    ap.add_argument("--key", default="111")
    ap.add_argument("--model", default="swift-1.5-qwen3.8-flash-next-iq3_s")
    ap.add_argument("--sessions", type=int, default=3)
    ap.add_argument("--turns", type=int, default=8)
    ap.add_argument("--start-words", type=int, default=2048, help="first prompt, words (~tokens x1/1.3 for prose)")
    ap.add_argument("--growth-words", type=int, default=1024, help="words appended per turn")
    ap.add_argument("--max-tokens", type=int, default=64)
    ap.add_argument("--seed", type=int, default=20261114)
    ap.add_argument("--out", default="soak-long-sessions.jsonl")
    args = ap.parse_args()

    rows = []
    lock = threading.Lock()
    barrier = threading.Barrier(args.sessions)
    errors = []

    def session(sid: int) -> None:
        try:
            msgs = [{"role": "system", "content": "You are a careful engineer. Answer briefly."}]
            for turn in range(args.turns):
                words = args.start_words + args.growth_words * turn
                msgs.append({"role": "user", "content": f"Turn {turn}. Continue. " + make_words(words, args.seed + sid * 1000 + turn)})
                barrier.wait()
                data, wall = call(args.url, args.key, args.model, msgs, args.max_tokens)
                u = data.get("usage", {})
                t = data.get("timings", {})
                m = data.get("metrics", {})
                row = {"session": sid, "turn": turn,
                       "prompt_tokens": u.get("prompt_tokens"),
                       "cached_tokens": (u.get("prompt_tokens_details") or {}).get("cached_tokens"),
                       "prompt_ms": t.get("prompt_ms"), "predicted_per_second": t.get("predicted_per_second"),
                       "draft_n": t.get("draft_n"), "draft_accepted": t.get("draft_n_accepted"),
                       "ttft_ms": m.get("ttft_ms"), "itl_ms_mean": m.get("itl_ms_mean"), "wall_ms": round(wall)}
                with lock:
                    rows.append(row)
                reply = (data["choices"][0].get("message") or {}).get("content") or ""
                msgs.append({"role": "assistant", "content": reply or "(length)"})
        except Exception as e:  # noqa: BLE001
            with lock:
                errors.append((sid, repr(e)[:200]))

    threads = [threading.Thread(target=session, args=(i,), daemon=True) for i in range(args.sessions)]
    t_all = time.time()
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    total_s = time.time() - t_all

    with open(args.out, "w", encoding="utf-8") as f:
        for r in sorted(rows, key=lambda r: (r["session"], r["turn"])):
            f.write(json.dumps(r) + "\n")

    print(f"{'sess':>4} {'turn':>4} {'prompt':>8} {'cached':>8} {'prompt_ms':>9} {'ttft_ms':>7} "
          f"{'tok/s':>7} {'drafts':>11} {'wall_ms':>7}")
    for r in sorted(rows, key=lambda r: (r["session"], r["turn"])):
        ttft = r["ttft_ms"] if r["ttft_ms"] is not None else -1
        print(f"{r['session']:>4} {r['turn']:>4} {r['prompt_tokens']:>8} {r['cached_tokens']:>8} "
              f"{r['prompt_ms']:>9.1f} {ttft:>7} {r['predicted_per_second']:>7.1f} "
              f"{r['draft_accepted']:>4}/{r['draft_n']:<6} {r['wall_ms']:>7}")
    if rows:
        dec = [r["predicted_per_second"] for r in rows if r["predicted_per_second"]]
        print(f"\nsessions={args.sessions} turns={args.turns} total={total_s:.1f}s rows={len(rows)} "
              f"decode_median={statistics.median(dec):.1f} tok/s")
        cached = sum((r["cached_tokens"] or 0) for r in rows) / max(1, sum((r["prompt_tokens"] or 0) for r in rows))
        print(f"cache_reuse={cached:.3f} of prompt tokens (sum cached / sum prompt)")
    if errors:
        print("errors:", errors, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
