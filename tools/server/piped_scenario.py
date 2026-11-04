#!/usr/bin/env python3
"""Coexistence scenario: one stream decodes while another reads a cold ~127K-token prompt.

This is the scenario the concurrent prompt read exists for, and the shape the production-shape
A-B-A of STRATA_PIPED_CONCURRENT has to be measured in (L-015: on an otherwise idle box 97% of the
concurrent reads helped nobody, with a second stream alive that falls to 46% and the slots advance a
median of 57 rows per read - so switching the gate on an idle box measures the idle box, not the gate).

The original script for this lived in /tmp/piped_scenario_127k.py and produced L-004.  It is gone
(confirmed 2026-10-24), which is exactly the failure mode L-004 warned about, so this version lives
in git.  Run once on 2026-10-24 (L-019): the engine-side evidence was sound, but stream A read the wrong JSON
field (choices[0]["content"] instead of choices[0]["delta"]["content"]) and printed "A 0 tok" for every rep,
so the neighbour-progress columns were empty.  Fixed the same day, along with a `solo` mode that runs the same
decode stream with nobody reading beside it, which is the control the coexist numbers need.

What it reports per rep:
  - B: the cold read - client wall, and the engine's own "prompt N tokens = R reused + K read in M ms"
  - the engine's "read K tokens on a thread of its own in M ms (concurrent, ...); the slots decoded
    N rows meanwhile" lines that belong to B, which is the gate's own accounting of what the other
    stream got done while B's bytes were arriving
  - A: tokens/second over the window B was reading, and A's longest gap between streamed chunks
    (a stall a user would feel)

Usage: piped_scenario.py [reps] [chars-for-B]
"""
from __future__ import annotations

import json
import os
import re
import statistics
import sys
import threading
import time
import urllib.request
import uuid

BASE = "http://127.0.0.1:8080"
URL = BASE + "/v1/chat/completions"
METRICS = BASE + "/metrics"
AUTH = {"Authorization": "Bearer 111", "Content-Type": "application/json"}
MODEL = "qwen3.8-flash-next-iq3_xxs-abliterated"
LOG = "/opt/ai/src/strata-0.2.2/strata-022.log"
CORPUS = os.environ.get("MP_CORPUS") or "/opt/ai/docs/archive/strata-parameters-0.2.2-20261022.md"

PROMPT_A = os.environ.get(
    "PEER_PROMPT",
    "请写一篇约 2000 个汉字的技术说明，主题是混合专家模型的路由、容量因子、负载均衡与尾延迟。"
    "要求分为 10 节，每节至少 8 句，连续写满，不要提前结束。",
)
PROMPT_B_TOKENS = 127_000

READ_LINE = re.compile(
    r"read (\d+) tokens on a thread of its own in (\d+) ms \(concurrent, to (\d+) of (\d+)\);"
    r" the slots decoded (\d+) rows meanwhile")
PROMPT_LINE = re.compile(r"prompt (\d+) tokens = (\d+) reused \+ (\d+) read in (\d+) ms")
REFUSAL = "stages its blobs"


def post(body: dict, timeout: int = 1800):
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers=AUTH)
    return urllib.request.urlopen(req, timeout=timeout)


def decode_stream(stop: threading.Event, out: dict) -> None:
    """Stream A: record the arrival time of every chunk so gaps can be measured.

    The text of a chunk lives in choices[0]["delta"]["content"] (checked against this server's stream
    on 2026-10-24: `data: {... "choices": [{"index": 0, "delta": {"role": "assistant", "cont...`).
    The first version of this function read choices[0]["content"], which is absent, so stream A looked
    like it produced nothing while it was in fact the very thing the slots were decoding - the run
    printed "A 0 tok" for every rep.  Reasoning text is counted too, since it also arrives per token.
    """
    # PEER_MAX_TOKENS stretches the decode stream.  The old 400-token default often ended in about a
    # second, before a 22K-token read began; then "the slots decoded N rows meanwhile" measured an empty
    # peer rather than starvation.  The prompt asks for roughly 2000 Chinese characters and the default
    # cap is 1200 tokens so the peer covers the whole read window.  Both remain overridable.
    max_tokens = int(os.environ.get("PEER_MAX_TOKENS", "1200"))
    body = {"model": MODEL, "temperature": 0, "top_p": 1, "max_tokens": max_tokens, "stream": True,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": PROMPT_A}]}
    stamps: list[tuple[float, int]] = []
    try:
        with post(body) as response:
            for raw in response:
                if stop.is_set():
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                text = (delta.get("content") or "") + (delta.get("reasoning_content") or "")
                if chunk.get("usage"):
                    out["usage"] = chunk["usage"]
                if text:
                    stamps.append((time.perf_counter(), len(text)))
    except Exception as error:                       # noqa: BLE001
        out["error"] = f"{type(error).__name__}: {error}"
    out["stamps"] = stamps


def log_lines_from(index: int) -> list[str]:
    with open(LOG, encoding="utf-8", errors="replace") as handle:
        lines = handle.read().splitlines()
    return lines[index:]


def max_gap_overlapping(times: list[float], lo: float, hi: float) -> float:
    """The longest inter-chunk gap that overlaps [lo, hi].

    Filtering chunks to the window before computing gaps misses the stall this scenario exists to
    measure: when A sends one chunk before B starts and the next one after B ends, the long gap
    crosses the window but neither endpoint is inside it.  Keep both endpoints and require the
    interval between them to overlap the window.
    """
    return max((b - a for a, b in zip(times, times[1:]) if a <= hi and b >= lo), default=0.0)


def max_silence_in_window(times: list[float], lo: float, hi: float) -> float:
    """The longest part of [lo, hi] with no streamed chunk.

    A stalled stream often sends nothing at all while B reads, so there is no pair of chunks to
    subtract.  Treat the window ends as pseudo-events and measure the empty interval between them.
    """
    points = [lo] + [t for t in times if lo < t < hi] + [hi]
    return max((b - a for a, b in zip(points, points[1:])), default=0.0)


def main(argv: list[str]) -> int:
    reps = int(argv[1]) if len(argv) > 1 else 2
    chars = int(argv[2]) if len(argv) > 2 else PROMPT_B_TOKENS
    mode = (argv[3] if len(argv) > 3 else "coexist").lower()
    peers = int(argv[4]) if len(argv) > 4 else 1
    med = lambda xs: statistics.median(xs)                        # noqa: E731
    corpus = open(CORPUS, encoding="utf-8", errors="replace").read() if mode != "solo" else ""

    print(f"  {reps} reps, mode={mode}, {peers} 条解码流（每条最多 {os.environ.get('PEER_MAX_TOKENS', '1200')} token）"
          + (f" 同时 B 读一条全新冷提示词 ~{chars} 字符" if mode != "solo" else "，旁边没人读"))
    print("  注：A 那一侧数的是流式里收到的**字符**数（本提示词约 2 字符/token），不是 token 数；"
          "流里带 usage 时会另报 completion_tokens。跨臂比较请用同一单位，别把字符/s 当 tok/s。")
    rows = []
    for rep in range(1, reps + 1):
        stop = threading.Event()
        outs: list[dict] = []
        threads: list[threading.Thread] = []
        for _ in range(peers):
            stream: dict = {}
            outs.append(stream)
            threads.append(threading.Thread(target=decode_stream, args=(stop, stream), daemon=True))
        for th in threads:
            th.start()

        if mode == "solo":
            # the control: the same decode streams with nobody reading a cold prompt beside it
            for th in threads:
                th.join(timeout=180)
            t0, b_wall, b_tokens = 0.0, 0.0, None
            lines, reads, prompt_lines, refused = [], [], [], False
        else:
            time.sleep(1.0)                                      # let A get into its decode
            marker = sum(1 for _ in open(LOG, encoding="utf-8", errors="replace"))
            start = (int(uuid.uuid4().hex[:6], 16) * 7) % max(1, len(corpus) - chars)
            cold = corpus[start:start + chars]
            t0 = time.perf_counter()
            with post({"model": MODEL, "temperature": 0, "top_p": 1, "max_tokens": 8,
                       "chat_template_kwargs": {"enable_thinking": False},
                       "messages": [{"role": "user", "content": f"[{uuid.uuid4().hex[:8]}] " + cold}]}) as r:
                done = json.loads(r.read().decode("utf-8", "replace"))
            b_wall = time.perf_counter() - t0
            b_tokens = done.get("usage", {}).get("prompt_tokens")
            stop.set()
            for th in threads:
                th.join(timeout=120)
            time.sleep(1.0)                                      # let the engine flush its lines
            lines = log_lines_from(marker)
            reads = [READ_LINE.search(l) for l in lines if READ_LINE.search(l)]
            prompt_lines = [PROMPT_LINE.search(l) for l in lines if PROMPT_LINE.search(l)]
            refused = any(REFUSAL in l for l in lines)

        # per decode stream: characters seen, rate over its own life, rate inside the B window, worst gap
        per_stream = []
        for stream in outs:
            st = stream.get("stamps", [])
            stamps = [t for t, _ in st]
            tot = sum(n for _, n in st)
            sp = (st[-1][0] - st[0][0]) if len(st) > 1 else 0.0
            ins = [(t, n) for (t, n) in st if t0 <= t <= t0 + b_wall] if b_wall else st
            per_stream.append({
                "chars": tot,
                "span_s": round(sp, 1),
                "chars_s_alone": round(tot / sp, 1) if sp else 0.0,
                "chars_s_during_b": round(sum(n for _, n in ins) / b_wall, 1) if (b_wall and tot) else 0.0,
                "chunks_during_b": len(ins),
                "longest_gap_overlapping_b_s": round(max_gap_overlapping(stamps, t0, t0 + b_wall), 2) if b_wall else 0.0,
                "longest_silence_in_b_s": round(max_silence_in_window(stamps, t0, t0 + b_wall), 2) if b_wall else 0.0,
                "worst_gap_overall_s": round(max([stamps[i] - stamps[i - 1] for i in range(1, len(stamps))], default=0.0), 2),
            })

        a_total = sum(s["chars"] for s in per_stream)
        span = med([s["span_s"] for s in per_stream])
        a_rate_alone = med([s["chars_s_alone"] for s in per_stream])
        a_rate = med([s["chars_s_during_b"] for s in per_stream])
        worst_gap = med([s["longest_silence_in_b_s"] for s in per_stream])
        all_gaps = [s["worst_gap_overall_s"] for s in per_stream]
        a_tok = sum(int(s["chars_s_during_b"] * s["span_s"]) for s in per_stream)

        row = {"rep": rep, "mode": mode, "peers": peers, "b_prompt_tokens": b_tokens,
               "b_client_wall_s": round(b_wall, 1),
               "a_tokens_seen": a_total, "a_tok_s_alone": round(a_rate_alone, 1),
               "a_tok_s_during_b": round(a_rate, 1),
               "a_longest_silence_in_b_s": round(worst_gap, 2),
               "a_worst_gap_overall_s": round(max(all_gaps), 2) if all_gaps else 0.0,
               "gate_refused_this_rep": refused,
               "streams": per_stream,
               "concurrent_reads": [[int(m.group(1)), int(m.group(2)), int(m.group(5))] for m in reads]}
        rows.append(row)

        if mode == "solo":
            print(f"  rep {rep}: {peers} 条流各自 solo，合计 {a_total} 字符 | 每条中位 {span:.1f}s 跑完 "
                  f"= {a_rate_alone:.1f} 字符/s（每条最长 chunk 间隔中位 {worst_gap:.2f}s）")
        else:
            print(f"  rep {rep}: B prompt={b_tokens} client_wall={b_wall:.1f}s | "
                  f"{peers} 条流在 B 窗口内合计 {a_tok} 字符，每条中位 {a_rate:.1f} 字符/s，"
                  f"最长 chunk 间隔中位 {worst_gap:.2f}s（各流全程最长里取中位 {max(all_gaps) if all_gaps else 0:.2f}s）")
        for m in prompt_lines[-2:]:
            print(f"      engine: prompt {m.group(1)} = {m.group(2)} reused + {m.group(3)} read in {m.group(4)} ms")
        for m in reads[-3:]:
            print(f"      engine: read {m.group(1)} tokens in {m.group(2)} ms concurrent; "
                  f"slots decoded {m.group(5)} rows meanwhile")
        if refused:
            print("      gate refused this rep (the read stayed exclusive) - expected on the closed arm")
        time.sleep(2)

    if path := os.environ.get("PIPED_JSON"):
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(rows, handle, ensure_ascii=False, indent=1)
        print(f"  json: {path}")

    med = lambda xs: statistics.median(xs)                        # noqa: E731
    if mode == "solo":
        live = [r for r in rows if r["a_tok_s_alone"]]
        if live:
            print("\n  中位（solo 对照）：A %.1f 字符/s | 最长 chunk 间隔 %.2f s | 每次 %d 字符"
                  % (med([r["a_tok_s_alone"] for r in live]),
                     med([r["a_worst_gap_overall_s"] for r in live]),
                     int(med([r["a_tokens_seen"] for r in live]))))
    else:
        live = [r for r in rows if r["a_tok_s_during_b"]]
        if live:
            print("\n  中位：B 客户端墙钟 %.1f s | A 在 B 窗口内 %.1f 字符/s（A 全程 %.1f 字符/s）"
                  "| A 最长间隔 %.2f s | gate 拒绝 %d/%d 轮"
                  % (med([r["b_client_wall_s"] for r in rows]),
                     med([r["a_tok_s_during_b"] for r in live]),
                     med([r["a_tok_s_alone"] for r in live]),
                     med([r["a_longest_silence_in_b_s"] for r in live]),
                     sum(1 for r in rows if r["gate_refused_this_rep"]), len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
