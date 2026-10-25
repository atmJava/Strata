#!/usr/bin/env python3
"""round_gate.py - check a round's log entry against the Gate sheet (PORTING_RULES.md §3).

Usage:
  round_gate.py [log] [--round R11]
Exit 0 when the checked round passes; 1 otherwise (each problem printed).

The gate is a format + wording check, not proof the feature works: it rejects missing fields,
non-PASS test lines (xfail/skip/FAIL/TODO/未跑) and empty values where a value is required.
"""
from __future__ import annotations

import argparse
import re
import sys

REQUIRED = ("轮次", "项", "验收判据", "实现", "测试", "生产", "证据", "回滚", "台账",
            "剩余未完成项", "没测什么")
NON_PASS = re.compile(r"xfail|skip|FAIL|TODO|未跑", re.IGNORECASE)
ROUND_RE = re.compile(r"^## (R\d+)\b.*$", re.MULTILINE)
GATE_RE = re.compile(r"^### Gate\b", re.MULTILINE)


def _round_blocks(text: str) -> list[tuple[str, str]]:
    matches = list(ROUND_RE.finditer(text))
    blocks = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        blocks.append((match.group(1), text[match.start():end]))
    return blocks


def _gate_section(block: str) -> str | None:
    gate = GATE_RE.search(block)
    if not gate:
        return None
    section = block[gate.start():]
    next_heading = re.search(r"^#{2,3} ", section[len("### Gate"):], re.MULTILINE)
    return section[: len("### Gate") + next_heading.start()] if next_heading else section


def _field(section: str, key: str) -> str | None:
    match = re.search(rf"^-[ \t]*{key}[ \t]*[:：][ \t]*(.*)$", section, re.MULTILINE)
    return match.group(1).strip() if match else None


def validate_log(text: str, round_name: str | None = None) -> list[str]:
    blocks = _round_blocks(text)
    if not blocks:
        return ["no '## R<n>' round heading found in the log"]
    names = [name for name, _ in blocks]
    if round_name:
        block = next((body for name, body in blocks if name == round_name), None)
        if block is None:
            return [f"round {round_name} not found (rounds: {', '.join(names[:6])}...)"]
    else:
        block = blocks[0][1]

    section = _gate_section(block)
    if section is None:
        return ["no '### Gate' section in the round"]

    errors: list[str] = []
    for key in REQUIRED:
        if _field(section, key) is None:
            errors.append(f"Gate is missing '- {key}:'")

    def value(key: str) -> str:
        return _field(section, key) or ""

    test_line = _field(section, "测试")
    if test_line is not None:
        if "→" not in test_line or "PASS" not in test_line:
            errors.append("Gate test line does not say '→ PASS'")
        if NON_PASS.search(test_line):
            errors.append("Gate test line records a non-PASS result (xfail/skip/FAIL/TODO/未跑 does not count)")

    if not value("项"):
        errors.append("Gate item ('项') is empty")
    if not value("证据"):
        errors.append("Gate evidence ('证据') is empty")
    if value("生产") and not value("回滚"):
        errors.append("Gate changed production but left '回滚' empty")
    if not value("没测什么"):
        errors.append("Gate '没测什么' is empty")

    remaining = value("剩余未完成项")
    if remaining and not re.match(r"^\d+", remaining):
        errors.append("Gate '剩余未完成项' must start with an integer")
    if value("实现") == "" and not value("项").startswith("测量"):
        errors.append("Gate implementation ('实现') is empty and this is not a measurement round")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", nargs="?", default="docs/MULTIPATH_LOG.md")
    parser.add_argument("--round", dest="round_name")
    options = parser.parse_args()
    text = open(options.log, encoding="utf-8").read()
    errors = validate_log(text, options.round_name)
    for error in errors:
        print(f"GATE FAIL: {error}")
    print("GATE PASS" if not errors else f"{len(errors)} problem(s)")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
