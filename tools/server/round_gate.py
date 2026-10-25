#!/usr/bin/env python3
"""round_gate.py - check a round's log entry against the Gate sheet (PORTING_RULES.md §3).

Usage:
  round_gate.py [log] [--round R11]
Exit 0 when the checked round passes; 1 otherwise (each problem printed).

The gate is a format + wording check, not proof the feature works: it rejects missing fields,
non-PASS test lines (xfail/skip/FAIL/TODO/未跑) and empty values where a value is required.
Queue counts are compared only for the latest round (no --round): an older round records a snapshot.
"""
from __future__ import annotations

import argparse
import re
import sys

REQUIRED = ("轮次", "项", "验收判据", "实现", "测试", "生产", "证据", "回滚", "台账",
            "剩余未完成项", "没测什么")
NON_PASS = re.compile(r"xfail|skip|FAIL|TODO|未跑", re.IGNORECASE)
ROUND_RE = re.compile(r"^## (R\d+|V\d+)\b.*$", re.MULTILINE)
# V0–V3 are batch headings (SELF_ITERATION_PLAN §4); they carry the same Gate as a round.
BATCH_ROW = re.compile(r"^V\d+$")
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


def parse_queue(text: str) -> tuple[list[str], int, list[str]]:
    """Return (ids, unfinished count, problems) from a WORK_QUEUE-style markdown table."""
    ids: list[str] = []
    unfinished = 0
    problems: list[str] = []
    declared = None
    for line in text.splitlines():
        match = re.match(r"^\s*未完成采纳项\s*[:：]\s*(\d+)", line)
        if match:
            declared = int(match.group(1))
            continue
        row = line.strip()
        if not row.startswith("|") or set(row) <= set("|-: "):
            continue
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        if len(cells) < 5 or cells[0] in ("ID", ""):
            continue
        item_id, _, _, status = cells[0], cells[1], cells[2], cells[3]
        if not item_id or item_id.startswith("ID"):
            continue
        ids.append(item_id)
        if BATCH_ROW.match(item_id):
            continue                  # a batch row (V0–V3) is a milestone; it is not a backlog item
        if status not in ("已完成", "否决") and not status.startswith("搁置"):
            unfinished += 1
    if declared is None:
        problems.append("queue has no '未完成采纳项：<n>' line")
    elif declared != unfinished:
        problems.append(f"queue says 未完成采纳项 {declared} but the table counts {unfinished}")
    return ids, unfinished, problems


def validate_log(text: str, round_name: str | None = None, queue_text: str | None = None) -> list[str]:
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

    if queue_text is not None:
        ids, unfinished, problems = parse_queue(queue_text)
        errors.extend(problems)
        # The count is a snapshot taken when the round was written: a later discovery-round item
        # must not retroactively fail a past round. Comparing to the current queue is a write-time
        # check, so it only runs for the latest round (no --round given).
        if round_name is None:
            gate_count = re.match(r"^(\d+)", value("剩余未完成项"))
            if gate_count and gate_count.group(1) != str(unfinished):
                errors.append(f"Gate says {gate_count.group(1)} unfinished but the queue counts {unfinished}"
                              " (expected after a discovery round adds items; the next round's Gate must match)")
        item = re.split(r"[\s（(]", value("项").replace("测量：", "").strip())[0] if value("项") else ""
        if item and item not in ids:
            errors.append(f"Gate item '{item}' is not in the work queue")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", nargs="?", default="docs/MULTIPATH_LOG.md")
    parser.add_argument("--round", dest="round_name")
    parser.add_argument("--queue", default="docs/WORK_QUEUE.md")
    options = parser.parse_args()
    text = open(options.log, encoding="utf-8").read()
    queue_text = None
    try:
        queue_text = open(options.queue, encoding="utf-8").read()
    except OSError:
        pass
    errors = validate_log(text, options.round_name, queue_text)
    for error in errors:
        print(f"GATE FAIL: {error}")
    print("GATE PASS" if not errors else f"{len(errors)} problem(s)")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
