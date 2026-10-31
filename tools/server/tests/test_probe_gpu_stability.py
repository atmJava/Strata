"""The GPU stability probe must retain real NVRM events without inventing a cause."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "probe-gpu-stability.py"
spec = importlib.util.spec_from_file_location("probe_gpu_stability", SCRIPT)
probe = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(probe)


KERNEL = """\
2026-10-31T17:27:28+00:00 host kernel: NVRM: GPU at PCI:0000:03:00: GPU-example
2026-10-31T17:27:28+00:00 host kernel: NVRM: Xid (PCI:0000:03:00): 13, Graphics SM Warp Exception on (GPC 1, TPC 0, SM 0): Illegal Instruction Encoding
2026-10-31T17:27:28+00:00 host kernel: NVRM: Xid (PCI:0000:03:00): 43, pid=172641, name=strata, channel 0x0000000b
2026-10-31T17:35:00+00:00 host kernel: r8169 0000:06:00.0 eth0: XID 541, IRQ 49
"""


def test_parser_keeps_only_nvrm_xid_and_extracts_process():
    events = probe.parse_xid_lines(KERNEL)

    assert [event["code"] for event in events] == [13, 43]
    assert events[0]["pci"] == "0000:03:00"
    assert events[0]["fault_candidate"] is True
    assert events[1]["pid"] == 172641
    assert events[1]["process"] == "strata"


def test_summary_marks_reset_events_without_claiming_damage():
    events = probe.parse_xid_lines(
        "Oct 31 17:27:28 host kernel: NVRM: Xid (PCI:0000:03:00): 154, "
        "GPU recovery action changed from 0x0 (None) to 0x1 (GPU Reset Required)\n"
    )

    summary = probe.summarize_events(events)
    assert summary["by_code"] == {"154": 1}
    assert summary["incident_count"] == 1
    assert summary["reset_required"] is True
    report = probe.build_report("", {"available": False, "reason": "test", "devices": []})
    assert "does not by itself prove" in report["interpretation"]


def test_summary_clusters_events_across_seconds_but_splits_distant_faults():
    events = probe.parse_xid_lines(
        "2026-10-31T17:27:28+00:00 host kernel: NVRM: Xid (PCI:0000:03:00): 13, first\n"
        "2026-10-31T17:27:31+00:00 host kernel: NVRM: Xid (PCI:0000:03:00): 43, second\n"
        "2026-10-31T17:27:40+00:00 host kernel: NVRM: Xid (PCI:0000:03:00): 13, third\n"
    )

    summary = probe.summarize_events(events, cluster_seconds=5)

    assert summary["count"] == 3
    assert summary["incident_count"] == 2
    assert [incident["event_count"] for incident in summary["incidents"]] == [2, 1]


def test_untimed_events_are_not_merged():
    events = probe.parse_xid_lines(
        "kernel: NVRM: Xid (PCI:0000:03:00): 13, first\n"
        "kernel: NVRM: Xid (PCI:0000:03:00): 43, second\n"
    )

    summary = probe.summarize_events(events)

    assert summary["incident_count"] == 2
    assert summary["untimed_event_count"] == 2


def test_boot_scope_does_not_silently_apply_the_24_hour_default(monkeypatch):
    seen = {}

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        seen["command"] = command
        return Result()

    monkeypatch.setattr(probe.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(probe.subprocess, "run", fake_run)

    _, source = probe.read_journal(None, None, True)

    assert source["error"] is None
    assert "-b" in seen["command"]
    assert "--since" not in seen["command"]


def test_device_csv_parser_keeps_unavailable_values():
    rows = probe.parse_device_rows(
        "0, 00000000:03:00.0, NVIDIA GeForce RTX 2080 Ti, 595.71.05, 21202, 22528, 49, "
        "52.75, 280.00, 300, 405, 0, Disabled, 0x0000000000000001\n"
    )

    assert rows[0]["index"] == 0
    assert rows[0]["pci_bus_id"] == "00000000:03:00.0"
    assert rows[0]["power_draw"] == 52.75
    assert rows[0]["memory_used"] == 21202
    assert rows[0]["clocks_throttle_reasons_active"] == "0x0000000000000001"


def test_saved_journal_cli_is_machine_readable(tmp_path, capsys):
    journal = tmp_path / "kernel.log"
    journal.write_text(KERNEL, encoding="utf-8")
    output = tmp_path / "report.json"

    code = probe.main(["--journal", str(journal), "--json", "--output", str(output)])

    assert code == 0
    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert printed["summary"]["count"] == 2
    assert saved["events"][0]["code"] == 13
    assert saved["kernel_source"]["kind"] == "file"
