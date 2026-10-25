#!/usr/bin/env python3
"""preflight.py - check the durable state before an autonomous optimization iteration.

Checks the durable state against the queue, then the live server:
  - WORK_QUEUE.md is self-consistent and its count is what STATE.md says;
  - STATE.md's current item exists in the queue;
  - /health is 200 and /metrics answers (engine version printed).

Usage: preflight.py [--base-url URL] [--api-key KEY] [--offline]
Exit code 0 only when every check passes.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import round_gate  # noqa: E402


def read_state(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^-\s*([^：:]+)[：:]\s*(.*)$", line)
        if match:
            fields[match.group(1).strip()] = match.group(2).strip()
    return fields


def check_state(state: dict[str, str], queue_text: str) -> list[str]:
    errors: list[str] = []
    ids, unfinished, problems = round_gate.parse_queue(queue_text)
    errors.extend(problems)

    declared = re.match(r"^(\d+)", state.get("未完成采纳项", ""))
    if not declared:
        errors.append("STATE.md has no '未完成采纳项：<n>' line")
    elif int(declared.group(1)) != unfinished:
        errors.append(f"STATE.md says {declared.group(1)} unfinished but the queue counts {unfinished}")

    item = re.split(r"[\s（(]", state.get("当前项", "").strip())[0] if state.get("当前项") else ""
    if not item:
        errors.append("STATE.md has no '当前项：<id>' line")
    elif item not in ids:
        errors.append(f"STATE.md current item '{item}' is not in the work queue")

    return errors


def fetch(base_url: str, path: str, api_key: str) -> dict:
    request = urllib.request.Request(base_url.rstrip("/") + path,
                                     headers={"Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("MP_BASE_URL", "http://192.168.3.14:8080"))
    parser.add_argument("--api-key", default=os.environ.get("MP_API_KEY", "111"))
    parser.add_argument("--state", default="docs/STATE.md")
    parser.add_argument("--queue", default="docs/WORK_QUEUE.md")
    parser.add_argument("--offline", action="store_true")
    options = parser.parse_args()

    state = read_state(open(options.state, encoding="utf-8").read())
    queue_text = open(options.queue, encoding="utf-8").read()
    errors = check_state(state, queue_text)
    print(f"state: item={state.get('当前项', '?')} unfinished={state.get('未完成采纳项', '?')}")

    if not options.offline:
        try:
            health = fetch(options.base_url, "/health", options.api_key)
            metrics = fetch(options.base_url, "/metrics", options.api_key)
            engine = metrics.get("engine", {})
            print(f"server: health ok, engine={engine.get('version')} slots={engine.get('batch_slots')}")
        except Exception as error:  # noqa: BLE001 - report, do not crash
            errors.append(f"server unreachable: {type(error).__name__}: {error}")
    else:
        print("server: skipped (offline)")

    for error in errors:
        print(f"PREFLIGHT FAIL: {error}")
    print("PREFLIGHT PASS" if not errors else f"{len(errors)} problem(s)")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
