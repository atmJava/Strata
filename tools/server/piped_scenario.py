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
CORPUS = "/opt/ai/docs/strata-parameters-0.2.2-20261022.md"

PROMPT_A = "用中文详细解释混合专家模型的路由与负载均衡，尽量写长。"
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
    body = {"model": MODEL, "temperature": 0, "top_p": 1, "max_tokens": 400, "stream": True,
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


def main(argv: list[str]) -> int:
    reps = int(argv[1]) if len(argv) > 1 else 2
    chars = int(argv[2]) if len(argv) > 2 else PROMPT_B_TOKENS
    mode = (argv[3] if len(argv) > 3 else "coexist").lower()
    corpus = open(CORPUS, encoding="utf-8", errors="replace").read() if mode != "solo" else ""

    print(f"  {reps} reps, mode={mode}: A decodes 400 tokens"
          + (f" while B reads a cold prompt of ~{chars} 字符" if mode != "solo" else " alone"))
    print("  注：A 那一侧数的是流式里收到的**字符**数（本提示词约 2 字符/token），不是 token 数；"
          "流里带 usage 时会另报 completion_tokens。跨臂比较请用同一单位，别把字符/s 当 tok/s。")
    rows = []
    for rep in range(1, reps + 1):
        out: dict = {}
        stop = threading.Event()
        thread = threading.Thread(target=decode_stream, args=(stop, out), daemon=True)
        thread.start()

        if mode == "solo":
            # the control: the same decode stream with nobody reading a cold prompt beside it
            thread.join(timeout=180)
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
            thread.join(timeout=120)
            time.sleep(1.0)                                      # let the engine flush its lines
            lines = log_lines_from(marker)
            reads = [READ_LINE.search(l) for l in lines if READ_LINE.search(l)]
            prompt_lines = [PROMPT_LINE.search(l) for l in lines if PROMPT_LINE.search(l)]
            refused = any(REFUSAL in l for l in lines)

        stamps = out.get("stamps", [])

        a_total = sum(n for _, n in stamps)
        span = (stamps[-1][0] - stamps[0][0]) if len(stamps) > 1 else 0.0
        a_rate_alone = a_total / span if span else 0.0
        all_gaps = [stamps[i][0] - stamps[i - 1][0] for i in range(1, len(stamps))]

        # A's speed while B was reading: chunk stamps inside the B window
        inside = [(t, n) for (t, n) in stamps if t0 <= t <= t0 + b_wall] if b_wall else stamps
        a_tok = sum(n for _, n in inside)
        a_rate = a_tok / b_wall if b_wall and a_tok else 0.0
        gaps = [inside[i][0] - inside[i - 1][0] for i in range(1, len(inside))]
        worst_gap = max(gaps) if gaps else 0.0

        row = {"rep": rep, "mode": mode, "b_prompt_tokens": b_tokens,
               "b_client_wall_s": round(b_wall, 1),
               "a_tokens_seen": a_total, "a_tok_s_alone": round(a_rate_alone, 1),
               "a_tok_s_during_b": round(a_rate, 1),
               "a_worst_gap_during_b_s": round(worst_gap, 2),
               "a_worst_gap_overall_s": round(max(all_gaps), 2) if all_gaps else 0.0,
               "gate_refused_this_rep": refused,
               "concurrent_reads": [[int(m.group(1)), int(m.group(2)), int(m.group(5))] for m in reads]}
        rows.append(row)

        if mode == "solo":
            print(f"  rep {rep}: A alone {a_total} 字符 in {span:.1f}s = {a_rate_alone:.1f} 字符/s "
                  f"({out.get('usage', {}).get('completion_tokens', '?')} tok 由 usage 报), "
                  f"最长 chunk 间隔 {worst_gap:.2f}s")
        else:
            print(f"  rep {rep}: B prompt={b_tokens} client_wall={b_wall:.1f}s | "
                  f"A {a_total} 字符 总, B 窗口内 {a_tok} 字符 = {a_rate:.1f} 字符/s, "
                  f"最长 chunk 间隔 {worst_gap:.2f}s（全程 {max(all_gaps) if all_gaps else 0:.2f}s）")
        for m in prompt_lines[-2:]:
            print(f"      engine: prompt {m.group(1)} = {m.group(2)} reused + {m.group(3)} read in {m.group(4)} ms")
        for m in reads[-3:]:
            print(f"      engine: read {m.group(1)} tokens in {m.group(2)} ms concurrent; "
                  f"slots decoded {m.group(5)} rows meanwhile")
        if refused:
            print("      gate refused this rep (the read stayed exclusive) - expected on the closed arm")
        time.sleep(2)

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
                     med([r["a_worst_gap_during_b_s"] for r in live]),
                     sum(1 for r in rows if r["gate_refused_this_rep"]), len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
