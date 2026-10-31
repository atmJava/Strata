#!/usr/bin/env python3
"""Run privacy-safe control/diet Codex coding-task pairs through Strata.

Each arm starts from a fresh synthetic repository. The runner records wall
time, tool-call counts, proxy request/usage summaries, edit correctness and
artifact recovery. It never prints prompts, assistant messages, reasoning or
tool bodies; Codex event streams remain private temporary files.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import pathlib
import shutil
import statistics
import subprocess
import sys
import time
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_MODEL = "qwen3.8-flash-next-iq3_xxs-abliterated"
REPORT_MARKER = "CONTEXT_DIET_REPORT_MARKER=cedar-7319"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_report(path: pathlib.Path, lines: int = 1000) -> None:
    rows = [
        f"row={index:04d} group={index % 17:02d} checksum={digest(f'report-{index}')[:32]}"
        for index in range(lines)
    ]
    rows.append(REPORT_MARKER)
    body = "\n".join(rows) + "\n"
    write(path, f"REPORT = {body!r}\nprint(REPORT, end='')\n")


def build_task(task: str, path: pathlib.Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    make_report(path / "tools" / "report.py")
    write(
        path / "calc.py",
        "def add(left, right):\n"
        "    return left - right\n\n"
        "def multiply(left, right):\n"
        "    return left * right\n",
    )
    if task == "matrix":
        cases = "\n".join(
            f"@pytest.mark.parametrize('value', [{index}])"
            for index in range(70)
        )
        test_body = (
            cases
            + "\n"
            + "def test_add(value):\n"
            + "    assert calc.add(value, 7) == value + 7\n"
            + "    assert calc.multiply(value, 3) == value * 3\n"
        )
    else:
        test_body = (
            "def test_add():\n"
            "    assert calc.add(9, 4) == 13\n"
            "    assert calc.add(-2, 5) == 3\n\n"
            "def test_multiply():\n"
            "    assert calc.multiply(6, 7) == 42\n"
        )
    write(
        path / "tests" / "test_calc.py",
        "import pathlib\n"
        "import sys\n\n"
        "import pytest\n\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))\n"
        "import calc\n\n"
        + test_body,
    )
    if task == "parser":
        write(
            path / "parser.py",
            "def parse_pairs(text):\n"
            "    return dict(line.split('=', 1) for line in text.splitlines() if line)\n",
        )
        write(
            path / "tests" / "test_parser.py",
            "import pathlib\n"
            "import sys\n\n"
            "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))\n"
            "import parser\n\n"
            "def test_parse_pairs():\n"
            "    assert parser.parse_pairs('a=1\\nb=two=2\\n') == {'a': '1', 'b': 'two=2'}\n",
        )
    write(
        path / "README.md",
        "# Context diet fixture\n\n"
        "Run `python tools/report.py` for a successful large report. "
        "Run `python -m pytest -q` for the task gate.\n",
    )


def task_prompt(task: str) -> str:
    parser_step = (
        " The parser test also has an implementation bug in parser.py."
        if task == "parser"
        else ""
    )
    return (
        "Work directly in the current repository. Use the native exec_command tool "
        "for every shell action. To edit files, execute a Python or sed command "
        "through exec_command; never print a textual XML/tool-call block.\n"
        "1. Run `python tools/report.py` exactly once, without a pipe, redirection "
        "or tail. It is a successful large diagnostic output; do not repeat it or "
        "paste it.\n"
        "2. Run `python -m pytest -q`.\n"
        "3. Fix the implementation rather than weakening or deleting tests."
        + parser_step
        + "\n"
        "4. Rerun the tests and continue until they pass.\n"
        "5. Reply with exactly `RESULT=PASS` when the repository tests pass."
    )


def proxy_log_offset(path: pathlib.Path | None) -> int:
    if path is None or not path.exists():
        return 0
    return path.stat().st_size


def read_proxy_summary(path: pathlib.Path | None, offset: int) -> dict[str, Any]:
    if path is None or not path.exists():
        return {
            "requests": 0,
            "http_errors": 0,
            "input_tokens": 0,
            "cached_tokens": 0,
            "output_tokens": 0,
            "duration_s": 0.0,
            "diet_candidates": 0,
            "diet_replaced": 0,
            "diet_saved_chars": 0,
        }
    with path.open("rb") as handle:
        handle.seek(offset)
        data = handle.read()
    summary: dict[str, Any] = {
        "requests": 0,
        "http_errors": 0,
        "input_tokens": 0,
        "cached_tokens": 0,
        "output_tokens": 0,
        "duration_s": 0.0,
        "diet_candidates": 0,
        "diet_replaced": 0,
        "diet_saved_chars": 0,
    }
    for raw in data.splitlines():
        try:
            row = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(row, dict) or not row.get("response"):
            continue
        summary["requests"] += 1
        response = row.get("response")
        if not isinstance(response, dict):
            continue
        status = response.get("http_status")
        if isinstance(status, int) and status >= 400:
            summary["http_errors"] += 1
        usage = response.get("usage")
        if isinstance(usage, dict):
            summary["input_tokens"] += int(usage.get("input_tokens") or 0)
            summary["cached_tokens"] += int(usage.get("cached_tokens") or 0)
            summary["output_tokens"] += int(usage.get("output_tokens") or 0)
        duration = response.get("duration_s")
        if isinstance(duration, (int, float)):
            summary["duration_s"] += float(duration)
        diet = row.get("context_diet")
        if isinstance(diet, dict):
            summary["diet_candidates"] += int(diet.get("candidates") or 0)
            summary["diet_replaced"] += int(diet.get("replaced") or 0)
            summary["diet_saved_chars"] += int(diet.get("saved_chars") or 0)
    summary["duration_s"] = round(summary["duration_s"], 3)
    return summary


def codex_event_summary(path: pathlib.Path) -> dict[str, Any]:
    events: collections.Counter[str] = collections.Counter()
    tool_calls: collections.Counter[str] = collections.Counter()
    errors: list[str] = []
    final: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            continue
        kind = row.get("type")
        if isinstance(kind, str):
            events[kind] += 1
        item = row.get("item")
        if kind == "item.completed" and isinstance(item, dict):
            item_type = item.get("type")
            if item_type in {
                "function_call",
                "custom_tool_call",
                "command_execution",
                "mcp_tool_call",
            }:
                name = item.get("name")
                if not isinstance(name, str):
                    name = item_type
                tool_calls[name] += 1
            elif item_type == "agent_message" and isinstance(item.get("text"), str):
                final = item["text"]
            elif item_type == "error":
                message = item.get("message")
                errors.append(digest(message if isinstance(message, str) else repr(item)))
    return {
        "events": dict(sorted(events.items())),
        "tool_calls": dict(sorted(tool_calls.items())),
        "tool_call_count": sum(tool_calls.values()),
        "errors": len(errors),
        "final": final,
    }


def artifact_summary(
    path: pathlib.Path | None, task: str
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "files": 0,
        "bytes": 0,
        "min_mode": None,
        "marker_recovered": False,
        "marker_files": 0,
    }
    if path is None or not path.is_dir():
        return result
    modes: list[int] = []
    for item in path.rglob("*"):
        if not item.is_file():
            continue
        stat = item.stat()
        result["files"] += 1
        result["bytes"] += stat.st_size
        modes.append(stat.st_mode & 0o777)
        try:
            data = item.read_bytes()
        except OSError:
            continue
        if REPORT_MARKER.encode() in data:
            result["marker_recovered"] = True
            result["marker_files"] += 1
    if modes:
        result["min_mode"] = f"{min(modes):03o}"
    return result


def expected_pass(path: pathlib.Path) -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=path,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=120,
    )
    return proc.returncode == 0, digest(proc.stdout)


def run_arm(
    *,
    task: str,
    arm: str,
    path: pathlib.Path,
    codex: str,
    model: str,
    provider_url: str,
    api_key_env: str,
    proxy_log: pathlib.Path | None,
    diet_dir: pathlib.Path | None,
    timeout_s: int,
    raw_dir: pathlib.Path,
) -> dict[str, Any]:
    build_task(task, path)
    raw_dir.mkdir(parents=True, exist_ok=True)
    events_path = raw_dir / f"{task}-{arm}.jsonl"
    last_path = raw_dir / f"{task}-{arm}-last.txt"
    offset = proxy_log_offset(proxy_log)
    command = [
        codex,
        "exec",
        "--ignore-user-config",
        "--ephemeral",
        "--skip-git-repo-check",
        "-C",
        str(path),
        "--json",
        "--color",
        "never",
        "--output-last-message",
        str(last_path),
        "-c",
        'model_provider="b4"',
        "-c",
        f'model="{model}"',
        "-c",
        'model_context_window=262144',
        "-c",
        'model_auto_compact_token_limit=210000',
        "-c",
        'model_reasoning_effort="low"',
        "-c",
        'model_providers.b4.name="Strata B4 benchmark"',
        "-c",
        f'model_providers.b4.base_url="{provider_url}"',
        "-c",
        f'model_providers.b4.env_key="{api_key_env}"',
        "-c",
        'model_providers.b4.wire_api="responses"',
        "-c",
        'approval_policy="never"',
        "-c",
        'sandbox_mode="danger-full-access"',
        task_prompt(task),
    ]
    started = time.monotonic()
    timed_out = False
    with events_path.open("wb") as output:
        try:
            proc = subprocess.run(
                command,
                cwd=path,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                env=os.environ.copy(),
                timeout=timeout_s,
            )
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            returncode = 124
    wall_s = time.monotonic() - started
    event = codex_event_summary(events_path)
    pass_gate, test_hash = expected_pass(path)
    final = event["final"]
    if final is None and last_path.exists():
        final = last_path.read_text(encoding="utf-8").strip()
    return {
        "task": task,
        "arm": arm,
        "wall_s": round(wall_s, 3),
        "returncode": returncode,
        "timed_out": timed_out,
        "tests_pass": pass_gate,
        "test_output_sha256": test_hash,
        "final_pass": isinstance(final, str) and final.strip() == "RESULT=PASS",
        "final_sha256": digest(final or ""),
        "events": event["events"],
        "tool_calls": event["tool_calls"],
        "tool_call_count": event["tool_call_count"],
        "event_errors": event["errors"],
        "proxy": read_proxy_summary(proxy_log, offset),
        "artifacts": artifact_summary(diet_dir, task),
    }


def summarize_pairs(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pairs: list[dict[str, Any]] = []
    by_key: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    for row in rows:
        key = (row["task"], row["pair"])
        by_key.setdefault(key, {})[row["arm"]] = row
    walls: list[float] = []
    prompt_reductions: list[float] = []
    for (task, pair), arms in sorted(by_key.items()):
        if "control" not in arms or "diet" not in arms:
            continue
        control = arms["control"]
        diet = arms["diet"]
        reduction = (control["wall_s"] - diet["wall_s"]) * 100 / control["wall_s"]
        walls.append(reduction)
        c_input = control["proxy"]["input_tokens"]
        d_input = diet["proxy"]["input_tokens"]
        prompt_reduction = (
            (c_input - d_input) * 100 / c_input if c_input else 0.0
        )
        prompt_reductions.append(prompt_reduction)
        pairs.append(
            {
                "task": task,
                "pair": pair,
                "control_wall_s": control["wall_s"],
                "diet_wall_s": diet["wall_s"],
                "wall_reduction_pct": round(reduction, 3),
                "control_requests": control["proxy"]["requests"],
                "diet_requests": diet["proxy"]["requests"],
                "control_input_tokens": c_input,
                "diet_input_tokens": d_input,
                "input_reduction_pct": round(prompt_reduction, 3),
                "control_tools": control["tool_call_count"],
                "diet_tools": diet["tool_call_count"],
                "control_tests_pass": control["tests_pass"],
                "diet_tests_pass": diet["tests_pass"],
                "control_final_pass": control["final_pass"],
                "diet_final_pass": diet["final_pass"],
                "diet_artifacts": diet["artifacts"],
            }
        )
    median_wall = statistics.median(walls) if walls else 0.0
    all_correct = all(
        row["tests_pass"]
        and row["final_pass"]
        and row["returncode"] == 0
        and not row["timed_out"]
        and row["proxy"]["http_errors"] == 0
        for row in rows
    )
    no_turn_regression = all(
        pair["diet_requests"] <= pair["control_requests"]
        and pair["diet_tools"] <= pair["control_tools"]
        for pair in pairs
    )
    artifact_recovered = all(
        not pair["diet_artifacts"]["files"]
        or (
            pair["diet_artifacts"]["marker_recovered"]
            and pair["diet_artifacts"]["min_mode"] == "600"
        )
        for pair in pairs
    )
    return {
        "pairs": pairs,
        "median_wall_reduction_pct": round(median_wall, 3),
        "median_input_reduction_pct": round(
            statistics.median(prompt_reductions) if prompt_reductions else 0.0,
            3,
        ),
        "all_correct": all_correct,
        "no_turn_regression": no_turn_regression,
        "artifact_recovered": artifact_recovered,
        "gate_pass": (
            len(pairs) >= 3
            and all_correct
            and no_turn_regression
            and artifact_recovered
            and median_wall >= 1.0
        ),
    }


def main(args: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--control-url", default="http://127.0.0.1:18082/v1"
    )
    parser.add_argument(
        "--diet-url", default="http://127.0.0.1:18083/v1"
    )
    parser.add_argument("--control-log", type=pathlib.Path)
    parser.add_argument("--diet-log", type=pathlib.Path)
    parser.add_argument("--diet-dir", type=pathlib.Path)
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-key-env", default="B4_API_KEY")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--pairs", type=int, default=3)
    parser.add_argument(
        "--tasks",
        default="simple,matrix,parser",
        help="comma-separated task order; repeats when pairs exceed tasks",
    )
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=pathlib.Path("/tmp/context-diet-codex-live.json"),
    )
    parser.add_argument(
        "--work-root",
        type=pathlib.Path,
        default=pathlib.Path("/tmp/context-diet-codex-live"),
    )
    options = parser.parse_args(args)
    api_key = os.environ.get(options.api_key_env)
    if not api_key:
        parser.error(f"{options.api_key_env} is required")
    tasks = [item for item in options.tasks.split(",") if item]
    if not tasks:
        parser.error("--tasks must not be empty")
    unknown = set(tasks) - {"simple", "matrix", "parser"}
    if unknown:
        parser.error(f"unknown tasks: {', '.join(sorted(unknown))}")
    options.work_root.mkdir(parents=True, exist_ok=True)
    raw_dir = options.work_root / "raw"
    rows: list[dict[str, Any]] = []
    for pair in range(1, options.pairs + 1):
        task = tasks[(pair - 1) % len(tasks)]
        for arm, url, proxy_log, diet_dir in (
            ("control", options.control_url, options.control_log, None),
            ("diet", options.diet_url, options.diet_log, options.diet_dir),
        ):
            path = options.work_root / f"pair-{pair}-{task}-{arm}"
            print(f"running pair={pair} task={task} arm={arm}", flush=True)
            row = run_arm(
                task=task,
                arm=arm,
                path=path,
                codex=options.codex,
                model=options.model,
                provider_url=url,
                api_key_env=options.api_key_env,
                proxy_log=proxy_log,
                diet_dir=diet_dir,
                timeout_s=options.timeout,
                raw_dir=raw_dir,
            )
            row["pair"] = pair
            rows.append(row)
            print(
                f"  wall={row['wall_s']:.3f}s tests={row['tests_pass']} "
                f"final={row['final_pass']} tools={row['tool_call_count']} "
                f"requests={row['proxy']['requests']}",
                flush=True,
            )
    result = {
        "scope": (
            "synthetic coding fixtures through Codex 0.164 -> Strata opt-in "
            "proxy; private event streams omitted"
        ),
        "model": options.model,
        "tasks": tasks,
        "rows": rows,
        "summary": summarize_pairs(rows),
    }
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(result["summary"], ensure_ascii=False, sort_keys=True),
        flush=True,
    )
    return 0 if result["summary"]["gate_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
