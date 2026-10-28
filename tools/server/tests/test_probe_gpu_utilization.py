"""Strict regression tests for the Nsight GPU-utilization probe."""
from __future__ import annotations

import importlib.util
import sqlite3
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "probe-gpu-utilization.py"


def load_probe():
    spec = importlib.util.spec_from_file_location("probe_gpu_utilization", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_metrics_db(path: Path, start_ns: int, rows: list[tuple[int, int, int, int]]) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE TARGET_INFO_SESSION_START_TIME (
            utcEpochNs INTEGER,
            utcTime TEXT,
            localTime TEXT
        );
        CREATE TABLE TARGET_INFO_GPU_METRICS (
            typeId INTEGER NOT NULL,
            sourceId INTEGER NOT NULL,
            typeName TEXT NOT NULL,
            metricId INTEGER NOT NULL,
            metricName TEXT NOT NULL
        );
        CREATE TABLE GPU_METRICS (
            rawTimestamp INTEGER NOT NULL,
            timestamp INTEGER NOT NULL,
            typeId INTEGER NOT NULL,
            metricId INTEGER NOT NULL,
            value INTEGER NOT NULL
        );
        """
    )
    con.execute(
        "INSERT INTO TARGET_INFO_SESSION_START_TIME VALUES (?, ?, ?)",
        (start_ns, "2026-10-27T00:00:00", "2026-10-27T08:00:00"),
    )
    metric_names = {
        2: "GR Active [Throughput %]",
        7: "SMs Active [Throughput %]",
        8: "SM Issue [Throughput %]",
        9: "Tensor Active / FP16 Active [Throughput %]",
        16: "Compute Warps in Flight [Throughput %]",
        19: "Unallocated Warps in Active SMs [Throughput %]",
        22: "DRAM Read Bandwidth [Throughput %]",
        23: "DRAM Write Bandwidth [Throughput %]",
    }
    for channel in (101, 102):
        for metric_id, metric_name in metric_names.items():
            con.execute(
                "INSERT INTO TARGET_INFO_GPU_METRICS VALUES (?, ?, '', ?, ?)",
                (channel, channel, metric_id, metric_name),
            )
    con.executemany("INSERT INTO GPU_METRICS VALUES (?, ?, ?, ?, ?)", rows)
    con.commit()
    con.close()


def test_parse_nvidia_smi_keeps_physical_gpu_index():
    probe = load_probe()
    rows = probe.parse_nvidia_smi_rows(
        "1, NVIDIA GeForce RTX 2080 Ti, GPU-b, 98, 41, 249.2, 280.0, 1770, 6800, 79, 21000, 22528\n"
        "0, NVIDIA GeForce RTX 2080 Ti, GPU-a, 96, 40, 256.4, 280.0, 1770, 6800, 81, 21050, 22528\n"
    )

    assert [row["index"] for row in rows] == [1, 0]
    assert rows[0]["power_w"] == pytest.approx(249.2)
    assert rows[1]["utilization_gpu_pct"] == pytest.approx(96.0)


def test_live_is_idle_refuses_every_nonzero_work_state():
    probe = load_probe()
    idle = {"running": 0, "queued": 0, "waiting": 0, "admitting": 0, "outside_slots": 0}
    assert probe.live_is_idle({"live": idle}) is True

    for key in idle:
        busy = dict(idle)
        busy[key] = 1
        assert probe.live_is_idle({"live": busy}) is False


def test_running_stamp_accepts_an_arbitrary_engine_build_directory(monkeypatch):
    probe = load_probe()
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        stdout = "101\n" if command[2] == probe.ENGINE_PROCESS_PATTERN else "202\n"
        return subprocess.CompletedProcess(command, 0, stdout, "")

    def fake_read_bytes(path):
        values = {
            "/proc/101/cmdline": b"/opt/ai/src/new-tree/build75-027/strata\0--serve\0",
            "/proc/101/environ": b"STRATA_HC_Q8=1\0",
            "/proc/202/cmdline": (
                b"python\0/opt/ai/src/new-tree/serve/server.py\0--config\0"
                b"/opt/ai/src/new-tree/strata-live.json\0"
            ),
        }
        return values[str(path).replace("\\", "/")]

    monkeypatch.setattr(probe, "run_command", fake_run)
    monkeypatch.setattr(probe.pathlib.Path, "read_bytes", fake_read_bytes)
    monkeypatch.setattr(probe, "digest", lambda path: {"path": path})

    stamp = probe.running_stamp("/opt/ai/src/new-tree/strata-live.json")

    assert calls[0][2] == "[s]trata --serve"
    assert stamp["engine"]["binary"] == "/opt/ai/src/new-tree/build75-027/strata"
    assert stamp["server"]["script"] == "/opt/ai/src/new-tree/serve/server.py"
    assert stamp["config"]["path"] == "/opt/ai/src/new-tree/strata-live.json"


def test_metric_summary_uses_only_stage_window(tmp_path: Path):
    probe = load_probe()
    start_ns = 1_000_000_000
    path = tmp_path / "trace.sqlite"
    rows = []
    for channel in (101, 102):
        for offset, value in ((-10, 99), (5, 10), (15, 20), (25, 30), (35, 99)):
            rows.append((offset, offset, channel, 7, value))
            rows.append((offset, offset, channel, 8, value // 2))
    make_metrics_db(path, start_ns, rows)

    report = probe.summarize_nsys_metrics(
        path,
        stage_start_epoch_ns=start_ns + 10,
        stage_end_epoch_ns=start_ns + 30,
    )

    assert report["windows_ns"] == {"start": 10, "end": 30}
    assert sorted(report["channels"]) == ["channel_0", "channel_1"]
    assert report["channels"]["channel_0"]["metrics"]["sm_active_pct"]["n"] == 2
    assert report["channels"]["channel_0"]["metrics"]["sm_active_pct"]["median"] == 25.0
    assert report["channels"]["channel_0"]["metrics"]["sm_issue_pct"]["max"] == 15.0


def test_metric_summary_rejects_empty_window(tmp_path: Path):
    probe = load_probe()
    start_ns = 1_000_000_000
    path = tmp_path / "trace.sqlite"
    make_metrics_db(path, start_ns, [(5, 5, 101, 7, 10)])

    with pytest.raises(probe.ProbeError, match="no GPU metric samples"):
        probe.summarize_nsys_metrics(path, start_ns + 20, start_ns + 30)


def test_metric_summary_requires_both_channels(tmp_path: Path):
    probe = load_probe()
    start_ns = 1_000_000_000
    path = tmp_path / "trace.sqlite"
    make_metrics_db(path, start_ns, [(5, 5, 101, 7, 10), (5, 5, 101, 8, 5)])

    with pytest.raises(probe.ProbeError, match="expected 2 GPU metric channels, found 1"):
        probe.summarize_nsys_metrics(path, start_ns, start_ns + 10, expected_channels=2)


def test_validate_short_request_rejects_truncation_and_missing_needle():
    probe = load_probe()
    good = {
        "http_status": 200,
        "finish_reason": "stop",
        "output_tokens": 80,
        "needle_found": True,
    }
    assert probe.validate_request("short", good, target_tokens=0) == []

    bad = dict(good, finish_reason="length", output_tokens=16, needle_found=False)
    problems = probe.validate_request("short", bad, target_tokens=0)
    assert any("finish_reason" in item for item in problems)
    assert any("needle" in item for item in problems)
    assert any("output tokens" in item for item in problems)


def test_validate_long_request_rejects_short_prompt_and_cache_reuse():
    probe = load_probe()
    row = {
        "http_status": 200,
        "finish_reason": "stop",
        "prompt_tokens": 20_000,
        "reused_tokens": 8_000,
        "needle_found": True,
    }

    problems = probe.validate_request("long", row, target_tokens=32_000)

    assert any("target" in item for item in problems)
    assert any("reused" in item for item in problems)


def test_select_request_metrics_uses_closest_matching_request():
    probe = load_probe()
    metrics = {
        "requests": [
            {"id": "old", "time": 100.0, "prompt_tokens": 12, "duration_s": 1.0},
            {"id": "match", "time": 110.0, "prompt_tokens": 321, "duration_s": 2.5},
        ]
    }

    row = probe.select_request_metrics(metrics, started_epoch_s=110.1, prompt_tokens=321)

    assert row is not None
    assert row["id"] == "match"
    assert probe.select_request_metrics(metrics, 120.0, 321) is None


def test_request_windows_omit_too_short_prefill_window():
    probe = load_probe()
    record = {
        "time": 1_000.0,
        "duration_s": 2.0,
        "first_engine_token_s": 0.2,
    }

    windows = probe.request_windows({"kind": "long"}, record)

    assert "prefill" not in windows
    assert "decode" in windows

    windows = probe.request_windows(
        {"kind": "long"},
        {"time": 1_000.0, "duration_s": 2.0, "first_engine_token_s": 0.8},
    )
    assert "prefill" in windows


def test_metric_interpretation_never_labels_sm_activity_as_compute_utilization():
    probe = load_probe()

    text = probe.METRIC_INTERPRETATION

    assert "SMs Active" in text
    assert "not a per-SM arithmetic or tensor utilization" in text
    assert "SM Issue" in text
    assert "Tensor Active" in text


def test_calibrate_prompt_hits_target_using_token_counter():
    probe = load_probe()

    def count(prompt: str) -> int:
        return prompt.count("unit") + 20

    prompt, actual, repetitions = probe.calibrate_prompt(
        target_tokens=10_020,
        repetitions=997,
        render=lambda count: ("unit " * count) + "needle",
        count_fn=count,
        tolerance=0.0001,
    )

    assert repetitions == 10_000
    assert actual == 10_020
    assert prompt.endswith("needle")


def test_short_prompt_requests_long_verifiable_output():
    probe = load_probe()

    prompt = probe.make_short_prompt("ABC123")

    assert "NEEDLE=ABC123" in prompt
    assert prompt.count("alpha") == 96


def test_nsys_stop_keeps_session_tracked_while_it_is_still_active(monkeypatch):
    probe = load_probe()
    session = probe.NsysSession("nsys", "probe-session", Path("probe.nsys-rep"))
    session.started = True
    monkeypatch.setattr(
        probe,
        "run_command",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "stop failed"),
    )
    monkeypatch.setattr(probe, "session_exists", lambda *args: True)

    with pytest.raises(probe.ProbeError, match="Nsight stop failed"):
        session.stop()

    assert session.started is True


def test_nsys_start_tracks_partial_session_for_cleanup(monkeypatch):
    probe = load_probe()
    session = probe.NsysSession("nsys", "probe-session", Path("probe.nsys-rep"))

    def fail_start(*args, **kwargs):
        raise probe.ProbeError("start failed")

    monkeypatch.setattr(probe, "run_command", fail_start)
    monkeypatch.setattr(probe, "session_exists", lambda *args: True)

    with pytest.raises(probe.ProbeError, match="start failed"):
        session.start(100)

    assert session.started is True
