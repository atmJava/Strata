#!/usr/bin/env python3
"""round_gate.py - check that a round's log entry in docs/MULTIPATH_LOG.md carries a valid Gate sheet.

docs/MULTIPATH_RULES.md (v0.2) says a round does not exist unless its Gate sheet is filled and
the test line says PASS (xfail/skip = FAIL).  This is the machine check: run it after writing a
round, before committing.

Usage:
  round_gate.py [log] [--round R11]
Exit code 0 when the checked round passes, 1 otherwise (each problem printed).
"""
from __future__ import annotations

import argparse
import re
import sys

REQUIRED = ("项", "验收判据", "实现", "测试", "生产", "台账", "剩余未完成采纳项")
ROUND_RE = re.compile(r"^## (R\d+)\b.*$", re.MULTILINE)
GATE_RE = re.compile(r"^### Gate\b", re.MULTILINE)


def _round_blocks(text: str) -> list[tuple[str, str]]:
    matches = list(ROUND_RE.finditer(text))
    blocks = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        blocks.append((match.group(1), text[match.start():end]))
    return blocks


def validate_log(text: str, round_name: str | None = None) -> list[str]:
    blocks = _round_blocks(text)
    if not blocks:
        return ["no '## R<n>' round heading found in the log"]
    names = [name for name, _ in blocks]
    if round_name:
        match = next((block for name, block in blocks if name == round_name), None)
        if match is None:
            return [f"round {round_name} not found (rounds: {', '.join(names[:6])}...)"]
        block = match
    else:
        block = blocks[0][1]

    errors: list[str] = []
    gate = GATE_RE.search(block)
    if not gate:
        return ["no '### Gate' section in the round"]
    section = block[gate.start():]
    next_heading = re.search(r"^#{2,3} ", section[len("### Gate"):], re.MULTILINE)
    if next_heading:
        section = section[: len("### Gate") + next_heading.start()]

    for key in REQUIRED:
        if not re.search(rf"^-\s*{key}\s*[:：]", section, re.MULTILINE):
            errors.append(f"Gate is missing '- {key}:'")

    test_line = next((line for line in section.splitlines()
                      if re.match(r"^-\s*测试\s*[:：]", line)), None)
    if test_line is not None:
        if not re.search(r"→\s*PASS", test_line):
            errors.append("Gate test line does not say '→ PASS'")
        if re.search(r"XFAIL|FAIL|skip", test_line, re.IGNORECASE):
            errors.append("Gate test line records a non-PASS result (xfail/skip/FAIL does not count)")
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
