#!/usr/bin/env python3
"""Measure real Codex edit-test loops without storing command or output text.

The probe reuses the privacy-safe parser in ``trace-agent-tasks.py`` and adds
only derived facts: command category, exact-repeat counts, exit success/failure
when it is present, model turns before the first edit, model turns between an
edit and the first test, and model work after a failed/repeated test.

All raw command lines, patches and tool outputs remain in memory.  The JSON
result contains counts, timings, token totals and opaque root/thread IDs only.

Example:
    probe-edit-test-loop.py ~/.codex/sessions --output /tmp/edit-test-loop.json
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import importlib.util
import json
import pathlib
import statistics
import sys
from typing import Iterable


def _load_trace():
    path = pathlib.Path(__file__).with_name("trace-agent-tasks.py")
    spec = importlib.util.spec_from_file_location("trace_agent_tasks", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


trace = _load_trace()


def _seconds(value: float) -> float:
    return round(max(0.0, value), 3)


def _percentile_ms(values: Iterable[float]) -> dict:
    rows = sorted(max(0, round(value * 1000)) for value in values)
    if not rows:
        return {"samples": 0, "median": 0, "p90": 0, "p99": 0, "max": 0}
    return {
        "samples": len(rows),
        **trace.percentile(rows),
    }


def _percentile_number(values: Iterable[float]) -> dict:
    rows = sorted(max(0, round(value)) for value in values)
    if not rows:
        return {"samples": 0, "median": 0, "p90": 0, "p99": 0, "max": 0}
    return {
        "samples": len(rows),
        **trace.percentile(rows),
    }


def _model_summary(rows: list[dict]) -> dict:
    return {
        "requests": len(rows),
        "span_s": _seconds(sum(row["seconds"] for row in rows)),
        "input": sum(row["input"] for row in rows),
        "cached": sum(row["cached"] for row in rows),
        "uncached": sum(row["input"] - row["cached"] - row["cache_write"] for row in rows),
        "output": sum(row["output"] for row in rows),
        "reasoning": sum(row["reasoning"] for row in rows),
    }


def _tool_summary(rows: list[dict]) -> dict:
    by_kind = collections.defaultdict(list)
    for row in rows:
        by_kind[str(row.get("command_kind") or "other")].append(row)
    return {
        "calls": len(rows),
        "span_s": _seconds(sum(row["seconds"] for row in rows)),
        "by_kind": {
            key: {
                "calls": len(values),
                "span_s": _seconds(sum(row["seconds"] for row in values)),
            }
            for key, values in sorted(by_kind.items())
        },
    }


def analyze(parsed: dict) -> dict:
    tools, accounting = trace.tool_spans(parsed)
    models = trace.model_spans(parsed)
    model_by_root: dict[str | None, list[dict]] = collections.defaultdict(list)
    tools_by_root: dict[str | None, list[dict]] = collections.defaultdict(list)
    for row in models:
        model_by_root[row.get("root")].append(row)
    for row in tools:
        tools_by_root[row.get("root")].append(row)
    for rows in model_by_root.values():
        rows.sort(key=lambda row: (row["end"], row["start"]))
    for rows in tools_by_root.values():
        rows.sort(key=lambda row: row["start"])

    roots = []
    first_edit_model_turns: list[float] = []
    edit_to_test_s: list[float] = []
    edit_to_test_model_turns: list[float] = []
    first_tool_kind: collections.Counter[str] = collections.Counter()
    after_test = collections.defaultdict(list)
    roots_with_edit = 0
    roots_with_edit_test = 0
    tests_total = 0
    test_repeats = 0
    test_repeat_after_success = 0
    test_repeat_after_failure = 0
    test_repeat_after_edit = 0
    test_repeat_without_edit = 0
    test_repeat_after_success_without_edit = 0
    test_repeat_after_failure_without_edit = 0
    failed_tests = 0
    succeeded_tests = 0
    unknown_tests = 0
    tests_before_edit = 0
    edit_count = 0
    post_failure_models: set[str] = set()
    pre_edit_models: dict[str, dict] = {}
    pre_edit_tools: dict[str, dict] = {}
    edit_test_models: dict[str, dict] = {}
    edit_test_tools: dict[str, dict] = {}

    for root, root_info in parsed["roots"].items():
        start = root_info.get("start")
        if start is None:
            continue
        calls = tools_by_root.get(root, [])
        spans = model_by_root.get(root, [])
        if calls:
            first_tool_kind[str(calls[0].get("command_kind") or "other")] += 1

        edits = [row for row in calls if row.get("command_kind") == "edit"]
        tests = [row for row in calls if row.get("command_kind") == "test"]
        edit_count += len(edits)
        tests_total += len(tests)
        if edits:
            roots_with_edit += 1

        first_edit = edits[0] if edits else None
        first_test = next(
            (row for row in tests if first_edit is None or row["start"] >= first_edit["start"]),
            None,
        )
        if first_edit is not None and first_test is not None:
            roots_with_edit_test += 1
            delta = (first_test["start"] - first_edit["end"]).total_seconds()
            edit_to_test_s.append(delta)
            edit_model_turns = [
                row for row in spans
                if row["start"] >= first_edit["end"] and row["end"] <= first_test["start"]
            ]
            edit_to_test_model_turns.append(float(len(edit_model_turns)))
        if first_edit is not None:
            before = [row for row in spans if row["end"] <= first_edit["start"]]
            first_edit_model_turns.append(float(len(before)))
            tests_before_edit += sum(
                1 for row in tests if row["start"] < first_edit["start"]
            )
            for row in before:
                key = str(row.get("response"))
                pre_edit_models.setdefault(key, row)
            for row in calls:
                if row["end"] <= first_edit["start"]:
                    pre_edit_tools.setdefault(str(row.get("call_id")), row)
        if first_edit is not None and first_test is not None:
            for row in spans:
                if row["start"] >= first_edit["end"] and row["end"] <= first_test["start"]:
                    edit_test_models.setdefault(str(row.get("response")), row)
            for row in calls:
                if row["start"] >= first_edit["end"] and row["end"] <= first_test["start"]:
                    edit_test_tools.setdefault(str(row.get("call_id")), row)

        seen_tests: dict[str, dict] = {}
        root_after_test = collections.defaultdict(list)
        root_repeats = 0
        root_repeat_after_success = 0
        root_repeat_after_failure = 0
        root_repeat_after_edit = 0
        root_repeat_without_edit = 0
        root_repeat_after_success_without_edit = 0
        root_repeat_after_failure_without_edit = 0
        for row in tests:
            failed = row.get("failed")
            if failed is True:
                failed_tests += 1
            elif failed is False:
                succeeded_tests += 1
            else:
                unknown_tests += 1

            next_model = next(
                (model for model in spans if model["start"] >= row["end"]),
                None,
            )
            bucket = (
                "after_success" if failed is False
                else "after_failure" if failed is True
                else "after_unknown"
            )
            if next_model is not None:
                if not any(existing.get("response") == next_model.get("response")
                           for existing in root_after_test[bucket]):
                    root_after_test[bucket].append(next_model)
                if failed is True and next_model.get("response"):
                    post_failure_models.add(str(next_model["response"]))

            signature = row.get("command_signature")
            if signature:
                previous = seen_tests.get(signature)
                if previous is not None:
                    root_repeats += 1
                    previous_failed = previous.get("failed")
                    edited_between = any(
                        edit["start"] >= previous["end"] and edit["end"] <= row["start"]
                        for edit in edits
                    )
                    if edited_between:
                        root_repeat_after_edit += 1
                    else:
                        root_repeat_without_edit += 1
                    if previous_failed is False:
                        root_repeat_after_success += 1
                        if not edited_between:
                            root_repeat_after_success_without_edit += 1
                    elif previous_failed is True:
                        root_repeat_after_failure += 1
                        if not edited_between:
                            root_repeat_after_failure_without_edit += 1
                seen_tests[signature] = row
        test_repeats += root_repeats
        test_repeat_after_success += root_repeat_after_success
        test_repeat_after_failure += root_repeat_after_failure
        test_repeat_after_edit += root_repeat_after_edit
        test_repeat_without_edit += root_repeat_without_edit
        test_repeat_after_success_without_edit += root_repeat_after_success_without_edit
        test_repeat_after_failure_without_edit += root_repeat_after_failure_without_edit
        for bucket, rows in root_after_test.items():
            seen = {
                row.get("response")
                for row in after_test[bucket]
            }
            after_test[bucket].extend(
                row for row in rows
                if row.get("response") not in seen
            )

        roots.append({
            "root": root,
            "status": root_info.get("status"),
            "start": trace.iso(start),
            "first_tool_kind": calls[0].get("command_kind") if calls else None,
            "first_edit_s": _seconds((first_edit["start"] - start).total_seconds())
            if first_edit else None,
            "model_turns_before_first_edit": len([
                row for row in spans
                if first_edit and row["end"] <= first_edit["start"]
            ]) if first_edit else None,
            "first_edit_to_test_s": _seconds((first_test["start"] - first_edit["end"]).total_seconds())
            if first_edit and first_test else None,
            "model_turns_between_edit_and_test": len([
                row for row in spans
                if first_edit and first_test
                and row["start"] >= first_edit["end"] and row["end"] <= first_test["start"]
            ]) if first_edit and first_test else None,
            "before_first_edit": {
                "model": _model_summary([
                    row for row in spans if first_edit and row["end"] <= first_edit["start"]
                ]),
                "tools": _tool_summary([
                    row for row in calls if first_edit and row["end"] <= first_edit["start"]
                ]),
            } if first_edit else None,
            "edit_to_first_test": {
                "model": _model_summary([
                    row for row in spans
                    if first_edit and first_test
                    and row["start"] >= first_edit["end"] and row["end"] <= first_test["start"]
                ]),
                "tools": _tool_summary([
                    row for row in calls
                    if first_edit and first_test
                    and row["start"] >= first_edit["end"] and row["end"] <= first_test["start"]
                ]),
            } if first_edit and first_test else None,
            "edits": len(edits),
            "tests": len(tests),
            "tests_before_first_edit": sum(
                1 for row in tests if first_edit and row["start"] < first_edit["start"]
            ),
            "failed_tests": sum(1 for row in tests if row.get("failed") is True),
            "succeeded_tests": sum(1 for row in tests if row.get("failed") is False),
            "exact_test_repeats": root_repeats,
            "exact_test_repeats_after_success": root_repeat_after_success,
            "exact_test_repeats_after_failure": root_repeat_after_failure,
            "exact_test_repeats_after_edit": root_repeat_after_edit,
            "exact_test_repeats_without_edit": root_repeat_without_edit,
            "exact_test_repeats_after_success_without_edit":
                root_repeat_after_success_without_edit,
            "exact_test_repeats_after_failure_without_edit":
                root_repeat_after_failure_without_edit,
            "post_test_model": {
                key: _model_summary(rows) for key, rows in sorted(root_after_test.items())
            },
        })

    roots.sort(
        key=lambda row: (
            row["exact_test_repeats_after_failure"],
            row["exact_test_repeats"],
            row["first_edit_s"] or 0,
        ),
        reverse=True,
    )
    roots_with_loops = roots_with_edit_test
    return {
        "schema": 1,
        "scope": "Codex client events; derived edit/test categories; not Strata server timing",
        "files": parsed.get("files", 0),
        "roots": len(roots),
        "threads": len({row.get("thread") for row in models if row.get("thread")}),
        "tool_accounting": accounting,
        "coverage": {
            "roots_with_tools": len(tools_by_root),
            "roots_with_edit": roots_with_edit,
            "roots_with_edit_and_test": roots_with_loops,
            "edit_test_root_share_pct": round(100 * roots_with_loops / len(roots), 3)
            if roots else 0.0,
        },
        "first_tool_kind": dict(first_tool_kind.most_common()),
        "edits": edit_count,
        "tests": {
            "calls": tests_total,
            "succeeded": succeeded_tests,
            "failed": failed_tests,
            "unknown": unknown_tests,
            "before_first_edit": tests_before_edit,
            "exact_repeats": test_repeats,
            "exact_repeats_pct": round(100 * test_repeats / tests_total, 3)
            if tests_total else 0.0,
            "exact_repeats_after_success": test_repeat_after_success,
            "exact_repeats_after_failure": test_repeat_after_failure,
            "exact_repeats_after_edit": test_repeat_after_edit,
            "exact_repeats_without_edit": test_repeat_without_edit,
            "exact_repeats_after_success_without_edit":
                test_repeat_after_success_without_edit,
            "exact_repeats_after_failure_without_edit":
                test_repeat_after_failure_without_edit,
        },
        "phase_cost": {
            "before_first_edit": {
                "model": _model_summary(list(pre_edit_models.values())),
                "tools": _tool_summary(list(pre_edit_tools.values())),
            },
            "edit_to_first_test": {
                "model": _model_summary(list(edit_test_models.values())),
                "tools": _tool_summary(list(edit_test_tools.values())),
            },
        },
        "first_edit": {
            "samples": len(first_edit_model_turns),
            "latency_ms": _percentile_ms(row["first_edit_s"] or 0 for row in roots
                                          if row["first_edit_s"] is not None),
            "model_turns_before": _percentile_number(first_edit_model_turns),
        },
        "edit_to_first_test": {
            "samples": len(edit_to_test_s),
            "latency_ms": _percentile_ms(edit_to_test_s),
            "model_turns_between": _percentile_number(edit_to_test_model_turns),
        },
        "model_after_test": {
            key: _model_summary(rows) for key, rows in sorted(after_test.items())
        },
        "post_failure_unique_model_requests": len(post_failure_models),
        "post_failure_model": _model_summary([
            row for row in models if str(row.get("response")) in post_failure_models
        ]),
        "roots_by_loop_cost": roots,
        "parse_notes": parsed.get("notes", []),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args(argv)
    parsed = trace.parse(args.paths)
    parsed["files"] = len(trace.files(args.paths))
    result = analyze(parsed)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(json.dumps({
            "output": str(args.output),
            "roots": result["roots"],
            "tests": result["tests"]["calls"],
            "edit_test_roots": result["coverage"]["roots_with_edit_and_test"],
            "parse_notes": len(result["parse_notes"]),
        }, sort_keys=True))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
