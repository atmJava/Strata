#!/usr/bin/env python3
"""tools/sim_client.py - the fixed-load client this fork is judged with (AGENTS.md).

DSH's traffic on this box is 1-5 conversations, each compacted at about 80% of a 200K window, a typical prompt 15K-75K
tokens and a short answer.  That shape is what the conversation cache, the KV format and admission are tuned for, so a
change is judged by running the same shape again and reading the same table - not by a probe sent at a live session.

What it does: opens --sessions conversations (the default mix is 160K, 80K, 30K, 30K, 30K tokens of history), sends
--turns user messages to each, every session in its own thread so they overlap the way subagents do, and records what
the server reports for each request from /metrics (prompt_total, reused, prompt_read, prompt_ms, decode_ms,
first_engine_token_s).  The prompts are generated from a seed, so two runs of the same command send the same text.

    python tools/sim_client.py --sessions 5 --turns 3 --context-mix 160000,80000,30000,30000,30000
    python tools/sim_client.py --dry-run              # print the plan and the assertions, send nothing

Exit code 0 when every assertion holds, 1 otherwise, so it can gate a build:
  * from the second turn on, a conversation reuses at least --min-reuse of its prompt (default 0.9): the previous
    turn's KV was still parked, which is the whole point of the cache;
  * no request re-reads more than --max-read tokens (default 8000): a whole-conversation read is the failure this
    measures;
  * with several sessions the slowest mean decode rate is at least --min-decode-ratio of the fastest (default 0.7),
    which is what batching is supposed to cost.

It never changes the server, never restarts it and never writes to it: it is a client, and the only thing it needs is
the address, the key and the model name (from --config, or the flags).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import threading
import time
import urllib.request

# 4 characters of this filler text are about one token for the Qwen3.8 tokenizer, so a target of 160K tokens is about
# 640K characters.  It is an approximation on purpose: the table reports what the server actually read, which is the
# number the assertions use.
CHARS_PER_TOKEN = 4.0
WORDS = ("harbour ledger signal meadow furnace lattice orbit canvas thimble quarry lantern pasture circuit "
         "magnet willow compass rafter beacon cistern drifter gantry hollow ingot juniper kettle").split()


def filler(words: int, seed: int) -> str:
    """A deterministic block of text: the same (words, seed) gives the same string on every run and machine."""
    rng = random.Random(seed)
    out = []
    for i in range(words):
        out.append(rng.choice(WORDS))
        if i % 12 == 11:
            out.append("\n")
    return " ".join(out)


def conversation(session: int, context_tokens: int, turns: int) -> list[dict]:
    """The messages of one conversation: a system prompt shared by every session (as DSH's is), then the history the
    session starts with, then the user messages its turns append.  Deterministic from the session number."""
    history_words = max(1, int(context_tokens / CHARS_PER_TOKEN))
    system = ("You are a coding agent inside a workspace. You answer briefly, and you use the tools you are given "
              "rather than describing them.")
    body = filler(history_words, seed=1000 + session)
    msgs = [{"role": "system", "content": system},
            {"role": "user", "content": "Read the log below and keep it in mind.\n\n" + body},
            {"role": "assistant", "content": "Read. What should I do with it?"}]
    for turn in range(turns):
        msgs.append({"role": "user", "content": f"Turn {turn + 1}: name one thing in the log, in one short sentence."})
        msgs.append({"role": "assistant", "content": "(pending)"})
    return msgs


def request_body(model: str, messages: list[dict], turn: int, max_tokens: int) -> dict:
    """The messages up to `turn`: the trailing placeholder answer of this turn is dropped, so the request is what a
    client sends before the model answers."""
    cut = messages[:3 + 2 * turn]
    return {"model": model, "max_tokens": max_tokens, "temperature": 0, "stream": False,
            "messages": cut + [messages[3 + 2 * turn]]}


class Client:
    def __init__(self, base_url: str, api_key: str, timeout: float):
        self.base = base_url.rstrip("/")
        self.key = api_key
        self.timeout = timeout
        self.claimed: set[str] = set()
        self.claim_lock = threading.Lock()

    def _open(self, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + self.key})
        with urllib.request.urlopen(req, timeout=self.timeout) as f:
            return json.loads(f.read())

    def metrics(self) -> dict:
        return self._open("/metrics")

    def chat(self, body: dict) -> dict:
        return self._open("/v1/chat/completions", body)

    def row(self, started: float, prompt_tokens: int) -> dict:
        """The /metrics row of this request: the newest finished one since `started` with that prompt length that no
        other thread has claimed (the box may also be serving a live session)."""
        for row in reversed(self.metrics().get("requests") or []):
            if row.get("time", 0) < started or row.get("prompt_total") != prompt_tokens:
                continue
            rid = str(row.get("id"))
            with self.claim_lock:
                if rid in self.claimed:
                    continue
                self.claimed.add(rid)
            return row
        return {}


def run_session(client: Client, model: str, session: int, context_tokens: int, turns: int, max_tokens: int,
                rows: list, errors: list) -> None:
    messages = conversation(session, context_tokens, turns)
    for turn in range(turns):
        body = request_body(model, messages, turn, max_tokens)
        started = time.time()
        try:
            reply = client.chat(body)
        except Exception as exc:                       # a refused request is a result, not a crash
            errors.append(f"session {session} turn {turn + 1}: {exc}")
            return
        usage = reply.get("usage") or {}
        text = ((reply.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        row = client.row(started, int(usage.get("prompt_tokens") or 0))
        rows.append({"session": session, "turn": turn + 1, "prompt": row.get("prompt_total"),
                     "reused": row.get("reused"), "read": row.get("prompt_read"),
                     "prefill_s": round((row.get("prompt_ms") or 0) / 1000, 1),
                     "decode_s": round((row.get("decode_ms") or 0) / 1000, 1),
                     "tok_s": row.get("decode_tok_s"), "first_s": row.get("first_engine_token_s"),
                     "wall_s": round(time.time() - started, 1), "chars": len(text)})
        # the answer this turn really produced becomes the history the next turn reads
        messages[3 + 2 * turn + 1] = {"role": "assistant", "content": text or "(empty)"}


def summary(rows: list, errors: list, args) -> tuple[list[str], list[str]]:
    """The verdict: (passed, failed) sentences.  Kept apart, as the repo's reports keep them."""
    passed, failed = [], []
    late = [r for r in rows if r["turn"] >= 2 and r["prompt"]]
    if not late:
        failed.append("no turn 2 or later completed, so reuse could not be measured")
    else:
        ratio = [r["reused"] / r["prompt"] for r in late if r["reused"] is not None]
        if not ratio:
            failed.append("the server did not report reused for any late turn (an engine before 0.1.36?)")
        elif min(ratio) >= args.min_reuse:
            passed.append(f"reuse on late turns: min {min(ratio):.2f}, mean {sum(ratio) / len(ratio):.2f} "
                          f"(>= {args.min_reuse})")
        else:
            worst = min(late, key=lambda r: (r["reused"] or 0) / (r["prompt"] or 1))
            failed.append(f"reuse on late turns: min {min(ratio):.2f} (>= {args.min_reuse}); worst is session "
                          f"{worst['session']} turn {worst['turn']} reuse {worst['reused']} of {worst['prompt']}")
    reads = [r for r in rows if r["read"] is not None]
    if reads:
        worst = max(reads, key=lambda r: r["read"])
        if worst["read"] <= args.max_read:
            passed.append(f"largest prompt read: {worst['read']} tokens (<= {args.max_read})")
        else:
            failed.append(f"largest prompt read: {worst['read']} tokens by session {worst['session']} turn "
                          f"{worst['turn']} (> {args.max_read})")
    rates = [(r["session"], r["tok_s"]) for r in rows if r["tok_s"]]
    if rates and args.sessions > 1:
        by_session = {}
        for session, rate in rates:
            by_session.setdefault(session, []).append(rate)
        means = {s: sum(v) / len(v) for s, v in by_session.items() if v}
        if means:
            slowest, fastest = min(means.values()), max(means.values())
            got = slowest / fastest if fastest else 0.0
            if got >= args.min_decode_ratio:
                passed.append(f"concurrent decode kept {got:.2f} of the fastest session's rate "
                              f"(>= {args.min_decode_ratio})")
            else:
                failed.append(f"concurrent decode kept only {got:.2f} of the fastest session's rate "
                              f"({slowest:.1f} vs {fastest:.1f} tok/s, >= {args.min_decode_ratio})")
    for e in errors:
        failed.append(e)
    return passed, failed


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="the fixed-load client this fork is judged with",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="/opt/ai/config/strata-swift-iq3s-trial.json",
                    help="the run config to take the address, key and model from")
    ap.add_argument("--base-url", default=None)
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--sessions", type=int, default=5)
    ap.add_argument("--turns", type=int, default=3)
    ap.add_argument("--context-mix", default="160000,80000,30000,30000,30000",
                    help="starting history per session, in tokens (one entry per session)")
    ap.add_argument("--max-tokens", type=int, default=24)
    ap.add_argument("--min-reuse", type=float, default=0.9)
    ap.add_argument("--max-read", type=int, default=8000)
    ap.add_argument("--min-decode-ratio", type=float, default=0.7)
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("--dry-run", action="store_true", help="print the plan and send nothing")
    args = ap.parse_args(argv)

    if not (args.base_url and args.api_key is not None and args.model):
        cfg = {}
        if args.config:
            if not os.path.exists(args.config):
                print(f"sim_client: no run config at {args.config}; give --base-url, --api-key and --model",
                      file=sys.stderr)
                return 2
            with open(args.config, encoding="utf-8") as f:
                cfg = json.load(f)
        if not args.base_url:
            # a config that listens on 0.0.0.0 is reached on the loopback address, not on the wildcard
            host = cfg.get("host")
            host = host if isinstance(host, str) and host not in ("", "0.0.0.0", "::") else "127.0.0.1"
            args.base_url = f"http://{host}:{int(cfg.get('port') or 8080)}"
        if args.api_key is None:
            key = cfg.get("api_key")
            if not isinstance(key, str) or not key:      # api_key can be a flag in a config, not a secret
                print("sim_client: the run config has no usable api_key; give --api-key", file=sys.stderr)
                return 2
            args.api_key = key
        if not args.model:
            args.model = cfg.get("model_name")
        if not args.model:
            print("sim_client: the run config names no model; give --model", file=sys.stderr)
            return 2
    mix = [int(x) for x in str(args.context_mix).split(",") if x.strip()]
    if not mix:
        print("sim_client: --context-mix is empty", file=sys.stderr)
        return 2
    mix = (mix * args.sessions)[:args.sessions]
    print(f"sim_client: {args.sessions} sessions, {args.turns} turns, history {mix}, model {args.model}, "
          f"{args.base_url}")
    if args.dry_run:
        for s, ctx in enumerate(mix):
            print(f"  session {s}: {ctx} tokens of history, {args.turns} turns")
        print("assertions: reuse >= %.2f on turn 2+, read <= %d, decode ratio >= %.2f"
              % (args.min_reuse, args.max_read, args.min_decode_ratio))
        return 0

    client = Client(args.base_url, args.api_key, args.timeout)
    rows, errors = [], []
    threads = [threading.Thread(target=run_session,
                                args=(client, args.model, s, ctx, args.turns, args.max_tokens, rows, errors),
                                name=f"sim-{s}") for s, ctx in enumerate(mix)]
    started = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rows.sort(key=lambda r: (r["turn"], r["session"]))

    print("sess turn   prompt   reused    read  prefill   decode  tok/s   first   wall")
    for r in rows:
        print(f"{r['session']:>4} {r['turn']:>4} {str(r['prompt']):>8} {str(r['reused']):>8} "
              f"{str(r['read']):>7} {r['prefill_s']:>8} {r['decode_s']:>7} {str(r['tok_s']):>6} "
              f"{str(r['first_s']):>7} {r['wall_s']:>6}")
    passed, failed = summary(rows, errors, args)
    print(f"\n{time.time() - started:.0f}s, {len(rows)} requests")
    for p in passed:
        print("  PASS  " + p)
    for f_ in failed:
        print("  FAIL  " + f_)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
