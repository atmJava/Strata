#!/usr/bin/env python3
"""Estimate the real-session yield of the opt-in Codex context diet.

The probe scans Codex rollout JSONL files and reports only counts, sizes,
classifications and derived estimates. It never prints or writes prompt,
assistant, reasoning or tool-output text.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import importlib.util
import json
import math
import pathlib
import sys
from typing import Iterable


UTC = dt.timezone.utc
ROOT = pathlib.Path(__file__).resolve().parents[2]
PROXY_PATH = ROOT / "tools" / "server" / "agent-shape-proxy.py"


def load_proxy_module():
    spec = importlib.util.spec_from_file_location("agent_shape_proxy", PROXY_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {PROXY_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PROXY = load_proxy_module()


def timestamp(value) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def number(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def files(paths: Iterable[pathlib.Path]) -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    for path in paths:
        found.extend(path.rglob("*.jsonl") if path.is_dir() else [path])
    return sorted({path.resolve() for path in found})


def turn_id(payload: dict) -> str | None:
    metadata = payload.get("internal_chat_message_metadata_passthrough")
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("turn_id")
    return value if isinstance(value, str) and value else None


def reference_chars(root: pathlib.Path, conversation: str, call_id: str, output: str) -> int:
    path = (
        root
        / f"{conversation[:24]}-{PROXY.digest(call_id)[:24]}-"
        f"{len(output)}-{PROXY.digest(output)[:12]}.txt"
    )
    reference = (
        "[Strata context diet: full tool output is saved unchanged locally.\n"
        f"bytes={len(output.encode('utf-8'))} sha256={PROXY.digest(output)}\n"
        f"path={path}\n"
        f"read: sed -n '1,200p' {path}\n"
        f"search: rg -n 'PATTERN' {path}\n"
        "Use the file when older details are required.]"
    )
    return len(reference)


def parse(paths: Iterable[pathlib.Path], artifact_root: pathlib.Path) -> dict:
    requests: list[dict] = []
    outputs: list[dict] = []
    compactions: list[dict] = []
    item_threads: dict[str, str] = {}
    turn_threads: dict[str, str] = {}
    notes: list[str] = []
    scanned = files(paths)

    for path in scanned:
        try:
            handle = path.open(encoding="utf-8")
        except OSError as exc:
            notes.append(f"{path}: {exc}")
            continue
        with handle:
            for line_number, line in enumerate(handle, 1):
                try:
                    row = json.loads(line)
                except (TypeError, ValueError):
                    notes.append(f"{path}:{line_number}: invalid JSON")
                    continue
                if not isinstance(row, dict):
                    continue
                payload = row.get("payload")
                if not isinstance(payload, dict):
                    continue
                when = timestamp(row.get("timestamp"))
                kind = row.get("type")
                payload_kind = payload.get("type")

                if kind == "token_usage_record":
                    usage = payload.get("usage")
                    thread = payload.get("thread_id")
                    turn = payload.get("turn_id")
                    if not isinstance(usage, dict) or not isinstance(thread, str):
                        continue
                    if isinstance(turn, str):
                        turn_threads[turn] = thread
                    requests.append(
                        {
                            "path": str(path),
                            "line": line_number,
                            "at": when,
                            "thread": thread,
                            "input": number(usage.get("input_tokens")),
                            "cached": number(
                                usage.get("cached_input_tokens")
                                or usage.get("cached_tokens")
                            ),
                            "output": number(usage.get("output_tokens")),
                        }
                    )

                elif kind == "event_msg" and payload_kind == "item_completed":
                    item = payload.get("item")
                    thread = payload.get("thread_id")
                    if isinstance(item, dict) and isinstance(thread, str):
                        item_id = item.get("id")
                        if isinstance(item_id, str):
                            item_threads[item_id] = thread

                elif kind == "response_item":
                    item_type = payload.get("type")
                    if item_type not in (
                        "function_call_output",
                        "custom_tool_call_output",
                        "tool_search_output",
                    ):
                        continue
                    output = payload.get("output")
                    if output is None and item_type == "tool_search_output":
                        output = payload.get("tools")
                    if isinstance(output, str):
                        chars = len(output)
                        foldable = (
                            item_type != "tool_search_output"
                            and chars >= PROXY.DIET_MIN_CHARS
                            and not PROXY.failed_tool_output(output)
                        )
                        reference = reference_chars(
                            artifact_root,
                            hashlib.sha256(
                                str(payload.get("id") or payload.get("call_id") or "").encode()
                            ).hexdigest(),
                            str(payload.get("call_id") or ""),
                            output,
                        )
                    elif output is None:
                        chars = 0
                        foldable = False
                        reference = 0
                    else:
                        chars = len(
                            json.dumps(output, ensure_ascii=False, separators=(",", ":"))
                        )
                        foldable = False
                        reference = 0
                    turn = turn_id(payload)
                    thread = item_threads.get(str(payload.get("id") or ""))
                    if thread is None and turn:
                        thread = turn_threads.get(turn)
                    outputs.append(
                        {
                            "path": str(path),
                            "line": line_number,
                            "at": when,
                            "thread": thread,
                            "type": item_type,
                            "chars": chars,
                            "foldable": foldable,
                            "reference_chars": reference,
                        }
                    )

                elif kind == "compacted":
                    latest = payload.get("latest_token_usage_record")
                    thread = latest.get("thread_id") if isinstance(latest, dict) else None
                    if isinstance(thread, str):
                        compactions.append(
                            {
                                "path": str(path),
                                "line": line_number,
                                "at": when,
                                "thread": thread,
                            }
                        )

    return {
        "files": scanned,
        "requests": requests,
        "outputs": outputs,
        "compactions": compactions,
        "notes": notes,
    }


def event_after(left: dict, right: dict) -> bool:
    if left.get("at") is not None and right.get("at") is not None:
        return left["at"] > right["at"]
    if left.get("at") is not None and right.get("at") is None:
        return True
    if left.get("at") is None and right.get("at") is not None:
        return False
    if left["path"] != right["path"]:
        return left["path"] > right["path"]
    return left["line"] > right["line"]


def summarize(data: dict, min_chars: int, artifact_root: pathlib.Path) -> dict:
    requests = collections.defaultdict(list)
    compactions = collections.defaultdict(list)
    for row in data["requests"]:
        requests[row["thread"]].append(row)
    for row in data["compactions"]:
        compactions[row["thread"]].append(row)

    total_input = sum(row["input"] for row in data["requests"])
    total_cached = sum(row["cached"] for row in data["requests"])
    total_output_tokens = sum(row["output"] for row in data["requests"])
    output_rows = data["outputs"]
    total_output_chars = sum(row["chars"] for row in output_rows)
    large = [row for row in output_rows if row["chars"] >= min_chars]
    foldable = [row for row in large if row["foldable"]]

    visible_requests = 0
    original_visible_chars = 0
    reference_visible_chars = 0
    estimated_saved_chars = 0
    exposure_histogram = collections.Counter()
    saved_by_tool = collections.Counter()
    saved_by_file = collections.Counter()

    for row in foldable:
        thread = row["thread"]
        if not isinstance(thread, str):
            exposure_histogram["unmapped"] += 1
            continue
        cutoff = next(
            (
                event
                for event in sorted(
                    (event for event in compactions[thread] if event_after(event, row)),
                    key=lambda event: (event.get("at") or dt.datetime.min.replace(tzinfo=UTC), event["line"]),
                )
            ),
            None,
        )
        count = 0
        for request in requests[thread]:
            if not event_after(request, row):
                continue
            if cutoff is not None and not event_after(cutoff, request):
                continue
            count += 1
        exposure_histogram["0" if count == 0 else "1" if count == 1 else "2-4" if count <= 4 else "5+"] += 1
        if count == 0:
            continue
        saved_each = max(0, row["chars"] - row["reference_chars"])
        visible_requests += count
        original_visible_chars += row["chars"] * count
        reference_visible_chars += row["reference_chars"] * count
        estimated_saved_chars += saved_each * count
        saved_by_tool[row["type"]] += saved_each * count
        saved_by_file[row["path"]] += saved_each * count

    estimated_saved_tokens = math.ceil(estimated_saved_chars / 4)
    share_pct = (
        round(estimated_saved_tokens * 100 / total_input, 4)
        if total_input
        else 0.0
    )

    file_input = collections.Counter()
    for row in data["requests"]:
        file_input[row["path"]] += row["input"]
    file_share_pct = []
    for path, tokens in file_input.items():
        file_share_pct.append(
            round(saved_by_file[path] * 25 / tokens, 4)
            if tokens
            else 0.0
        )
    file_share_pct.sort()

    def percentile(values: list[float], fraction: float) -> float:
        if not values:
            return 0.0
        index = min(len(values) - 1, math.ceil(len(values) * fraction) - 1)
        return values[max(0, index)]

    tools = collections.defaultdict(
        lambda: {
            "outputs": 0,
            "large": 0,
            "foldable": 0,
            "retained_large": 0,
            "chars": 0,
            "foldable_chars": 0,
        }
    )
    for row in output_rows:
        entry = tools[row["type"]]
        entry["outputs"] += 1
        entry["chars"] += row["chars"]
        if row["chars"] >= min_chars:
            entry["large"] += 1
            if row["foldable"]:
                entry["foldable"] += 1
                entry["foldable_chars"] += row["chars"]
            else:
                entry["retained_large"] += 1

    return {
        "scope": "stored Codex client events; token and character estimates are not server timing",
        "files": len(data["files"]),
        "requests": len(data["requests"]),
        "tool_outputs": len(output_rows),
        "input_tokens": total_input,
        "cached_tokens": total_cached,
        "cached_pct": round(total_cached * 100 / total_input, 3) if total_input else 0.0,
        "output_tokens": total_output_tokens,
        "tool_output_chars": total_output_chars,
        "large_outputs": len(large),
        "foldable_outputs": len(foldable),
        "foldable_chars": sum(row["chars"] for row in foldable),
        "visible_foldable_requests": visible_requests,
        "original_visible_chars": original_visible_chars,
        "reference_visible_chars": reference_visible_chars,
        "estimated_saved_chars": estimated_saved_chars,
        "estimated_saved_tokens": estimated_saved_tokens,
        "estimated_saved_input_pct": share_pct,
        "sessions_with_foldable_output": sum(
            1 for path in file_input if saved_by_file[path] > 0
        ),
        "sessions_at_or_above_1pct": sum(1 for value in file_share_pct if value >= 1.0),
        "session_estimated_saved_pct": {
            "p50": percentile(file_share_pct, 0.50),
            "p75": percentile(file_share_pct, 0.75),
            "p90": percentile(file_share_pct, 0.90),
            "max": round(file_share_pct[-1], 4) if file_share_pct else 0.0,
        },
        "compactions": len(data["compactions"]),
        "exposure_histogram": dict(sorted(exposure_histogram.items())),
        "saved_chars_by_output_type": dict(sorted(saved_by_tool.items())),
        "by_output_type": {
            name: dict(values) for name, values in sorted(tools.items())
        },
        "min_chars": min_chars,
        "artifact_root_chars": len(str(artifact_root)),
        "notes": data["notes"],
    }


def main(args: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=pathlib.Path)
    parser.add_argument("--min-chars", type=int, default=PROXY.DIET_MIN_CHARS)
    parser.add_argument(
        "--artifact-root",
        type=pathlib.Path,
        default=pathlib.Path("/tmp/strata-context-diet-real-yield/artifacts"),
    )
    parser.add_argument("--output", type=pathlib.Path)
    namespace = parser.parse_args(args)
    if namespace.min_chars < 1:
        parser.error("--min-chars must be positive")
    result = summarize(
        parse(namespace.paths, namespace.artifact_root),
        namespace.min_chars,
        namespace.artifact_root,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if namespace.output:
        namespace.output.parent.mkdir(parents=True, exist_ok=True)
        namespace.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
