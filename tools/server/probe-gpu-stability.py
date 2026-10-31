#!/usr/bin/env python3
"""Read recent NVIDIA driver faults and the current device state.

This probe never changes a clock, power limit, process or configuration.  It joins the
kernel's NVRM Xid events with a current ``nvidia-smi`` snapshot so a failed inference can
be classified as a GPU/runtime failure rather than guessed from the model response.

Examples:

    probe-gpu-stability.py
    probe-gpu-stability.py --since "2026-09-01 00:00:00"
    probe-gpu-stability.py --journal kernel.log --json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


XID_RE = re.compile(
    r"NVRM:\s+Xid\s+\(PCI:(?P<pci>[0-9a-fA-F:.]+)\):\s*"
    r"(?P<code>\d+)(?:,\s*(?P<detail>.*))?"
)
TIMESTAMP_RE = re.compile(
    r"^(?P<stamp>\d{4}-\d{2}-\d{2}T\S+|[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})"
)
PID_RE = re.compile(r"\bpid=(?P<pid>\d+)\b")
PROCESS_RE = re.compile(r"\bname=(?P<name>[^\s,]+)")

# These codes are worth retaining as evidence.  The list is not a diagnosis: Xid codes can
# have several causes and a reset or application error may be enough even for codes absent
# from this set.
GPU_FAULT_CODES = {13, 31, 43, 45, 62, 79, 109, 119, 120, 140, 154}
RESET_CODES = {79, 154}

DEVICE_FIELDS = (
    "index",
    "pci.bus_id",
    "name",
    "driver_version",
    "memory.used",
    "memory.total",
    "temperature.gpu",
    "power.draw",
    "power.limit",
    "clocks.sm",
    "clocks.mem",
    "utilization.gpu",
    "persistence_mode",
    "clocks_throttle_reasons.active",
)


def _number(text: str):
    text = text.strip()
    if text in ("", "[N/A]", "N/A"):
        return None
    match = re.fullmatch(r"(-?\d+(?:\.\d+)?)(?:\s*(?:MiB|W|MHz|%))?", text)
    if not match:
        return text
    value = match.group(1)
    return float(value) if "." in value else int(value)


def parse_xid_lines(text: str) -> list[dict]:
    """Return only NVRM Xid events, with no claim about their cause."""
    events: list[dict] = []
    for line in text.splitlines():
        match = XID_RE.search(line)
        if not match:
            continue
        stamp = TIMESTAMP_RE.match(line)
        detail = (match.group("detail") or "").strip()
        pid_match = PID_RE.search(detail)
        process_match = PROCESS_RE.search(detail)
        code = int(match.group("code"))
        events.append({
            "timestamp": stamp.group("stamp") if stamp else None,
            "pci": match.group("pci").lower(),
            "code": code,
            "detail": detail,
            "pid": int(pid_match.group("pid")) if pid_match else None,
            "process": process_match.group("name") if process_match else None,
            "fault_candidate": code in GPU_FAULT_CODES,
            "reset_required": code in RESET_CODES or "GPU Reset Required" in detail,
        })
    return events


def _event_time(value: str | None):
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        current_year = dt.datetime.now().year
        return dt.datetime.strptime(f"{current_year} {value}", "%Y %b %d %H:%M:%S")
    except ValueError:
        return None


def summarize_events(events: list[dict], cluster_seconds: int = 5) -> dict:
    by_code: dict[str, int] = {}
    by_pci: dict[str, int] = {}
    for event in events:
        by_code[str(event["code"])] = by_code.get(str(event["code"]), 0) + 1
        by_pci[event["pci"]] = by_pci.get(event["pci"], 0) + 1

    incidents: list[dict] = []
    last_by_pci: dict[str, dt.datetime] = {}
    incident_by_pci: dict[str, int] = {}
    untimed = 0
    for event in events:
        event_time = _event_time(event["timestamp"])
        previous = last_by_pci.get(event["pci"])
        if event_time is None:
            untimed += 1
            incidents.append({
                "timestamp": event["timestamp"],
                "pci": event["pci"],
                "event_count": 1,
                "codes": [event["code"]],
            })
            continue
        if previous is None or (event_time - previous).total_seconds() > cluster_seconds:
            incidents.append({
                "timestamp": event["timestamp"],
                "last_timestamp": event["timestamp"],
                "pci": event["pci"],
                "event_count": 1,
                "codes": [event["code"]],
            })
            incident_by_pci[event["pci"]] = len(incidents) - 1
        else:
            incident = incidents[incident_by_pci[event["pci"]]]
            incident["last_timestamp"] = event["timestamp"]
            incident["event_count"] += 1
            incident["codes"].append(event["code"])
        last_by_pci[event["pci"]] = event_time

    return {
        "count": len(events),
        "incident_count": len(incidents),
        "incident_cluster_seconds": cluster_seconds,
        "untimed_event_count": untimed,
        "fault_candidate_count": sum(bool(event["fault_candidate"]) for event in events),
        "reset_required": any(event["reset_required"] for event in events),
        "by_code": dict(sorted(by_code.items(), key=lambda item: int(item[0]))),
        "by_pci": dict(sorted(by_pci.items())),
        "first_timestamp": events[0]["timestamp"] if events else None,
        "last_timestamp": events[-1]["timestamp"] if events else None,
        "incidents": incidents,
    }


def parse_device_rows(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        fields = [part.strip() for part in line.split(",")]
        if len(fields) != len(DEVICE_FIELDS):
            continue
        row = {}
        for name, value in zip(DEVICE_FIELDS, fields):
            key = name.replace(".", "_")
            row[key] = _number(value)
        rows.append(row)
    return rows


def device_snapshot() -> dict:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return {"available": False, "reason": "nvidia-smi was not found", "devices": []}
    command = [exe, f"--query-gpu={','.join(DEVICE_FIELDS)}", "--format=csv,noheader"]
    try:
        proc = subprocess.run(command, text=True, capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"available": False, "reason": f"{type(error).__name__}: {error}", "devices": []}
    if proc.returncode != 0:
        reason = (proc.stderr or proc.stdout or "nvidia-smi failed").strip()
        return {"available": False, "reason": reason, "devices": []}
    return {"available": True, "reason": None, "devices": parse_device_rows(proc.stdout)}


def read_journal(path: str | None, since: str | None, boot: bool) -> tuple[str, dict]:
    if path:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            return "", {"kind": "file", "path": path, "error": f"{type(error).__name__}: {error}"}
        return text, {"kind": "file", "path": path, "error": None}

    exe = shutil.which("journalctl")
    if not exe:
        return "", {"kind": "journalctl", "error": "journalctl was not found"}
    command = [exe, "-k", "--no-pager", "-o", "short-iso"]
    if boot:
        command += ["-b", "0"]
    elif since:
        command += ["--since", since]
    else:
        command += ["--since", "-24 hours"]
    try:
        proc = subprocess.run(command, text=True, capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return "", {"kind": "journalctl", "error": f"{type(error).__name__}: {error}"}
    if proc.returncode != 0:
        reason = (proc.stderr or proc.stdout or "journalctl failed").strip()
        return "", {"kind": "journalctl", "error": reason}
    return proc.stdout, {"kind": "journalctl", "error": None}


def build_report(journal: str, device: dict | None = None) -> dict:
    events = parse_xid_lines(journal)
    return {
        "schema_version": 1,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "scope": "nvidia_kernel_faults_plus_current_device_state",
        "events": events,
        "summary": summarize_events(events),
        "devices": device if device is not None else device_snapshot(),
        "interpretation": (
            "An NVRM Xid is a driver/hardware event. Its presence does not by itself prove "
            "the GPU is damaged; correlate it with the request time, device and process, and "
            "retain the actual kernel line."
        ),
    }


def print_human(report: dict, source: dict) -> None:
    summary = report["summary"]
    print(f"kernel source: {source['kind']}" + (f" ({source.get('path')})" if source.get("path") else ""))
    if source.get("error"):
        print(f"kernel log: unavailable: {source['error']}")
    print(
        f"NVRM Xid events: {summary['count']} in {summary['incident_count']} time-clustered device "
        f"incident(s), {summary['incident_cluster_seconds']} s window "
        f"(fault candidates {summary['fault_candidate_count']}, reset_required={summary['reset_required']})"
    )
    for event in report["events"]:
        fields = [event["timestamp"] or "time unknown", event["pci"], f"Xid {event['code']}"]
        if event["pid"] is not None:
            fields.append(f"pid={event['pid']}")
        if event["process"]:
            fields.append(f"process={event['process']}")
        print("  " + "  ".join(fields) + (f": {event['detail']}" if event["detail"] else ""))

    devices = report["devices"]
    if not devices["available"]:
        print(f"nvidia-smi: unavailable: {devices['reason']}")
        return
    print("current devices:")
    for row in devices["devices"]:
        print(
            "  "
            f"GPU {row.get('index')} {row.get('pci_bus_id')} {row.get('name')}: "
            f"{row.get('temperature_gpu')} C, {row.get('power_draw')}/{row.get('power_limit')} W, "
            f"memory {row.get('memory_used')}/{row.get('memory_total')} MiB, "
            f"clocks sm/mem {row.get('clocks_sm')}/{row.get('clocks_mem')} MHz, "
            f"util {row.get('utilization_gpu')}%, throttle {row.get('clocks_throttle_reasons_active')}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--since", help="journalctl --since value (default when no scope: -24 hours)")
    scope.add_argument("--boot", action="store_true", help="read the whole current boot")
    parser.add_argument("--cluster-seconds", type=int, default=5,
                        help="merge same-device events at most this many seconds apart (default: 5)")
    parser.add_argument("--journal", help="read a saved kernel log instead of journalctl")
    parser.add_argument("--output", help="write the JSON report here")
    parser.add_argument("--json", action="store_true", help="print JSON instead of the human summary")
    parser.add_argument("--require-clean", action="store_true", help="exit 2 when any NVRM Xid is present")
    options = parser.parse_args(argv)

    journal, source = read_journal(options.journal, options.since, options.boot)
    report = build_report(journal)
    report["summary"] = summarize_events(report["events"], max(0, options.cluster_seconds))
    report["kernel_source"] = source
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if options.output:
        Path(options.output).write_text(rendered, encoding="utf-8")
    if options.json:
        sys.stdout.write(rendered)
    else:
        print_human(report, source)
        if options.output:
            print(f"JSON: {options.output}")

    if options.require_clean and report["summary"]["count"]:
        return 2
    return 1 if source.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
