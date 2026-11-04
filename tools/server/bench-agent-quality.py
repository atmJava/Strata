#!/usr/bin/env python3
"""Run deterministic coding tasks through Codex and score correctness, not speed.

The fixtures start from fresh directories. Reports contain only public fixture
names, process outcomes, hashes and usage counters; prompts, model messages,
reasoning and tool bodies are never copied into the report.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any


DEFAULT_MODEL = "qwen3.8-flash-next-iq3_xxs-abliterated"
TOOL_RULE = (
    " Use the provided tools normally and never print an XML tool-call block."
)
FINAL_RULE = (
    " When the tests pass, your final assistant message must contain only the exact "
    "text `RESULT=PASS`: no summary, explanation, blank line, or extra punctuation."
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fixture_suite_sha256(names: list[str]) -> str:
    suite = []
    for name in names:
        task = TASKS[name]
        suite.append(
            {
                "name": task.name,
                "prompt": task.prompt,
                "expected_files": task.expected_files,
                "protected_files": task.protected_files,
                "required_tool": task.required_tool,
            }
        )
    encoded = json.dumps(
        suite, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded)


def compare_reports(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    identity_fields = (
        "runner",
        "runner_profile",
        "model",
        "reasoning_effort",
        "fixture_suite_sha256",
        "prompt_suffix_sha256",
        "model_catalog_sha256",
        "provider_url",
    )
    identity_mismatches = {
        field: {
            "baseline": baseline.get(field),
            "candidate": candidate.get(field),
        }
        for field in identity_fields
        if baseline.get(field) != candidate.get(field)
    }
    if sorted(baseline.get("tasks", [])) != sorted(candidate.get("tasks", [])):
        identity_mismatches["tasks"] = {
            "baseline": baseline.get("tasks", []),
            "candidate": candidate.get("tasks", []),
        }

    baseline_rows = {row["task"]: row for row in baseline.get("rows", [])}
    candidate_rows = {row["task"]: row for row in candidate.get("rows", [])}
    task_names = sorted(set(baseline_rows) | set(candidate_rows))
    rows = []
    regressions = []
    shared_failures = []
    recoveries = []
    for task in task_names:
        before = baseline_rows.get(task)
        after = candidate_rows.get(task)
        if before is None or after is None:
            regressions.append({"task": task, "reason": "missing row"})
            continue
        before_pass = bool(before.get("quality_pass"))
        after_pass = bool(after.get("quality_pass"))
        if before_pass and not after_pass:
            regressions.append({"task": task, "reason": "quality pass regressed"})
        elif after_pass and not before_pass:
            recoveries.append(task)
        elif not before_pass and not after_pass:
            shared_failures.append(task)
        rows.append(
            {
                "task": task,
                "baseline_quality_pass": before_pass,
                "candidate_quality_pass": after_pass,
                "baseline_tool_calls": before.get("tool_calls", {}),
                "candidate_tool_calls": after.get("tool_calls", {}),
                "baseline_event_warnings": before.get("event_warnings"),
                "candidate_event_warnings": after.get("event_warnings"),
                "baseline_event_errors": before.get("event_errors"),
                "candidate_event_errors": after.get("event_errors"),
            }
        )

    if regressions:
        verdict = "reject_candidate_regression"
    elif identity_mismatches:
        verdict = "reject_incomparable"
    elif shared_failures:
        verdict = "blocked_shared_failures"
    else:
        verdict = "pass"
    return {
        "schema": 1,
        "scope": "paired quality comparison; no prompt or response text retained",
        "identity_fields": identity_fields,
        "identity_mismatches": identity_mismatches,
        "rows": rows,
        "candidate_regressions": regressions,
        "candidate_recoveries": recoveries,
        "shared_failures": shared_failures,
        "verdict": verdict,
    }


def write(root: pathlib.Path, relative: str, text: str) -> pathlib.Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@dataclass(frozen=True)
class Task:
    name: str
    prompt: str
    expected_files: tuple[str, ...]
    protected_files: tuple[str, ...]
    required_tool: str | None = None


TASKS: dict[str, Task] = {
    "single-file": Task(
        name="single-file",
        prompt=(
            "Work directly in the current repository. Fix the implementation bug without "
            "changing tests or signatures. Run `python -m pytest -q` until it passes. "
            + TOOL_RULE
            + FINAL_RULE
        ),
        expected_files=("calculator.py",),
        protected_files=("tests/test_calculator.py",),
    ),
    "cross-file": Task(
        name="cross-file",
        prompt=(
            "Work directly in the current repository. Fix the status filtering across the "
            "modules while preserving the existing public functions, and without changing "
            "tests. Run `python -m pytest -q` until it passes."
            + TOOL_RULE
            + FINAL_RULE
        ),
        expected_files=("orders/repository.py", "orders/service.py"),
        protected_files=("tests/test_orders.py",),
    ),
    "parser-edge": Task(
        name="parser-edge",
        prompt=(
            "Work directly in the current repository. Fix the parser so values may contain "
            "the delimiter while blank lines and malformed records still behave as the "
            "tests require without changing tests. Run `python -m pytest -q` until it passes. "
            + TOOL_RULE
            + FINAL_RULE
        ),
        expected_files=("config_parser.py",),
        protected_files=("tests/test_config_parser.py",),
    ),
    "diagnose-retry": Task(
        name="diagnose-retry",
        prompt=(
            "Work directly in the current repository. Diagnose the failing retry tests and "
            "fix the implementation, not the tests. Keep the public API and retry count. Run "
            "`python -m pytest -q` until it passes."
            + TOOL_RULE
            + FINAL_RULE
        ),
        expected_files=("retry.py",),
        protected_files=("tests/test_retry.py",),
    ),
    "transaction-rollback": Task(
        name="transaction-rollback",
        prompt=(
            "Work directly in the current repository. Fix reservation updates so a rejected "
            "reservation leaves stock and the reservation list unchanged, while successful "
            "reservations keep their existing behavior. Do not change tests or public "
            "signatures. Run `python -m pytest -q` until it passes without changing tests."
            + TOOL_RULE
            + FINAL_RULE
        ),
        expected_files=("inventory.py",),
        protected_files=("tests/test_inventory.py",),
    ),
    "long-context": Task(
        name="long-context",
        prompt=(
            "Work directly in the current repository. Read `docs/incident.md`, then fix the "
            "money totals implementation according to the compatibility rules there without "
            "changing tests or public signatures. Run `python -m pytest -q` until it passes."
            + TOOL_RULE
            + FINAL_RULE
        ),
        expected_files=("money.py",),
        protected_files=("tests/test_money.py",),
    ),
    "apply-patch": Task(
        name="apply-patch",
        prompt=(
            "Work directly in the current repository. Use the declared `apply_patch` tool to "
            "fix the implementation bug without changing tests or signatures. Do not edit "
            "source files through a shell command. Run `python -m pytest -q` until it passes. "
            + TOOL_RULE
            + FINAL_RULE
        ),
        expected_files=("calculator.py",),
        protected_files=("tests/test_calculator.py",),
        required_tool="apply_patch",
    ),
}


def build_fixture(task: Task, root: pathlib.Path) -> dict[str, str]:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    write(root, ".gitignore", "__pycache__/\n.pytest_cache/\n")
    if task.name in ("single-file", "apply-patch"):
        write(root, "calculator.py", "def add(left, right):\n    return left - right\n")
        write(
            root,
            "tests/test_calculator.py",
            "import pathlib\nimport sys\n\n"
            "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))\n"
            "import calculator\n\n"
            "def test_positive():\n    assert calculator.add(2, 3) == 5\n\n"
            "def test_negative():\n    assert calculator.add(-2, -3) == -5\n",
        )
    elif task.name == "cross-file":
        write(
            root,
            "orders/repository.py",
            "def orders_by_status(orders, status=None):\n"
            "    return [order for order in orders if status is None]\n",
        )
        write(
            root,
            "orders/service.py",
            "from .repository import orders_by_status\n\n"
            "def visible_orders(orders, status=None):\n"
            "    selected = orders_by_status(orders, status)\n"
            "    return [order for order in selected if order['active']]\n",
        )
        write(root, "orders/__init__.py", "")
        write(
            root,
            "tests/test_orders.py",
            "import pathlib\nimport sys\n\n"
            "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))\n"
            "from orders.service import visible_orders\n\n"
            "ORDERS = [\n"
            "    {'id': 1, 'status': 'paid', 'active': True},\n"
            "    {'id': 2, 'status': 'open', 'active': True},\n"
            "    {'id': 3, 'status': 'paid', 'active': False},\n"
            "]\n\n"
            "def test_all_visible():\n    assert [o['id'] for o in visible_orders(ORDERS)] == [1, 2]\n\n"
            "def test_status_filter():\n"
            "    assert [o['id'] for o in visible_orders(ORDERS, 'paid')] == [1]\n\n"
            "def test_unknown_status():\n    assert visible_orders(ORDERS, 'missing') == []\n",
        )
    elif task.name == "parser-edge":
        write(
            root,
            "config_parser.py",
            "def parse_pairs(text):\n"
            "    return dict(line.split('=', 1) for line in text.splitlines())\n",
        )
        write(
            root,
            "tests/test_config_parser.py",
            "import pathlib\nimport sys\n\n"
            "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))\n"
            "import config_parser\n\n"
            "def test_values_may_contain_equals():\n"
            "    assert config_parser.parse_pairs('a=1\\nb=two=2\\n') == {'a': '1', 'b': 'two=2'}\n\n"
            "def test_blank_lines_are_ignored():\n"
            "    assert config_parser.parse_pairs('\\n a = 1 \\n\\n b=2\\n') == {'a': '1', 'b': '2'}\n\n"
            "def test_malformed_records_are_ignored():\n"
            "    assert config_parser.parse_pairs('ok=1\\nbroken\\nlast=3') == {'ok': '1', 'last': '3'}\n",
        )
    elif task.name == "diagnose-retry":
        write(
            root,
            "retry.py",
            "def run_with_retry(operation, failures, attempts=3):\n"
            "    if attempts <= 0:\n"
            "        raise ValueError('attempts must be positive')\n"
            "    for _ in range(attempts):\n"
            "        try:\n"
            "            return operation()\n"
            "        except failures:\n"
            "            pass\n"
            "    return operation()\n",
        )
        write(
            root,
            "tests/test_retry.py",
            "import pathlib\nimport sys\nimport pytest\n\n"
            "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))\n"
            "import retry\n\n"
            "def test_success_after_two_failures():\n"
            "    calls = []\n"
            "    def operation():\n"
            "        calls.append(1)\n"
            "        if len(calls) < 3:\n"
            "            raise OSError('temporary')\n"
            "        return 'ok'\n"
            "    assert retry.run_with_retry(operation, OSError, attempts=3) == 'ok'\n"
            "    assert len(calls) == 3\n\n"
            "def test_retries_are_exhausted():\n"
            "    calls = []\n"
            "    def operation():\n"
            "        calls.append(1)\n"
            "        raise OSError('temporary')\n"
            "    with pytest.raises(OSError, match='temporary'):\n"
            "        retry.run_with_retry(operation, OSError, attempts=3)\n"
            "    assert len(calls) == 3\n",
        )
    elif task.name == "transaction-rollback":
        write(
            root,
            "inventory.py",
            "class Inventory:\n"
            "    def __init__(self, stock, reservation_limit):\n"
            "        self.stock = dict(stock)\n"
            "        self.reservation_limit = reservation_limit\n"
            "        self.reservations = []\n\n"
            "    def reserve(self, sku, quantity):\n"
            "        if quantity <= 0:\n"
            "            raise ValueError('quantity must be positive')\n"
            "        if sku not in self.stock or self.stock[sku] < quantity:\n"
            "            raise ValueError('not enough stock')\n"
            "        self.stock[sku] -= quantity\n"
            "        self.reservations.append((sku, quantity))\n"
            "        if len(self.reservations) > self.reservation_limit:\n"
            "            raise RuntimeError('reservation limit reached')\n",
        )
        write(
            root,
            "tests/test_inventory.py",
            "import pathlib\nimport sys\nimport pytest\n\n"
            "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))\n"
            "from inventory import Inventory\n\n"
            "def test_limit_failure_rolls_back_the_entire_reservation():\n"
            "    inventory = Inventory({'a': 5, 'b': 3}, reservation_limit=1)\n"
            "    inventory.reserve('a', 2)\n"
            "    with pytest.raises(RuntimeError, match='reservation limit'):\n"
            "        inventory.reserve('b', 1)\n"
            "    assert inventory.stock == {'a': 3, 'b': 3}\n"
            "    assert inventory.reservations == [('a', 2)]\n\n"
            "def test_success_and_validation_stay_unchanged():\n"
            "    inventory = Inventory({'a': 5}, reservation_limit=2)\n"
            "    inventory.reserve('a', 2)\n"
            "    assert inventory.stock == {'a': 3}\n"
            "    assert inventory.reservations == [('a', 2)]\n"
            "    with pytest.raises(ValueError, match='positive'):\n"
            "        inventory.reserve('a', 0)\n"
            "    with pytest.raises(ValueError, match='stock'):\n"
            "        inventory.reserve('missing', 1)\n",
        )
    elif task.name == "long-context":
        write(
            root,
            "money.py",
            "def format_total(values):\n"
            "    return f'{sum(float(value) for value in values):.2f}'\n",
        )
        write(
            root,
            "tests/test_money.py",
            "import pathlib\nimport sys\n\n"
            "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))\n"
            "import money\n\n"
            "def test_decimal_arithmetic_and_half_up_rounding():\n"
            "    assert money.format_total(['0.10', '0.20']) == '0.30'\n"
            "    assert money.format_total(['2.675']) == '2.68'\n"
            "    assert money.format_total(['1.004']) == '1.00'\n\n"
            "def test_input_order_is_not_mutated_and_values_stay_exact():\n"
            "    values = ['1234567890123456.78', '0.01']\n"
            "    before = list(values)\n"
            "    assert money.format_total(values) == '1234567890123456.79'\n"
            "    assert values == before\n",
        )
        archive = "".join(
            f"[archive {number:05d}] collector=payments shard={number % 17:02d} "
            f"status=healthy duplicate=false checksum={number * 7919 % 1000003:06d}\n"
            for number in range(2_000)
        )
        write(
            root,
            "docs/incident.md",
            "# Billing incident archive\n\n"
            + archive
            + "\n## Money formatting contract\n\n"
            "Values are decimal strings, not binary floating-point inputs. Add them with "
            "`Decimal`, round the final sum half up to two decimal places, return a plain "
            "two-decimal string, and do not mutate the caller's list. `format_total` remains "
            "the public function.\n",
        )
    else:
        raise ValueError(f"unknown task {task.name}")
    return {
        relative: sha256((root / relative).read_bytes())
        for relative in task.protected_files
    }


def event_summary(path: pathlib.Path, runner: str = "codex") -> dict[str, Any]:
    events: collections.Counter[str] = collections.Counter()
    tools: collections.Counter[str] = collections.Counter()
    errors = 0
    tool_errors = 0
    warnings = 0
    final = None
    session_id = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        kind = row.get("type")
        if isinstance(kind, str):
            events[kind] += 1
        if runner == "dsh":
            if kind == "session" and isinstance(row.get("sessionId"), str):
                session_id = row["sessionId"]
            elif kind == "final" and isinstance(row.get("text"), str):
                final = row["text"]
            elif kind == "tool_call":
                name = row.get("tool")
                tools[name if isinstance(name, str) else "tool"] += 1
            elif kind == "tool_result" and row.get("status") == "error":
                tool_errors += 1
            elif kind == "error":
                errors += 1
            elif kind == "status" and row.get("phase") == "turn_end":
                reason = row.get("reason")
                if isinstance(reason, dict) and reason.get("kind") not in (None, "completed"):
                    errors += 1
            continue
        item = row.get("item")
        if kind != "item.completed" or not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type in {
            "function_call",
            "custom_tool_call",
            "command_execution",
            "mcp_tool_call",
            "file_change",
        }:
            name = "apply_patch" if item_type == "file_change" else item.get("name")
            tools[name if isinstance(name, str) else item_type] += 1
        elif item_type == "agent_message" and isinstance(item.get("text"), str):
            final = item["text"]
        elif item_type == "error":
            message = item.get("message")
            if (
                isinstance(message, str)
                and (
                    (
                        message.startswith("Model metadata for `")
                        and "Defaulting to fallback metadata" in message
                    )
                    or "Codex is ignoring" in message
                )
            ):
                warnings += 1
            else:
                errors += 1
    return {
        "events": dict(sorted(events.items())),
        "tool_calls": dict(sorted(tools.items())),
        "tool_call_count": sum(tools.values()),
        "tool_errors": tool_errors,
        "event_errors": errors,
        "event_warnings": warnings,
        "final": final,
        "session_id": session_id,
    }


def dsh_command(dsh: str, task: Task, patch: pathlib.Path | None = None) -> list[str]:
    command = [dsh, "--profile", "headless"]
    if patch:
        command += ["--patch", str(patch.resolve())]
    return command + ["--json", task.prompt]


def task_prompt(task: Task, suffix: str | None = None) -> str:
    return task.prompt + (f"\n\n{suffix}" if suffix else "")


def run_task(
    task: Task,
    *,
    runner: str,
    codex: str,
    dsh: str,
    dsh_patch: pathlib.Path | None,
    model: str,
    provider_url: str | None,
    model_catalog: pathlib.Path | None,
    api_key_env: str,
    reasoning_effort: str,
    prompt_suffix: str | None,
    work_root: pathlib.Path,
    raw_root: pathlib.Path,
    timeout_s: int,
) -> dict[str, Any]:
    root = work_root / task.name
    protected_hashes = build_fixture(task, root)
    raw_root.mkdir(parents=True, exist_ok=True)
    events_path = raw_root / f"{task.name}.jsonl"
    final_path = raw_root / f"{task.name}-last.txt"
    prompt = task_prompt(task, prompt_suffix)
    if runner == "dsh":
        command = dsh_command(dsh, task, dsh_patch)
        command[-1] = prompt
    else:
        command = [
            codex,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "-C",
            str(root),
            "--json",
            "--color",
            "never",
            "--output-last-message",
            str(final_path),
            "-c",
            f'model="{model}"',
            "-c",
            "model_context_window=262144",
            "-c",
            "model_auto_compact_token_limit=210000",
            "-c",
            f'model_reasoning_effort="{reasoning_effort}"',
            "-c",
            'approval_policy="never"',
            "-c",
            'sandbox_mode="danger-full-access"',
            prompt,
        ]
        if provider_url:
            command[2:2] = ["--ignore-user-config"]
            at = command.index(f'model="{model}"') - 1
            command[at:at] = [
                "-c",
                'model_provider="b4"',
                "-c",
                'model_providers.b4.name="Strata agent quality"',
                "-c",
                f'model_providers.b4.base_url="{provider_url}"',
                "-c",
                f'model_providers.b4.env_key="{api_key_env}"',
                "-c",
                'model_providers.b4.wire_api="responses"',
            ]
        if model_catalog:
            command[-1:-1] = ["-c", f'model_catalog_json="{model_catalog}"']
    started = time.monotonic()
    with events_path.open("wb") as output:
        try:
            proc = subprocess.run(
                command,
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                env=os.environ.copy(),
                timeout=timeout_s,
            )
            returncode = proc.returncode
            timed_out = False
        except subprocess.TimeoutExpired:
            returncode = 124
            timed_out = True
    wall_s = time.monotonic() - started
    event = event_summary(events_path, runner)
    final = event["final"]
    if not final and final_path.exists():
        final = final_path.read_text(encoding="utf-8").strip()
    test = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=120,
    )
    current_hashes = {
        relative: sha256((root / relative).read_bytes())
        for relative in task.protected_files
    }
    tests_unchanged = current_hashes == protected_hashes
    tests_pass = test.returncode == 0
    final_pass = isinstance(final, str) and final.strip() == "RESULT=PASS"
    return {
        "task": task.name,
        "runner_profile": "dsh-headless" if runner == "dsh" else "codex-exec",
        "runner_session_id": event["session_id"],
        "dsh_patch": str(dsh_patch) if runner == "dsh" and dsh_patch else None,
        "prompt_suffix_sha256": sha256(prompt_suffix.encode("utf-8")) if prompt_suffix else None,
        "wall_s": round(wall_s, 3),
        "wall_scope": "runner_process; includes client-side post-turn auxiliary work",
        "returncode": returncode,
        "timed_out": timed_out,
        "tests_pass": tests_pass,
        "tests_unchanged": tests_unchanged,
        "final_pass": final_pass,
        "final_sha256": sha256((final or "").encode("utf-8")),
        "test_output_sha256": sha256(test.stdout.encode("utf-8")),
        "event_errors": event["event_errors"],
        "event_warnings": event["event_warnings"],
        "events": event["events"],
        "tool_calls": event["tool_calls"],
        "tool_call_count": event["tool_call_count"],
        "tool_errors": event["tool_errors"],
        "quality_pass": (
            returncode == 0
            and not timed_out
            and tests_pass
            and tests_unchanged
            and final_pass
            and event["event_errors"] == 0
            and (
                task.required_tool is None
                or event["tool_calls"].get(task.required_tool, 0) > 0
            )
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner", choices=("codex", "dsh"), default="codex")
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--dsh", default="dsh")
    parser.add_argument(
        "--dsh-patch",
        type=pathlib.Path,
        help="explicit DSH profile overlay used by the headless runner; does not edit the user's profile",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=f"Codex model slug (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--model-label",
        default=None,
        help="model/provider label recorded in the report; never changes the runner command",
    )
    parser.add_argument(
        "--provider-url",
        default="http://127.0.0.1:18080/v1",
        help="OpenAI-compatible base URL; pass an empty string to use the Codex user/provider config",
    )
    parser.add_argument("--api-key-env", default="STRATA_API_KEY")
    parser.add_argument(
        "--model-catalog",
        type=pathlib.Path,
        help="Codex model catalog to load; needed for real apply_patch and model metadata",
    )
    parser.add_argument("--reasoning-effort", choices=("low", "medium", "high"), default="high")
    parser.add_argument(
        "--prompt-suffix",
        help="test-only instruction appended to every fixture prompt; its bytes are hashed in the report",
    )
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--tasks", default=",".join(TASKS))
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("BASELINE", "CANDIDATE"),
        help="compare two completed quality reports without running a task",
    )
    parser.add_argument(
        "--work-root",
        type=pathlib.Path,
        default=pathlib.Path("/tmp/strata-agent-quality"),
    )
    parser.add_argument(
        "--output",
        type=pathlib.Path,
        default=pathlib.Path("/tmp/strata-agent-quality.json"),
    )
    options = parser.parse_args(argv)
    model = options.model or DEFAULT_MODEL
    if options.dsh_patch and not options.dsh_patch.is_file():
        parser.error(f"--dsh-patch does not exist: {options.dsh_patch}")
    names = [name.strip() for name in options.tasks.split(",") if name.strip()]
    unknown = set(names) - set(TASKS)
    if not names or unknown:
        parser.error(f"unknown tasks: {', '.join(sorted(unknown)) or '(none selected)'}")
    if options.compare:
        baseline_path, candidate_path = map(pathlib.Path, options.compare)
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        comparison = compare_reports(baseline, candidate)
        print(json.dumps(comparison, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if comparison["verdict"] == "pass" else 2

    if (
        options.runner == "codex"
        and options.provider_url
        and not os.environ.get(options.api_key_env)
    ):
        parser.error(f"{options.api_key_env} is required")
    raw_root = options.work_root / "raw"
    rows = []
    for name in names:
        print(f"running {name}", flush=True)
        row = run_task(
            TASKS[name],
            runner=options.runner,
            codex=options.codex,
            dsh=options.dsh,
            dsh_patch=options.dsh_patch,
            model=model,
            provider_url=options.provider_url,
            model_catalog=options.model_catalog,
            api_key_env=options.api_key_env,
            reasoning_effort=options.reasoning_effort,
            prompt_suffix=options.prompt_suffix,
            work_root=options.work_root,
            raw_root=raw_root,
            timeout_s=options.timeout,
        )
        rows.append(row)
        print(
            f"  wall={row['wall_s']:.3f}s quality={row['quality_pass']} "
            f"tests={row['tests_pass']} unchanged={row['tests_unchanged']} "
            f"final={row['final_pass']} tools={row['tool_call_count']}",
            flush=True,
        )
    result = {
        "schema": 5,
        "scope": (
            "deterministic coding fixtures through an agent CLI; "
            "no prompt or response text retained; wall_s is the full runner process"
        ),
        "wall_scope": "runner_process; includes client-side post-turn auxiliary work",
        "runner": options.runner,
        "runner_profile": "dsh-headless" if options.runner == "dsh" else "codex-exec",
        "dsh_patch": (
            str(options.dsh_patch)
            if options.runner == "dsh" and options.dsh_patch
            else None
        ),
        "model": options.model_label
        or (model if options.runner == "codex" else None),
        "reasoning_effort": options.reasoning_effort,
        "provider_url": (
            options.provider_url if options.runner == "codex" else None
        ),
        "model_catalog": (
            str(options.model_catalog.resolve()) if options.model_catalog else None
        ),
        "model_catalog_sha256": (
            sha256(options.model_catalog.read_bytes()) if options.model_catalog else None
        ),
        "fixture_suite_sha256": fixture_suite_sha256(names),
        "prompt_suffix_sha256": (
            sha256(options.prompt_suffix.encode("utf-8"))
            if options.prompt_suffix
            else None
        ),
        "tasks": names,
        "rows": rows,
        "summary": {
            "passed": sum(row["quality_pass"] for row in rows),
            "total": len(rows),
            "all_pass": all(row["quality_pass"] for row in rows),
            "all_tests_pass": all(row["tests_pass"] for row in rows),
            "all_tests_unchanged": all(row["tests_unchanged"] for row in rows),
        },
    }
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result["summary"], sort_keys=True), flush=True)
    return 0 if result["summary"]["all_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
