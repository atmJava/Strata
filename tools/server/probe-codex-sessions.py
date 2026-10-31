#!/usr/bin/env python3
"""Summarize real Codex session token records without printing prompt text.

The input is one or more Codex ``*.jsonl`` rollout files.  The output separates
client-side token shape from server timing: it can show which request kinds
dominate uncached input, but it cannot attribute latency to a Strata engine.

Compaction events are associated with the latest token usage record at or
before the event.  The record before that one is reported as the preceding
ordinary request, which makes the "cached before compaction" contrast explicit.

Example:
    probe-codex-sessions.py ~/.codex/sessions/2026/10/31 \
      --output /opt/ai/logs/codex-session-summary.json
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


FIELDS = ("input", "cached", "cache_write", "output", "reasoning")


def _timestamp(value) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _int(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _files(paths: Iterable[pathlib.Path]) -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    for path in paths:
        if path.is_dir():
            found.extend(path.rglob("*.jsonl"))
        else:
            found.append(path)
    return sorted({path.resolve() for path in found})


def parse(paths: Iterable[pathlib.Path]) -> tuple[list[dict], list[dict], list[str]]:
    """Return token records, compaction events and non-fatal parse notes."""
    records: list[dict] = []
    compactions: list[dict] = []
    notes: list[str] = []
    for path in _files(paths):
        try:
            lines = path.open(encoding="utf-8")
        except OSError as exc:
            notes.append(f"{path}: {exc}")
            continue
        with lines:
            for number, line in enumerate(lines, 1):
                try:
                    item = json.loads(line)
                except (TypeError, ValueError):
                    notes.append(f"{path}:{number}: invalid JSON")
                    continue
                if not isinstance(item, dict):
                    continue
                payload = item.get("payload")
                if not isinstance(payload, dict):
                    continue
                timestamp = _timestamp(item.get("timestamp"))
                if item.get("type") == "token_usage_record":
                    usage = payload.get("usage")
                    if not isinstance(usage, dict):
                        continue
                    cached = _int(usage.get("cached_input_tokens"))
                    if not cached and usage.get("cached_tokens") is not None:
                        cached = _int(usage.get("cached_tokens"))
                    record = {
                        "timestamp": item.get("timestamp"),
                        "time": timestamp,
                        "thread": payload.get("thread_id"),
                        "root": payload.get("root_turn_id") or payload.get("turn_id"),
                        "turn": payload.get("turn_id"),
                        "response": payload.get("response_id"),
                        "input": _int(usage.get("input_tokens")),
                        "cached": cached,
                        "cache_write": _int(usage.get("cache_write_input_tokens")),
                        "output": _int(usage.get("output_tokens")),
                        "reasoning": _int(usage.get("reasoning_output_tokens")),
                        "source": str(path),
                    }
                    records.append(record)
                elif item.get("type") == "compacted" or payload.get("type") == "context_compacted":
                    compactions.append({"timestamp": item.get("timestamp"), "time": timestamp,
                                        "source": str(path)})
    records.sort(key=lambda row: (row["time"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
                                  row["source"], row["response"] or ""))
    compactions.sort(key=lambda row: (row["time"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
                                      row["source"]))
    return records, compactions, notes


def _totals(rows: list[dict]) -> dict:
    result = {key: sum(row[key] for row in rows) for key in FIELDS}
    result["uncached"] = result["input"] - result["cached"] - result["cache_write"]
    result["reuse"] = round(result["cached"] / result["input"], 6) if result["input"] else 0.0
    return result


def _percentiles(values: list[int]) -> dict:
    if not values:
        return {"median": 0, "p90": 0, "p99": 0, "max": 0}
    values = sorted(values)

    def at(fraction: float) -> int:
        return values[round((len(values) - 1) * fraction)]

    return {"median": int(statistics.median(values)), "p90": at(0.90),
            "p99": at(0.99), "max": values[-1]}


def summarize(records: list[dict], compactions: list[dict], notes: list[str]) -> dict:
    by_root: dict[str, list[dict]] = collections.defaultdict(list)
    by_thread: dict[str, list[dict]] = collections.defaultdict(list)
    for row in records:
        by_root[row["root"] or "(none)"].append(row)
        by_thread[row["thread"] or "(none)"].append(row)

    roots = []
    for root, rows in by_root.items():
        totals = _totals(rows)
        start = next((row["time"] for row in rows if row["time"] is not None), None)
        end = next((row["time"] for row in reversed(rows) if row["time"] is not None), None)
        roots.append({
            "root": root,
            "requests": len(rows),
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "span_hours": round((end - start).total_seconds() / 3600, 3) if start and end else 0.0,
            **totals,
        })
    roots.sort(key=lambda row: row["span_hours"], reverse=True)

    compaction_rows = []
    for index, event in enumerate(compactions):
        later = [i for i, row in enumerate(records) if row["time"] and event["time"]
                 and row["time"] > event["time"]]
        stop = min(later) if later else len(records)
        before = [row for row in records[:stop] if row["time"] and event["time"]
                  and row["time"] <= event["time"]]
        if not before:
            continue
        current = before[-1]
        prior = before[-2] if len(before) > 1 else None
        compaction_rows.append({
            "index": index + 1,
            "timestamp": event["timestamp"],
            "record": {key: current[key] for key in
                       ("timestamp", "root", "thread", "response", "input", "cached", "output")},
            "record_uncached": current["input"] - current["cached"] - current["cache_write"],
            "prior": ({key: prior[key] for key in
                       ("timestamp", "root", "thread", "response", "input", "cached", "output")}
                      if prior else None),
            "prior_uncached": (prior["input"] - prior["cached"] - prior["cache_write"]
                               if prior else None),
        })

    totals = _totals(records)
    return {
        "schema": 1,
        "scope": "Codex client token records; not Strata server timing",
        "requests": len(records),
        "threads": len(by_thread),
        "roots": len(by_root),
        "totals": totals,
        "input_percentiles": _percentiles([row["input"] for row in records]),
        "uncached_percentiles": _percentiles(
            [row["input"] - row["cached"] - row["cache_write"] for row in records]),
        "by_root": roots,
        "compaction": {
            "events": len(compactions),
            "associated": len(compaction_rows),
            "uncached_input": sum(row["record_uncached"] for row in compaction_rows),
            "rows": compaction_rows,
        },
        "top_uncached": [
            {key: row[key] for key in
             ("timestamp", "root", "thread", "response", "input", "cached", "output")}
            | {"uncached": row["input"] - row["cached"] - row["cache_write"]}
            for row in sorted(records, key=lambda row:
                              row["input"] - row["cached"] - row["cache_write"],
                              reverse=True)[:20]
        ],
        "parse_notes": notes,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", type=pathlib.Path,
                        help="Codex rollout .jsonl files or directories")
    parser.add_argument("--output", type=pathlib.Path,
                        help="write JSON here instead of stdout")
    parser.add_argument("--top", type=int, default=20,
                        help="number of high-uncached events to keep (default: 20)")
    args = parser.parse_args(argv)
    if args.top < 0:
        parser.error("--top must be zero or positive")
    records, compactions, notes = parse(args.paths)
    result = summarize(records, compactions, notes)
    result["top_uncached"] = result["top_uncached"][:args.top]
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(json.dumps({"output": str(args.output), "requests": len(records),
                          "compactions": len(compactions), "parse_notes": len(notes)},
                         sort_keys=True))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
