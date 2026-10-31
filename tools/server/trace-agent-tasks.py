#!/usr/bin/env python3
"""Build a privacy-preserving timeline for real Codex agent tasks.

The input is one or more Codex ``*.jsonl`` rollout files.  The output groups
model requests and tool calls by root turn, and reports the wall clock covered
by each class plus the remaining unattributed gap.

This is client-side evidence, not Strata server timing.  Prompt, assistant,
reasoning and tool-output text never enters the result.  An optional Strata
``/metrics`` JSON can be supplied to match recent server requests by their end
time; unmatched requests stay explicit.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
import statistics
import sys
from typing import Iterable


UTC = dt.timezone.utc


def timestamp(value) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def epoch(value) -> dt.datetime | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return dt.datetime.fromtimestamp(value, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value else None


def number(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def files(paths: Iterable[pathlib.Path]) -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    for path in paths:
        found.extend(path.rglob("*.jsonl") if path.is_dir() else [path])
    return sorted({path.resolve() for path in found})


def turn_metadata(payload: dict) -> str | None:
    meta = payload.get("internal_chat_message_metadata_passthrough")
    if not isinstance(meta, dict):
        return None
    value = meta.get("turn_id")
    return value if isinstance(value, str) and value else None


def parse(paths: Iterable[pathlib.Path]) -> dict:
    roots: dict[str, dict] = {}
    records: list[dict] = []
    calls: dict[str, dict] = {}
    outputs: list[dict] = []
    user_events: list[dict] = []
    compactions: list[dict] = []
    turn_to_root: dict[str, str] = {}
    turn_to_thread: dict[str, str] = {}
    notes: list[str] = []

    for path in files(paths):
        try:
            handle = path.open(encoding="utf-8")
        except OSError as exc:
            notes.append(f"{path}: {exc}")
            continue
        with handle:
            for line_number, line in enumerate(handle, 1):
                try:
                    item = json.loads(line)
                except (TypeError, ValueError):
                    notes.append(f"{path}:{line_number}: invalid JSON")
                    continue
                if not isinstance(item, dict):
                    continue
                payload = item.get("payload")
                if not isinstance(payload, dict):
                    continue
                when = timestamp(item.get("timestamp"))
                kind = item.get("type")
                payload_kind = payload.get("type")

                if kind == "event_msg" and payload_kind == "task_started":
                    root = payload.get("root_turn_id") or payload.get("turn_id")
                    turn = payload.get("turn_id")
                    if isinstance(root, str):
                        start = epoch(payload.get("started_at")) or when
                        row = roots.setdefault(root, {
                            "root": root,
                            "threads": set(),
                            "status": "unknown",
                            "start": start,
                            "end": None,
                            "aborts": 0,
                            "completions": 0,
                            "terminal_duration_s": 0.0,
                        })
                        if start and (row["start"] is None or start < row["start"]):
                            row["start"] = start
                        if isinstance(turn, str):
                            turn_to_root[turn] = root

                elif kind == "event_msg" and payload_kind in ("task_complete", "turn_aborted"):
                    root = payload.get("root_turn_id") or payload.get("turn_id")
                    if not isinstance(root, str):
                        continue
                    row = roots.setdefault(root, {
                        "root": root,
                        "threads": set(),
                        "status": "unknown",
                        "start": epoch(payload.get("started_at")) or when,
                        "end": None,
                        "aborts": 0,
                        "completions": 0,
                        "terminal_duration_s": 0.0,
                    })
                    if payload_kind == "task_complete":
                        row["status"] = "completed"
                        row["completions"] += 1
                    else:
                        row["aborts"] += 1
                        row["status"] = "aborted"
                    duration = payload.get("duration_ms")
                    if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration >= 0:
                        row["terminal_duration_s"] += float(duration) / 1000
                    end = epoch(payload.get("completed_at")) or when
                    if end and (row["end"] is None or end > row["end"]):
                        row["end"] = end

                elif kind == "token_usage_record":
                    usage = payload.get("usage")
                    if not isinstance(usage, dict):
                        continue
                    root = payload.get("root_turn_id") or payload.get("turn_id") or "(none)"
                    turn = payload.get("turn_id")
                    thread = payload.get("thread_id")
                    cached = number(usage.get("cached_input_tokens"))
                    if not cached and usage.get("cached_tokens") is not None:
                        cached = number(usage.get("cached_tokens"))
                    records.append({
                        "at": when,
                        "root": root,
                        "turn": turn,
                        "thread": thread,
                        "response": payload.get("response_id"),
                        "input": number(usage.get("input_tokens")),
                        "cached": cached,
                        "cache_write": number(usage.get("cache_write_input_tokens")),
                        "output": number(usage.get("output_tokens")),
                        "reasoning": number(usage.get("reasoning_output_tokens")),
                    })
                    if isinstance(root, str):
                        roots.setdefault(root, {
                            "root": root,
                            "threads": set(),
                            "status": "unknown",
                            "start": when,
                            "end": None,
                            "aborts": 0,
                            "completions": 0,
                            "terminal_duration_s": 0.0,
                        })
                    if isinstance(turn, str) and isinstance(root, str):
                        turn_to_root[turn] = root
                    if isinstance(turn, str) and isinstance(thread, str):
                        turn_to_thread[turn] = thread

                elif kind == "response_item":
                    if payload_kind == "message" and payload.get("role") == "user":
                        turn = turn_metadata(payload)
                        user_events.append({
                            "at": when,
                            "turn": turn,
                            "root": turn_to_root.get(turn or ""),
                            "thread": turn_to_thread.get(turn or ""),
                        })
                    if payload_kind in (
                        "function_call", "function_call_output",
                        "custom_tool_call", "custom_tool_call_output",
                        "tool_search_call", "tool_search_output",
                    ):
                        call_id = payload.get("call_id")
                        if not isinstance(call_id, str):
                            continue
                        turn = turn_metadata(payload)
                        root = turn_to_root.get(turn or "")
                        thread = turn_to_thread.get(turn or "")
                        if payload_kind in ("function_call", "custom_tool_call", "tool_search_call"):
                            name = payload.get("name")
                            if not isinstance(name, str) and payload_kind == "tool_search_call":
                                name = "tool_search"
                            calls[call_id] = {
                                "at": when,
                                "root": root,
                                "thread": thread,
                                "turn": turn,
                                "name": name,
                            }
                        else:
                            output = payload.get("output")
                            if output is None and payload_kind == "tool_search_output":
                                output = payload.get("tools")
                            if isinstance(output, str):
                                chars = len(output)
                            elif output is None:
                                chars = 0
                            else:
                                chars = len(json.dumps(output, ensure_ascii=False,
                                                       separators=(",", ":")))
                            outputs.append({
                                "at": when,
                                "call_id": call_id,
                                "chars": chars,
                                "root": root,
                                "thread": thread,
                                "turn": turn,
                            })

                elif kind == "compacted" or payload_kind == "context_compacted":
                    latest = payload.get("latest_token_usage_record")
                    response = latest.get("response_id") if isinstance(latest, dict) else None
                    root = latest.get("root_turn_id") if isinstance(latest, dict) else None
                    thread = latest.get("thread_id") if isinstance(latest, dict) else None
                    compactions.append({
                        "at": when,
                        "root": root,
                        "thread": thread,
                        "response": response,
                    })

    for row in calls.values():
        if row.get("root") is None and row.get("turn"):
            row["root"] = turn_to_root.get(row["turn"])
        if row.get("thread") is None and row.get("turn"):
            row["thread"] = turn_to_thread.get(row["turn"])
    for row in outputs:
        if row.get("root") is None and row.get("turn"):
            row["root"] = turn_to_root.get(row["turn"])
        if row.get("thread") is None and row.get("turn"):
            row["thread"] = turn_to_thread.get(row["turn"])
    for row in user_events:
        if row.get("root") is None and row.get("turn"):
            row["root"] = turn_to_root.get(row["turn"])
        if row.get("thread") is None and row.get("turn"):
            row["thread"] = turn_to_thread.get(row["turn"])

    return {
        "roots": roots,
        "records": records,
        "calls": calls,
        "outputs": outputs,
        "user_events": user_events,
        "compactions": compactions,
        "turn_to_root": turn_to_root,
        "turn_to_thread": turn_to_thread,
        "notes": notes,
    }


def union_seconds(intervals: Iterable[tuple[dt.datetime, dt.datetime]]) -> float:
    rows = sorted((start, end) for start, end in intervals if end > start)
    if not rows:
        return 0.0
    total = 0.0
    start, end = rows[0]
    for next_start, next_end in rows[1:]:
        if next_start <= end:
            end = max(end, next_end)
        else:
            total += (end - start).total_seconds()
            start, end = next_start, next_end
    return total + (end - start).total_seconds()


def percentile(values: list[int]) -> dict:
    if not values:
        return {"median": 0, "p90": 0, "p99": 0, "max": 0}
    rows = sorted(values)

    def at(q: float) -> int:
        return rows[round((len(rows) - 1) * q)]

    return {"median": int(statistics.median(rows)), "p90": at(.90), "p99": at(.99), "max": rows[-1]}


def tool_spans(data: dict) -> tuple[list[dict], dict[str, int]]:
    outputs_by_call: dict[str, list[dict]] = collections.defaultdict(list)
    for row in data["outputs"]:
        outputs_by_call[row["call_id"]].append(row)
    spans = []
    missing = 0
    for call_id, call in data["calls"].items():
        start = call.get("at")
        candidates = [row for row in outputs_by_call.get(call_id, []) if row.get("at") and start and row["at"] >= start]
        if start is None or not candidates:
            missing += 1
            continue
        end = min(row["at"] for row in candidates)
        output = min(candidates, key=lambda row: row["at"])
        spans.append({
            "call_id": call_id,
            "root": call.get("root"),
            "thread": call.get("thread"),
            "name": call.get("name"),
            "start": start,
            "end": end,
            "seconds": max(0.0, (end - start).total_seconds()),
            "output_chars": output["chars"],
        })
    return spans, {"calls": len(data["calls"]), "unpaired": missing}


def model_spans(data: dict) -> list[dict]:
    """Infer each model call from the previous input boundary to its usage record.

    Codex records token usage when the response ends, not when it starts.  The
    latest user/function-output boundary in the same thread is therefore the
    best client-side start available.  The result is explicitly an estimate.
    """
    records = sorted((row for row in data["records"] if row.get("at")),
                     key=lambda row: row["at"])
    boundaries: dict[str, list[dt.datetime]] = collections.defaultdict(list)
    root_starts = {
        root: row["start"] for root, row in data["roots"].items()
        if row.get("start")
    }

    for output in data["outputs"]:
        if output.get("thread") and output.get("at"):
            boundaries[output["thread"]].append(output["at"])

    for event in data.get("user_events", []):
        if event.get("thread") and event.get("at"):
            boundaries[event["thread"]].append(event["at"])

    for thread in list(boundaries):
        boundaries[thread] = sorted(set(boundaries[thread]))

    compaction_responses = {row.get("response") for row in data["compactions"] if row.get("response")}
    spans = []
    latest_model_end: dict[str, dt.datetime] = {}
    for row in records:
        end = row["at"]
        thread = row.get("thread")
        starts = [x for x in boundaries.get(thread, ()) if x < end]
        if thread in latest_model_end and latest_model_end[thread] < end:
            starts.append(latest_model_end[thread])
        root_start = root_starts.get(row.get("root"))
        if root_start and root_start < end:
            starts.append(root_start)
        start = max(starts) if starts else end
        spans.append({
            "root": row["root"],
            "thread": thread,
            "turn": row.get("turn"),
            "response": row.get("response"),
            "start": start,
            "end": end,
            "seconds": max(0.0, (end - start).total_seconds()),
            "compaction": row.get("response") in compaction_responses,
            **{key: row[key] for key in ("input", "cached", "cache_write", "output", "reasoning")},
        })
        if thread:
            latest_model_end[thread] = end
    return spans


def summarize(parsed: dict, server_metrics: dict | None = None,
              server_match_seconds: float = 15.0, top: int = 20,
              server_token_tolerance: float = 0.03,
              server_token_slack: int = 2048) -> dict:
    data = dict(parsed)
    tool_rows, tool_accounting = tool_spans(data)
    model_rows = model_spans(data)

    for row in model_rows:
        if row.get("root") in data["roots"] and row.get("thread"):
            data["roots"][row["root"]]["threads"].add(row["thread"])
    for row in tool_rows:
        if row.get("root") in data["roots"] and row.get("thread"):
            data["roots"][row["root"]]["threads"].add(row["thread"])

    server_rows = []
    server_unmatched = 0
    if server_metrics is not None:
        raw = server_metrics.get("requests") if isinstance(server_metrics, dict) else server_metrics
        if not isinstance(raw, list):
            raise ValueError("server metrics must be an array or an object with a requests array")
        for row in raw:
            if not isinstance(row, dict):
                continue
            started = row.get("time")
            duration = row.get("duration_s")
            if not isinstance(started, (int, float)) or not isinstance(duration, (int, float)):
                continue
            try:
                end = dt.datetime.fromtimestamp(float(started) + float(duration), UTC)
            except (OverflowError, OSError, ValueError):
                continue
            server_rows.append({
                "end": end,
                "prompt_ms": row.get("prompt_ms"),
                "decode_ms": row.get("decode_ms"),
                "duration_s": duration,
                "prompt_tokens": row.get("prompt_total") or row.get("prompt_tokens"),
                "reused": row.get("reused"),
                "finish": row.get("finish"),
            })

        used: set[int] = set()
        for model in model_rows:
            best = None
            for index, server in enumerate(server_rows):
                if index in used:
                    continue
                distance = abs((server["end"] - model["end"]).total_seconds())
                prompt_tokens = server.get("prompt_tokens")
                token_delta = (abs(int(prompt_tokens) - model["input"])
                               if isinstance(prompt_tokens, int) else 0)
                token_ok = (not isinstance(prompt_tokens, int) or
                            token_delta <= max(server_token_slack,
                                               int(max(model["input"], prompt_tokens) *
                                                   server_token_tolerance)))
                if not token_ok or distance > server_match_seconds:
                    continue
                if best is None or distance < best[0]:
                    best = (distance, index)
            if best is None:
                server_unmatched += 1
                model["server"] = None
                continue
            used.add(best[1])
            server = server_rows[best[1]]
            prompt_tokens = server.get("prompt_tokens")
            model["server"] = {
                "match_delta_s": round(best[0], 3),
                "token_delta": abs(int(prompt_tokens) - model["input"])
                if isinstance(prompt_tokens, int) else None,
                **{key: server[key] for key in
                   ("prompt_ms", "decode_ms", "duration_s", "prompt_tokens", "reused", "finish")},
            }

    roots = []
    for root, info in data["roots"].items():
        start, end = info.get("start"), info.get("end")
        if start is None or end is None or end < start:
            continue
        models = [row for row in model_rows if row.get("root") == root]
        tools = [row for row in tool_rows if row.get("root") == root]
        model_intervals = [(row["start"], row["end"]) for row in models]
        tool_intervals = [(row["start"], row["end"]) for row in tools]
        model_union = union_seconds(model_intervals)
        tool_union = union_seconds(tool_intervals)
        covered_union = union_seconds(model_intervals + tool_intervals)
        wall = (end - start).total_seconds()
        usage = {key: sum(row[key] for row in models) for key in
                 ("input", "cached", "cache_write", "output", "reasoning")}
        roots.append({
            "root": root,
            "status": info.get("status"),
            "start": iso(start),
            "end": iso(end),
            "wall_s": round(wall, 3),
            "model_requests": len(models),
            "model_sum_s": round(sum(row["seconds"] for row in models), 3),
            "model_union_s": round(model_union, 3),
            "tool_calls": len(tools),
            "tool_sum_s": round(sum(row["seconds"] for row in tools), 3),
            "tool_union_s": round(tool_union, 3),
            "covered_union_s": round(covered_union, 3),
            "model_tool_overlap_s": round(max(0.0, model_union + tool_union - covered_union), 3),
            "unattributed_gap_s": round(max(0.0, wall - covered_union), 3),
            "model_share": round(model_union / wall, 6) if wall else 0.0,
            "tool_share": round(tool_union / wall, 6) if wall else 0.0,
            "unattributed_share": round(max(0.0, wall - covered_union) / wall, 6) if wall else 0.0,
            "compactions": sum(1 for row in models if row.get("compaction")),
            "completions": info.get("completions", 0),
            "aborts": info.get("aborts", 0),
            "terminal_duration_s": round(info.get("terminal_duration_s", 0.0), 3),
            "threads": len(info.get("threads") or ()),
            **usage,
            "uncached": usage["input"] - usage["cached"] - usage["cache_write"],
            "tool_output_chars": sum(row["output_chars"] for row in tools),
            "tool_outputs_10k": sum(1 for row in tools if row["output_chars"] >= 10000),
            "tool_calls_30s": sum(1 for row in tools if row["seconds"] >= 30),
            "server_matched": sum(1 for row in models if row.get("server")),
            "server_unmatched": sum(1 for row in models if row.get("server") is None)
            if server_metrics is not None else None,
            "server_prompt_ms_sum": round(sum((row["server"] or {}).get("prompt_ms") or 0 for row in models), 3)
            if server_metrics is not None else None,
            "server_decode_ms_sum": round(sum((row["server"] or {}).get("decode_ms") or 0 for row in models), 3)
            if server_metrics is not None else None,
            "server_duration_s_sum": round(sum((row["server"] or {}).get("duration_s") or 0 for row in models), 3)
            if server_metrics is not None else None,
        })

    roots.sort(key=lambda row: row["wall_s"], reverse=True)
    tools_ranked = sorted(tool_rows, key=lambda row: row["seconds"], reverse=True)[:top]
    models_ranked = sorted(model_rows, key=lambda row: row["output"], reverse=True)[:top]
    by_name: dict[str, list[dict]] = collections.defaultdict(list)
    for row in tool_rows:
        by_name[str(row.get("name") or "(unknown)")].append(row)
    tools_by_name = sorted(({
        "name": name,
        "calls": len(rows),
        "sum_s": round(sum(row["seconds"] for row in rows), 3),
        "output_chars": sum(row["output_chars"] for row in rows),
        "calls_30s": sum(1 for row in rows if row["seconds"] >= 30),
        "outputs_10k": sum(1 for row in rows if row["output_chars"] >= 10000),
    } for name, rows in by_name.items()), key=lambda row: row["sum_s"], reverse=True)

    def model_kind(compaction: bool) -> dict:
        rows = [row for row in model_rows if bool(row.get("compaction")) == compaction]
        return {
            "requests": len(rows),
            "input": sum(row["input"] for row in rows),
            "cached": sum(row["cached"] for row in rows),
            "output": sum(row["output"] for row in rows),
            "reasoning": sum(row["reasoning"] for row in rows),
            "reuse_50pct_requests": sum(
                1 for row in rows if row["input"] and row["cached"] / row["input"] >= 0.5),
            "miss_50pct_requests": sum(
                1 for row in rows if not row["input"] or row["cached"] / row["input"] < 0.5),
            "uncached_percentiles": percentile(
                [row["input"] - row["cached"] - row["cache_write"] for row in rows]),
            "span_sum_s": round(sum(row["seconds"] for row in rows), 3),
        }

    matched = [row["server"] for row in model_rows if row.get("server")]
    server_matches = sorted((
        {
            "root": row.get("root"),
            "turn": row.get("turn"),
            "thread": row.get("thread"),
            "client_span_s": round(row["seconds"], 3),
            "input": row["input"],
            "cached": row["cached"],
            "output": row["output"],
            "reasoning": row["reasoning"],
            "server": row["server"],
        }
        for row in model_rows if row.get("server")
    ), key=lambda row: row["server"].get("prompt_ms") or 0, reverse=True)
    server_timing = None
    if server_metrics is not None:
        server_timing = {
            "matched": len(matched),
            "prompt_ms_sum": round(sum(row.get("prompt_ms") or 0 for row in matched), 3),
            "decode_ms_sum": round(sum(row.get("decode_ms") or 0 for row in matched), 3),
            "duration_s_sum": round(sum(row.get("duration_s") or 0 for row in matched), 3),
            "match_delta_s_percentiles": percentile(
                [round((row.get("match_delta_s") or 0) * 1000) for row in matched]),
            "prompt_ms_percentiles": percentile(
                [round(row.get("prompt_ms") or 0) for row in matched]),
            "decode_ms_percentiles": percentile(
                [round(row.get("decode_ms") or 0) for row in matched]),
        }

    def clean(rows: list[dict]) -> list[dict]:
        return [{key: (iso(value) if isinstance(value, dt.datetime) else value)
                 for key, value in row.items()}
                for row in rows]

    all_wall = union_seconds([(dt.datetime.fromisoformat(row["start"]), dt.datetime.fromisoformat(row["end"]))
                              for row in roots])
    return {
        "schema": 1,
        "scope": "Codex client events; model spans are inferred and not Strata server timing",
        "files": 0,
        "roots": len(roots),
        "threads": len({row["thread"] for row in model_rows if row.get("thread")}),
        "model_requests": len(model_rows),
        "tool_accounting": tool_accounting,
        "wall_union_s": round(all_wall, 3),
        "model_union_s": round(union_seconds([(row["start"], row["end"]) for row in model_rows]), 3),
        "tool_union_s": round(union_seconds([(row["start"], row["end"]) for row in tool_rows]), 3),
        "input": sum(row["input"] for row in model_rows),
        "cached": sum(row["cached"] for row in model_rows),
        "cache_write": sum(row["cache_write"] for row in model_rows),
        "output": sum(row["output"] for row in model_rows),
        "reasoning": sum(row["reasoning"] for row in model_rows),
        "output_percentiles": percentile([row["output"] for row in model_rows]),
        "reasoning_percentiles": percentile([row["reasoning"] for row in model_rows]),
        "tool_wait_percentiles": percentile([round(row["seconds"] * 1000) for row in tool_rows]),
        "tool_output_chars": sum(row["output_chars"] for row in tool_rows),
        "tool_outputs_10k": sum(1 for row in tool_rows if row["output_chars"] >= 10000),
        "tool_by_name": tools_by_name,
        "model_calls": {
            "ordinary": model_kind(False),
            "compaction": model_kind(True),
        },
        "server_matched": sum(1 for row in model_rows if row.get("server")),
        "server_unmatched": server_unmatched if server_metrics is not None else None,
        "server_timing": server_timing,
        "server_matches": server_matches[:top],
        "roots_by_wall": roots,
        "top_tool_calls": clean(tools_ranked),
        "top_model_outputs": clean(models_ranked),
        "parse_notes": data["notes"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", type=pathlib.Path,
                        help="Codex rollout .jsonl files or directories")
    parser.add_argument("--server-metrics", type=pathlib.Path,
                        help="optional Strata /metrics JSON whose requests array gives server timing")
    parser.add_argument("--server-match-seconds", type=float, default=15.0,
                        help="maximum client/server end-time distance for a match (default: 15)")
    parser.add_argument("--server-token-tolerance", type=float, default=0.03,
                        help="prompt-token relative tolerance for a match (default: 0.03)")
    parser.add_argument("--server-token-slack", type=int, default=2048,
                        help="absolute prompt-token slack for a match (default: 2048)")
    parser.add_argument("--top", type=int, default=20,
                        help="number of longest tools and largest model outputs to keep")
    parser.add_argument("--output", type=pathlib.Path,
                        help="write JSON here instead of stdout")
    args = parser.parse_args(argv)
    if args.server_match_seconds < 0:
        parser.error("--server-match-seconds must be zero or positive")
    if args.server_token_tolerance < 0:
        parser.error("--server-token-tolerance must be zero or positive")
    if args.server_token_slack < 0:
        parser.error("--server-token-slack must be zero or positive")
    if args.top < 0:
        parser.error("--top must be zero or positive")

    parsed = parse(args.paths)
    parsed["files"] = len(files(args.paths))
    server = None
    if args.server_metrics:
        try:
            server = json.loads(args.server_metrics.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            parser.error(f"cannot read --server-metrics: {exc}")
    result = summarize(parsed, server, args.server_match_seconds, args.top,
                       args.server_token_tolerance, args.server_token_slack)
    result["files"] = parsed["files"]
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(json.dumps({
            "output": str(args.output),
            "roots": result["roots"],
            "model_requests": result["model_requests"],
            "tool_calls": result["tool_accounting"]["calls"],
            "parse_notes": len(result["parse_notes"]),
        }, sort_keys=True))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
