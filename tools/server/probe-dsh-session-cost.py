#!/usr/bin/env python3
"""Attribute a DSH run to model, decode, tool and client time without retaining text.

The probe reads DSH's durable session JSONL (optionally zstd-compressed) and, when
given a Strata ``/metrics`` snapshot, joins model steps by their token counts and
nearby start time.  It never prints prompts, reasoning, tool input or results.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import shutil
import subprocess
import sys
from typing import Any


def read_session(path: pathlib.Path) -> list[dict[str, Any]]:
    if path.suffix not in {".zst", ".zstd"}:
        text = path.read_text(encoding="utf-8", errors="replace")
    else:
        zstd = shutil.which("zstd")
        if not zstd:
            raise ValueError(f"{path}: zstd is required to read a compressed session")
        proc = subprocess.run(
            [zstd, "-dc", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise ValueError(f"{path}: zstd failed: {proc.stderr.strip()}")
        text = proc.stdout
    rows = []
    for number, raw in enumerate(text.splitlines(), 1):
        try:
            row = json.loads(raw)
        except ValueError as error:
            raise ValueError(f"{path}:{number}: invalid JSON: {error}") from error
        if isinstance(row, dict):
            rows.append(row)
    return rows


def interval_union(spans: list[tuple[float, float]]) -> float:
    """Wall time covered by at least one span, in the spans' unit."""
    total = 0.0
    start = end = None
    for left, right in sorted((left, right) for left, right in spans if right >= left):
        if start is None or left > end:
            if start is not None:
                total += end - start
            start, end = left, right
        else:
            end = max(end, right)
    if start is not None:
        total += end - start
    return total


def stream_first_time(message: dict[str, Any]) -> float | None:
    """The first content/stream timestamp attached to one assistant message."""
    times = []
    for event in message.get("stream") or []:
        if not isinstance(event, dict):
            continue
        if isinstance(event.get("time"), (int, float)):
            times.append(float(event["time"]))
        chunk = event.get("chunk")
        if isinstance(chunk, dict) and isinstance(chunk.get("time0"), (int, float)):
            times.append(float(chunk["time0"]))
    return min(times) if times else None


def tool_span(row: dict[str, Any], calls: dict[str, tuple[float, str]]) -> tuple[float, float, str] | None:
    data = row.get("data") or {}
    message = data.get("message") or {}
    call_id = message.get("toolCallId")
    if not isinstance(call_id, str) or call_id not in calls:
        return None
    started, name = calls[call_id]
    ended = row.get("time")
    if not isinstance(ended, (int, float)):
        return None
    return float(started), float(ended), name


def request_matches(
    step: dict[str, Any],
    requests: list[dict[str, Any]],
    window_s: float,
) -> dict[str, Any] | None:
    """Join a model step to one server request by exact token counts and start time."""
    started_s = step["started_ms"] / 1000
    context_tokens = step["fresh_input_tokens"] + step["cached_input_tokens"]
    candidates = [
        row
        for row in requests
        if row.get("prompt_tokens") == context_tokens
        and row.get("output_tokens") == step["output_tokens"]
        and isinstance(row.get("time"), (int, float))
        and abs(float(row["time"]) - started_s) <= window_s
    ]
    return min(candidates, key=lambda row: abs(float(row["time"]) - started_s)) if candidates else None


def summarize_session(
    path: pathlib.Path,
    requests: list[dict[str, Any]] | None = None,
    match_window_s: float = 2.0,
) -> dict[str, Any]:
    rows = read_session(path)
    timed = [row for row in rows if isinstance(row.get("time"), (int, float))]
    if not timed:
        raise ValueError(f"{path}: no timed session events")
    first_ms = float(timed[0]["time"])
    last_ms = float(timed[-1]["time"])

    step_start_ms = None
    assistant: tuple[float, dict[str, Any]] | None = None
    calls: dict[str, tuple[float, str]] = {}
    steps = []
    tool_spans: list[tuple[float, float, str]] = []
    turn_started_ms = None
    first_turn_started_ms = None
    last_turn_ended_ms = None
    turn_spans: list[tuple[float, float]] = []
    for row in rows:
        kind = row.get("type")
        when = row.get("time")
        data = row.get("data") or {}
        if kind == "turn/start" and isinstance(when, (int, float)):
            turn_started_ms = float(when)
            if first_turn_started_ms is None:
                first_turn_started_ms = turn_started_ms
        elif kind == "step/start" and isinstance(when, (int, float)):
            step_start_ms = float(when)
            assistant = None
        elif kind == "assistant/message" and isinstance(when, (int, float)):
            assistant = (float(when), data.get("message") or {}, data.get("usage") or {})
        elif kind == "step/end" and step_start_ms is not None and assistant is not None:
            ended_ms, message, usage = assistant
            first_stream_ms = stream_first_time(message)
            fresh = int(usage.get("inputTokens") or 0)
            cached = int(usage.get("cacheReadTokens") or 0)
            output = int(usage.get("outputTokens") or 0)
            step = {
                "turn": data.get("turn"),
                "step": data.get("step"),
                "started_ms": step_start_ms,
                "model_wall_s": round((ended_ms - step_start_ms) / 1000, 3),
                "time_to_first_stream_s": (
                    round((first_stream_ms - step_start_ms) / 1000, 3)
                    if first_stream_ms is not None
                    else None
                ),
                "stream_after_first_s": (
                    round((ended_ms - first_stream_ms) / 1000, 3)
                    if first_stream_ms is not None
                    else None
                ),
                "fresh_input_tokens": fresh,
                "cached_input_tokens": cached,
                "context_tokens": fresh + cached,
                "output_tokens": output,
            }
            if requests is not None:
                match = request_matches(step, requests, match_window_s)
                if match:
                    step["server"] = {
                        "id": match.get("id"),
                        "start_delta_s": round(float(match["time"]) - step_start_ms / 1000, 3),
                        "duration_s": match.get("duration_s"),
                        "prompt_tokens": match.get("prompt_tokens"),
                        "reused_tokens": match.get("reused"),
                        "prompt_read_tokens": match.get("prompt_read"),
                        "prompt_ms": match.get("prompt_ms"),
                        "decode_ms": match.get("decode_ms"),
                        "first_engine_token_s": match.get("first_engine_token_s"),
                        "finish": match.get("finish"),
                    }
            steps.append(step)
        elif kind == "tool/call" and isinstance(when, (int, float)):
            call_id = data.get("callId")
            if isinstance(call_id, str):
                calls[call_id] = (float(when), str(data.get("name") or "tool"))
        elif kind == "tool/result":
            span = tool_span(row, calls)
            if span:
                tool_spans.append(span)
        elif kind == "turn/end" and isinstance(when, (int, float)):
            last_turn_ended_ms = float(when)
            if turn_started_ms is not None:
                turn_spans.append((turn_started_ms, last_turn_ended_ms))
                turn_started_ms = None

    model_s = sum(step["model_wall_s"] for step in steps)
    tool_union_ms = interval_union([(left, right) for left, right, _ in tool_spans])
    client_wall_s = (last_ms - first_ms) / 1000
    task_wall_s = (
        (last_turn_ended_ms - first_turn_started_ms) / 1000
        if first_turn_started_ms is not None and last_turn_ended_ms is not None
        else None
    )
    turn_union_s = interval_union(turn_spans) / 1000
    post_turn_s = (
        max(0.0, (last_ms - last_turn_ended_ms) / 1000)
        if last_turn_ended_ms is not None
        else None
    )
    tool_union_s = tool_union_ms / 1000
    tool_sum_s = sum((right - left) / 1000 for left, right, _ in tool_spans)
    startup_s = ((first_turn_started_ms if first_turn_started_ms is not None else first_ms) - first_ms) / 1000
    residual_s = max(0.0, client_wall_s - startup_s - model_s - tool_union_s)
    fresh = sum(step["fresh_input_tokens"] for step in steps)
    cached = sum(step["cached_input_tokens"] for step in steps)
    output = sum(step["output_tokens"] for step in steps)
    tools: dict[str, dict[str, float | int]] = {}
    for _, _, name in tool_spans:
        tools.setdefault(name, {"calls": 0, "wall_s": 0.0})
        tools[name]["calls"] = int(tools[name]["calls"]) + 1
        tools[name]["wall_s"] = round(float(tools[name]["wall_s"]), 3)
    for left, right, name in tool_spans:
        tools[name]["wall_s"] = round(float(tools[name]["wall_s"]) + (right - left) / 1000, 3)

    matched = [step for step in steps if "server" in step]
    server = None
    if requests is not None:
        matched_ids = {step["server"]["id"] for step in matched}
        within = [
            row
            for row in requests
            if isinstance(row.get("time"), (int, float))
            and first_ms / 1000 <= float(row["time"]) <= last_ms / 1000
        ]
        unmatched = [row for row in within if row.get("id") not in matched_ids]
        server = {
            "requests_in_session_window": len(within),
            "matched_steps": len(matched),
            "request_match_rate": round(len(matched) / len(steps), 3) if steps else None,
            "prompt_ms_sum": round(sum(float(step["server"].get("prompt_ms") or 0) for step in matched), 1),
            "decode_ms_sum": round(sum(float(step["server"].get("decode_ms") or 0) for step in matched), 1),
            "unmatched": [
                {
                    "duration_s": row.get("duration_s"),
                    "prompt_tokens": row.get("prompt_tokens"),
                    "output_tokens": row.get("output_tokens"),
                    "prompt_ms": row.get("prompt_ms"),
                    "decode_ms": row.get("decode_ms"),
                    "finish": row.get("finish"),
                }
                for row in unmatched
            ],
        }

    return {
        "label": path.parents[1].name if path.parent.name.startswith("session-") else path.parent.name,
        "client_wall_s": round(client_wall_s, 3),
        "task_wall_s": round(task_wall_s, 3) if task_wall_s is not None else None,
        "turn_union_s": round(turn_union_s, 3),
        "post_turn_s": round(post_turn_s, 3) if post_turn_s is not None else None,
        "startup_s": round(startup_s, 3),
        "model_wall_s": round(model_s, 3),
        "model_share_pct": round(100 * model_s / client_wall_s, 3) if client_wall_s else None,
        "tool_union_s": round(tool_union_s, 3),
        "tool_sum_s": round(tool_sum_s, 3),
        "tool_share_pct": round(100 * tool_union_s / client_wall_s, 3) if client_wall_s else None,
        "residual_s": round(residual_s, 3),
        "steps": len(steps),
        "tokens": {
            "fresh_input": fresh,
            "cached_input": cached,
            "context_input": fresh + cached,
            "output": output,
            "cache_reuse_pct": round(100 * cached / (fresh + cached), 3) if fresh + cached else None,
        },
        "tool_calls": dict(sorted(tools.items())),
        "server": server,
        "step_rows": steps,
    }


def summarize(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    wall = sum(session["client_wall_s"] for session in sessions)
    model = sum(session["model_wall_s"] for session in sessions)
    tools = sum(session["tool_union_s"] for session in sessions)
    task_walls = [session["task_wall_s"] for session in sessions if session["task_wall_s"] is not None]
    fresh = sum(session["tokens"]["fresh_input"] for session in sessions)
    cached = sum(session["tokens"]["cached_input"] for session in sessions)
    return {
        "sessions": len(sessions),
        "steps": sum(session["steps"] for session in sessions),
        "client_wall_s": round(wall, 3),
        "task_wall_s": round(sum(task_walls), 3) if task_walls else None,
        "sessions_with_task_wall": len(task_walls),
        "model_wall_s": round(model, 3),
        "model_share_pct": round(100 * model / wall, 3) if wall else None,
        "tool_union_s": round(tools, 3),
        "tool_share_pct": round(100 * tools / wall, 3) if wall else None,
        "fresh_input_tokens": fresh,
        "cached_input_tokens": cached,
        "context_input_tokens": fresh + cached,
        "output_tokens": sum(session["tokens"]["output"] for session in sessions),
        "cache_reuse_pct": round(100 * cached / (fresh + cached), 3) if fresh + cached else None,
        "server_matched_steps": sum(
            int((session.get("server") or {}).get("matched_steps") or 0) for session in sessions
        ),
        "server_unmatched_requests": sum(
            len((session.get("server") or {}).get("unmatched") or []) for session in sessions
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sessions", nargs="+", type=pathlib.Path)
    parser.add_argument("--metrics", type=pathlib.Path, help="a GET /metrics?requests=all JSON snapshot")
    parser.add_argument("--match-window", type=float, default=2.0)
    parser.add_argument("--output", type=pathlib.Path)
    options = parser.parse_args(argv)

    requests = None
    if options.metrics:
        raw = json.loads(options.metrics.read_text(encoding="utf-8"))
        requests = raw.get("requests") if isinstance(raw, dict) else raw
        if not isinstance(requests, list):
            parser.error("--metrics must contain a requests array")

    try:
        parsed = [
            summarize_session(path, requests, options.match_window)
            for path in options.sessions
        ]
    except (OSError, ValueError) as error:
        parser.error(str(error))
    rows = [row for row in parsed if row["steps"]]
    skipped = [
        {"label": row["label"], "client_wall_s": row["client_wall_s"]}
        for row in parsed
        if not row["steps"]
    ]
    report = {
        "schema": 1,
        "scope": "DSH session timing and token metadata only; no prompt, reasoning or tool bodies",
        "match_window_s": options.match_window,
        "skipped_empty_sessions": skipped,
        "summary": summarize(rows),
        "sessions": rows,
    }
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if options.output:
        options.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
