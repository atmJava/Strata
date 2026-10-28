#!/usr/bin/env python3
"""Measure real GPU work during controlled inference, without guessing from NVML alone.

The probe starts a system-wide Nsight Systems GPU Metrics session, runs a short decode
and/or a cold long prefill against the live Strata server, and records the GPU counters
around each request. It does not restart the service. Nsight Systems must run as root;
the target server keeps running as its normal service user.

The result separates these facts:

  - SMs Active: whether SMs have work, not the fraction of arithmetic capacity used;
  - SM Issue: issue-slot activity;
  - Tensor Active and Warps in Flight: whether tensor/compute pipelines are occupied;
  - DRAM, NVLink and PCIe throughput: whether a data path is near saturation.

It also records NVML activity/power/clocks and the server's request timings as
cross-checks. A request is excluded unless its finish reason, length and needle pass.

Usage:
  sudo -S /opt/ai/bin/probe-gpu-utilization.py [options]
  sudo /opt/ai/bin/probe-gpu-utilization.py --check-only

Exit codes: 0 verified, 1 measurement rejected, 2 preflight or collection failed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import secrets
import shutil
import signal
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_BASE_URL = "http://127.0.0.1:8080"
DEFAULT_CONFIG = "/opt/ai/src/strata-027-picks/strata-live.json"
DEFAULT_NSYS = "/usr/local/bin/nsys"
DEFAULT_LOG_DIR = "/opt/ai/logs"
DEFAULT_NVIDIA_SMI = "/usr/bin/nvidia-smi"
ENGINE_PROCESS_PATTERN = "[s]trata --serve"
SERVER_PROCESS_PATTERN = "serve/[s]erver.py --engine"

GPU_QUERY = (
    "index,name,uuid,utilization.gpu,utilization.memory,power.draw,power.limit,"
    "clocks.sm,clocks.mem,temperature.gpu,memory.used,memory.total"
)
REQUIRED_METRICS = (
    "SMs Active [Throughput %]",
    "SM Issue [Throughput %]",
    "Tensor Active / FP16 Active [Throughput %]",
    "Compute Warps in Flight [Throughput %]",
    "Unallocated Warps in Active SMs [Throughput %]",
    "DRAM Read Bandwidth [Throughput %]",
    "DRAM Write Bandwidth [Throughput %]",
)
METRIC_KEYS = {
    "GR Active [Throughput %]": "gr_active_pct",
    "SMs Active [Throughput %]": "sm_active_pct",
    "SM Issue [Throughput %]": "sm_issue_pct",
    "Tensor Active / FP16 Active [Throughput %]": "tensor_fp16_active_pct",
    "Compute Warps in Flight [Throughput %]": "compute_warps_pct",
    "Unallocated Warps in Active SMs [Throughput %]": "unallocated_warps_pct",
    "DRAM Read Bandwidth [Throughput %]": "dram_read_pct",
    "DRAM Write Bandwidth [Throughput %]": "dram_write_pct",
    "NVLink RX Requests User Data [Throughput %]": "nvlink_rx_user_pct",
    "NVLink TX Requests User Data [Throughput %]": "nvlink_tx_user_pct",
    "PCIe RX Throughput [Throughput %]": "pcie_rx_pct",
    "PCIe TX Throughput [Throughput %]": "pcie_tx_pct",
}
METRIC_INTERPRETATION = (
    "Nsight Systems GPU Metrics are hardware counters. SMs Active is the share of SMs "
    "with work and is not a per-SM arithmetic or tensor utilization; SM Issue and "
    "Tensor Active are needed to judge execution-pipeline use. DRAM/NVLink/PCIe report "
    "throughput against their own paths, not SM occupancy. The SQLite export has one "
    "metric channel per selected GPU but no stable channel-to-UUID mapping, so channels "
    "are labelled channel_0/channel_1 rather than claiming a physical card without proof."
)


class ProbeError(RuntimeError):
    """A check or measurement failed before a trustworthy result could be produced."""


def utc_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def utc_iso(epoch_ns: int | None = None) -> str:
    if epoch_ns is None:
        epoch_ns = time.time_ns()
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch_ns / 1e9))


def number(value: str) -> float | int | None:
    text = value.strip()
    if not text or text in {"N/A", "[N/A]", "Not Supported"}:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    return int(parsed) if parsed.is_integer() else parsed


def parse_nvidia_smi_rows(text: str) -> list[dict[str, Any]]:
    """Parse the fixed CSV query and keep the physical index reported by NVML."""
    rows: list[dict[str, Any]] = []
    fields = (
        "index",
        "name",
        "uuid",
        "utilization_gpu_pct",
        "utilization_memory_pct",
        "power_w",
        "power_limit_w",
        "clock_sm_mhz",
        "clock_memory_mhz",
        "temperature_c",
        "memory_used_mib",
        "memory_total_mib",
    )
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != len(fields):
            raise ProbeError(f"nvidia-smi returned {len(parts)} fields, expected {len(fields)}: {line[:160]}")
        row: dict[str, Any] = {}
        for key, value in zip(fields, parts):
            row[key] = value if key in {"name", "uuid"} else number(value)
        if not isinstance(row["index"], int):
            raise ProbeError(f"nvidia-smi returned an invalid GPU index: {parts[0]!r}")
        rows.append(row)
    return rows


def run_command(
    argv: list[str],
    *,
    timeout: float = 30,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        done = subprocess.run(
            argv,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProbeError(f"cannot run {argv[0]}: {exc}") from exc
    if check and done.returncode != 0:
        detail = (done.stderr or done.stdout).strip()[:1000]
        raise ProbeError(f"{' '.join(argv)} failed with rc={done.returncode}: {detail}")
    return done


def live_is_idle(metrics: dict[str, Any]) -> bool:
    """Refuse any state whose work cannot be attributed to this probe."""
    live = metrics.get("live")
    if not isinstance(live, dict):
        return False
    for key in ("running", "queued", "waiting", "admitting", "outside_slots"):
        value = live.get(key, 0)
        if not isinstance(value, int) or value != 0:
            return False
    return True


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile of empty values")
    index = round(fraction * (len(ordered) - 1))
    return ordered[min(len(ordered) - 1, max(0, index))]


def stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "min": round(min(values), 3),
        "p10": round(percentile(values, 0.10), 3),
        "median": round(statistics.median(values), 3),
        "p90": round(percentile(values, 0.90), 3),
        "p95": round(percentile(values, 0.95), 3),
        "max": round(max(values), 3),
    }


def summarize_nsys_metrics(
    sqlite_path: pathlib.Path,
    stage_start_epoch_ns: int,
    stage_end_epoch_ns: int,
    *,
    expected_channels: int | None = None,
) -> dict[str, Any]:
    """Summarize one exact wall-clock window from an exported Nsight SQLite file."""
    if stage_end_epoch_ns <= stage_start_epoch_ns:
        raise ProbeError("GPU metric window end must be after start")
    if not sqlite_path.is_file():
        raise ProbeError(f"Nsight SQLite export does not exist: {sqlite_path}")
    uri = f"file:{sqlite_path.as_posix()}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise ProbeError(f"cannot open Nsight SQLite export: {exc}") from exc
    try:
        try:
            row = con.execute(
                "select utcEpochNs from TARGET_INFO_SESSION_START_TIME limit 1"
            ).fetchone()
            if not row or not isinstance(row[0], int):
                raise ProbeError("Nsight SQLite export has no session start time")
            session_start_ns = row[0]
            start_ns = stage_start_epoch_ns - session_start_ns
            end_ns = stage_end_epoch_ns - session_start_ns
            catalog = {
                int(metric_id): str(metric_name)
                for metric_id, metric_name in con.execute(
                    "select distinct metricId, metricName from TARGET_INFO_GPU_METRICS"
                )
            }
            rows = con.execute(
                """
                select typeId, metricId, value
                from GPU_METRICS
                where timestamp >= ? and timestamp <= ?
                order by typeId, timestamp, metricId
                """,
                (start_ns, end_ns),
            ).fetchall()
        except sqlite3.Error as exc:
            raise ProbeError(f"cannot read Nsight SQLite export: {exc}") from exc
    finally:
        con.close()
    if not rows:
        raise ProbeError("no GPU metric samples in the requested stage window")

    by_channel: dict[int, dict[int, list[float]]] = {}
    for type_id, metric_id, value in rows:
        if int(metric_id) not in catalog or not isinstance(value, (int, float)):
            continue
        by_channel.setdefault(int(type_id), {}).setdefault(int(metric_id), []).append(float(value))
    if expected_channels is not None and len(by_channel) != expected_channels:
        raise ProbeError(
            f"expected {expected_channels} GPU metric channels, found {len(by_channel)}"
        )

    channels: dict[str, Any] = {}
    all_values: dict[str, list[float]] = {}
    for channel_index, type_id in enumerate(sorted(by_channel)):
        metrics: dict[str, Any] = {}
        for metric_id, values in by_channel[type_id].items():
            metric_name = catalog[metric_id]
            metric_key = METRIC_KEYS.get(metric_name, metric_name)
            metrics[metric_key] = stats(values)
            all_values.setdefault(metric_key, []).extend(values)
        channels[f"channel_{channel_index}"] = {
            "nsys_type_id": type_id,
            "metrics": metrics,
        }

    present_names = set(catalog.values())
    return {
        "session_start_epoch_ns": session_start_ns,
        "windows_ns": {"start": start_ns, "end": end_ns},
        "channels": channels,
        "aggregate": {key: stats(values) for key, values in all_values.items()},
        "missing_required_metrics": sorted(set(REQUIRED_METRICS) - present_names),
        "interpretation": METRIC_INTERPRETATION,
    }


def validate_request(kind: str, row: dict[str, Any], *, target_tokens: int) -> list[str]:
    """Reject failed, truncated, or cache-dirty requests before they enter a comparison."""
    problems: list[str] = []
    if row.get("http_status") != 200:
        problems.append(f"HTTP status is {row.get('http_status')}, not 200")
    if row.get("finish_reason") != "stop":
        problems.append(f"finish_reason is {row.get('finish_reason')!r}, not 'stop'")
    if not row.get("needle_found"):
        problems.append("needle was not found in the response")
    output_tokens = row.get("output_tokens")
    if not isinstance(output_tokens, int) or output_tokens <= 0:
        problems.append("output token count is missing or zero")
    if kind == "short":
        if not isinstance(output_tokens, int) or output_tokens < 32:
            problems.append(f"short decode used {output_tokens!r} output tokens, need at least 32")
    elif kind == "long":
        prompt_tokens = row.get("prompt_tokens")
        if not isinstance(prompt_tokens, int) or prompt_tokens < int(target_tokens * 0.8):
            problems.append(
                f"long prompt has {prompt_tokens!r} tokens, below 80% of target {target_tokens}"
            )
        reused = row.get("reused_tokens")
        if not isinstance(reused, int):
            problems.append("reused prompt-token count is missing")
        elif reused > max(16, int(target_tokens * 0.01)):
            problems.append(f"long prompt reused {reused} tokens, above the 1% cold-prompt limit")
    else:
        problems.append(f"unknown request kind {kind!r}")
    return problems


def select_request_metrics(
    metrics: dict[str, Any],
    started_epoch_s: float,
    prompt_tokens: int | None,
    *,
    tolerance_s: float = 2.0,
) -> dict[str, Any] | None:
    """Select the completed server request nearest this probe's request start."""
    candidates: list[dict[str, Any]] = []
    for row in metrics.get("requests") or []:
        if not isinstance(row, dict):
            continue
        timestamp = row.get("time")
        if not isinstance(timestamp, (int, float)):
            continue
        if abs(float(timestamp) - started_epoch_s) > tolerance_s:
            continue
        if prompt_tokens is not None and row.get("prompt_tokens") != prompt_tokens:
            continue
        candidates.append(row)
    return min(candidates, key=lambda item: abs(float(item["time"]) - started_epoch_s), default=None)


def api_request(
    base_url: str,
    path: str,
    api_key: str,
    *,
    body: dict[str, Any] | None = None,
    timeout: float = 30,
) -> tuple[int, dict[str, Any]]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(base_url.rstrip("/") + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read()
    except (OSError, urllib.error.URLError) as exc:
        raise ProbeError(f"{path} request failed: {exc}") from exc
    try:
        parsed = json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise ProbeError(f"{path} returned non-JSON content") from exc
    return status, parsed


def fetch_health_and_metrics(
    base_url: str,
    api_key: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    health_status, health = api_request(base_url, "/health", api_key)
    metrics_status, metrics = api_request(base_url, "/metrics", api_key)
    if health_status != 200 or metrics_status != 200:
        raise ProbeError(f"health/metrics returned HTTP {health_status}/{metrics_status}")
    if health.get("loaded") is not True:
        raise ProbeError(f"service is not loaded: {health!r}")
    if not live_is_idle(metrics):
        raise ProbeError(f"service is not idle before the probe: {json.dumps(metrics.get('live'))}")
    return health, metrics


def wait_idle(base_url: str, api_key: str, timeout_s: float = 15) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        _, last = api_request(base_url, "/metrics", api_key)
        if live_is_idle(last):
            return last
        time.sleep(0.5)
    raise ProbeError(f"service did not return to idle within {timeout_s:.0f}s: {last and last.get('live')}")


def read_json_file(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeError(f"cannot read JSON config {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProbeError(f"JSON config {path} is not an object")
    return value


def digest(path: str) -> dict[str, Any]:
    md5 = hashlib.md5()
    sha256 = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            md5.update(chunk)
            sha256.update(chunk)
    st = os.stat(path)
    return {
        "path": path,
        "md5": md5.hexdigest(),
        "sha256": sha256.hexdigest(),
        "size": st.st_size,
        "mtime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime)),
    }


def running_stamp(config_path: str) -> dict[str, Any]:
    engine_pids = run_command(
        ["pgrep", "-f", ENGINE_PROCESS_PATTERN], check=False
    ).stdout.split()
    server_pids = run_command(
        ["pgrep", "-f", SERVER_PROCESS_PATTERN], check=False
    ).stdout.split()
    if len(engine_pids) != 1 or len(server_pids) != 1:
        raise ProbeError(
            f"expected one engine and one server process, found {engine_pids!r} and {server_pids!r}"
        )
    engine_pid = engine_pids[0]
    server_pid = server_pids[0]
    try:
        engine_argv = pathlib.Path(f"/proc/{engine_pid}/cmdline").read_bytes().split(b"\0")
        server_argv = pathlib.Path(f"/proc/{server_pid}/cmdline").read_bytes().split(b"\0")
        env = pathlib.Path(f"/proc/{engine_pid}/environ").read_bytes().split(b"\0")
    except OSError as exc:
        raise ProbeError(f"cannot read running process state: {exc}") from exc
    engine_path = engine_argv[0].decode("utf-8", "replace")
    server_path = next(
        (
            part.decode("utf-8", "replace")
            for part in server_argv
            if part.decode("utf-8", "replace").endswith("/serve/server.py")
        ),
        "",
    )
    return {
        "engine": {
            "pid": int(engine_pid),
            "binary": engine_path,
            "hash": digest(engine_path),
            "env": sorted(
                item.decode("utf-8", "replace")
                for item in env
                if item.startswith(b"STRATA_")
            ),
        },
        "server": {
            "pid": int(server_pid),
            "script": server_path,
            "hash": digest(server_path),
        },
        "config": digest(config_path),
    }


def query_gpus(nvidia_smi: str) -> list[dict[str, Any]]:
    done = run_command(
        [nvidia_smi, f"--query-gpu={GPU_QUERY}", "--format=csv,noheader,nounits"],
        timeout=10,
    )
    rows = parse_nvidia_smi_rows(done.stdout)
    if not rows:
        raise ProbeError("nvidia-smi returned no GPUs")
    return rows


class NvmlSampler(threading.Thread):
    """One-Hz NVML cross-check; deliberately not treated as the primary measurement."""

    def __init__(self, nvidia_smi: str) -> None:
        super().__init__(name="nvml-sampler", daemon=True)
        self.nvidia_smi = nvidia_smi
        self.stop_event = threading.Event()
        self.samples: list[dict[str, Any]] = []
        self.error: str | None = None

    def run(self) -> None:
        while not self.stop_event.is_set():
            captured = time.time_ns()
            try:
                rows = query_gpus(self.nvidia_smi)
                self.samples.append({"epoch_ns": captured, "gpus": rows})
            except ProbeError as exc:
                self.error = str(exc)
                return
            self.stop_event.wait(1.0)

    def stop(self) -> None:
        self.stop_event.set()
        self.join(timeout=5)
        if self.is_alive():
            self.error = "NVML sampler did not stop within 5s"


class NsysSession:
    """Start/stop a system-wide GPU Metrics session and always remove its active session."""

    def __init__(self, nsys: str, session: str, report: pathlib.Path) -> None:
        self.nsys = nsys
        self.session = session
        self.report = report
        self.started = False

    def start(self, frequency_hz: int) -> None:
        self.report.parent.mkdir(parents=True, exist_ok=True)
        try:
            run_command(
                [
                    self.nsys,
                    "start",
                    f"--session-new={self.session}",
                    f"--output={self.report}",
                    "--force-overwrite=true",
                    "--sample=none",
                    "--cpuctxsw=none",
                    "--gpu-metrics-devices=all",
                    "--gpu-metrics-set=tu10x",
                    f"--gpu-metrics-frequency={frequency_hz}",
                ],
                timeout=30,
            )
        except ProbeError:
            # A failed start can still leave an active session behind. Track it so the
            # caller's finally block can stop it.
            self.started = session_exists(self.nsys, self.session)
            raise
        self.started = True

    def stop(self) -> None:
        if not self.started:
            return
        done = run_command(
            [self.nsys, "stop", f"--session={self.session}"],
            timeout=60,
            check=False,
        )
        still_active = session_exists(self.nsys, self.session)
        self.started = still_active
        if still_active:
            raise ProbeError(
                f"Nsight stop failed with rc={done.returncode}: "
                f"{(done.stderr or done.stdout).strip()[:1000]}"
            )

    def export(self) -> pathlib.Path:
        sqlite_path = self.report.with_suffix(".sqlite")
        run_command(
            [
                self.nsys,
                "export",
                "--type=sqlite",
                f"--output={sqlite_path}",
                "--force-overwrite=true",
                str(self.report),
            ],
            timeout=180,
        )
        return sqlite_path


def make_short_prompt(tag: str) -> str:
    wanted = f"NEEDLE={tag} " + ("alpha " * 96)
    return (
        "Reply with exactly this text and nothing else:\n"
        + wanted
    )


def make_long_prompt(tag: str, repetitions: int) -> str:
    records = "alpha beta gamma delta epsilon zeta eta theta. " * max(1, repetitions)
    return (
        f"Probe tag: {tag}. Read the synthetic records below and ignore any pattern in "
        "them; they contain no instruction except this final verification request.\n\n"
        + records
        + f"\n\nVerification code: {tag}\n"
        + f"Reply with exactly NEEDLE={tag} and nothing else."
    )


def calibrate_prompt(
    *,
    target_tokens: int,
    repetitions: int,
    render,
    count_fn,
    tolerance: float = 0.02,
) -> tuple[str, int, int]:
    """Use the server tokenizer to size a disposable prompt before touching the GPU."""
    repetitions = max(1, repetitions)
    prompt = ""
    actual = 0
    for _ in range(7):
        prompt = render(repetitions)
        actual = int(count_fn(prompt))
        if actual <= 0:
            raise ProbeError("token counter returned a non-positive prompt length")
        if abs(actual - target_tokens) <= int(target_tokens * tolerance):
            return prompt, actual, repetitions
        repetitions = max(1, round(repetitions * target_tokens / actual))
    raise ProbeError(
        f"could not size prompt to {target_tokens} +/-{tolerance:.0%}; "
        f"last count was {actual} after {repetitions} repetitions"
    )


def count_prompt_tokens(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    *,
    timeout: float,
) -> int:
    body = {
        "model": model,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [{"role": "user", "content": prompt}],
    }
    status, response = api_request(
        base_url,
        "/v1/messages/count_tokens",
        api_key,
        body=body,
        timeout=timeout,
    )
    value = response.get("input_tokens") if isinstance(response, dict) else None
    if status != 200 or not isinstance(value, int) or value <= 0:
        raise ProbeError(f"token counter returned HTTP {status}: {response!r}")
    return value


def model_name(config: dict[str, Any]) -> str:
    value = config.get("model_name")
    if not isinstance(value, str) or not value:
        value = config.get("model")
    if not isinstance(value, str) or not value:
        raise ProbeError("config has no non-empty model_name/model")
    return value


def post_completion(
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    *,
    max_tokens: int,
    timeout: float,
) -> dict[str, Any]:
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
        "messages": [{"role": "user", "content": prompt}],
    }
    started_epoch_ns = time.time_ns()
    status, response = api_request(base_url, "/v1/chat/completions", api_key, body=body, timeout=timeout)
    ended_epoch_ns = time.time_ns()
    choice = ((response.get("choices") or [{}])[0]) if isinstance(response, dict) else {}
    message = choice.get("message") or {}
    content = message.get("content") or choice.get("text") or ""
    usage = response.get("usage") or {} if isinstance(response, dict) else {}
    return {
        "http_status": status,
        "started_epoch_ns": started_epoch_ns,
        "ended_epoch_ns": ended_epoch_ns,
        "wall_s": (ended_epoch_ns - started_epoch_ns) / 1e9,
        "finish_reason": choice.get("finish_reason"),
        "content": content,
        "output_tokens": usage.get("completion_tokens"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "reused_tokens": (
            (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
            if isinstance(usage.get("prompt_tokens_details"), dict)
            else 0
        ),
        "response_error": (response.get("error") if isinstance(response, dict) else None),
    }


def execute_request(
    *,
    kind: str,
    tag: str,
    base_url: str,
    api_key: str,
    model: str,
    target_tokens: int,
    max_tokens: int,
    timeout: float,
    prompt_override: str | None = None,
) -> dict[str, Any]:
    prompt = prompt_override or (
        make_short_prompt(tag)
        if kind == "short"
        else make_long_prompt(tag, max(1, target_tokens // 10))
    )
    result = post_completion(
        base_url,
        api_key,
        model,
        prompt,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    result["kind"] = kind
    result["needle_found"] = f"NEEDLE={tag}" in str(result.get("content") or "")
    result["prompt_chars"] = len(prompt)
    del result["content"]
    return result


def attach_server_metrics(
    base_url: str,
    api_key: str,
    result: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    deadline = time.monotonic() + 15
    last_metrics: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        _, last_metrics = api_request(base_url, "/metrics", api_key)
        record = select_request_metrics(
            last_metrics,
            result["started_epoch_ns"] / 1e9,
            result.get("prompt_tokens"),
        )
        if record is not None:
            return result, record
        time.sleep(0.5)
    return result, None


def request_windows(result: dict[str, Any], record: dict[str, Any]) -> dict[str, dict[str, int]]:
    start_ns = int(float(record["time"]) * 1e9)
    end_ns = start_ns + int(float(record["duration_s"]) * 1e9)
    first_ns = start_ns + int(float(record.get("first_engine_token_s") or 0) * 1e9)
    windows = {
        "request": {"start_epoch_ns": start_ns, "end_epoch_ns": end_ns},
    }
    if result.get("kind") != "short" and first_ns > start_ns + 250_000_000:
        windows["prefill"] = {
            "start_epoch_ns": start_ns,
            "end_epoch_ns": min(first_ns, end_ns),
        }
    if end_ns > first_ns + 250_000_000:
        windows["decode"] = {
            "start_epoch_ns": max(first_ns, start_ns),
            "end_epoch_ns": end_ns,
        }
    return windows


def summarize_nvml_window(
    samples: list[dict[str, Any]],
    start_epoch_ns: int,
    end_epoch_ns: int,
) -> dict[str, Any]:
    selected = [
        sample for sample in samples
        if start_epoch_ns <= sample.get("epoch_ns", 0) <= end_epoch_ns
    ]
    if not selected:
        return {"samples": 0, "gpus": {}}
    indices = sorted({
        gpu["index"]
        for sample in selected
        for gpu in sample.get("gpus") or []
        if isinstance(gpu.get("index"), int)
    })
    gpus: dict[str, Any] = {}
    for index in indices:
        rows = [
            gpu for sample in selected for gpu in sample.get("gpus") or []
            if gpu.get("index") == index
        ]
        gpus[str(index)] = {
            key: stats([
                float(row[key]) for row in rows
                if isinstance(row.get(key), (int, float))
            ])
            for key in (
                "utilization_gpu_pct",
                "utilization_memory_pct",
                "power_w",
                "clock_sm_mhz",
                "clock_memory_mhz",
                "temperature_c",
            )
        }
    return {"samples": len(selected), "gpus": gpus}


def session_exists(nsys: str, session: str) -> bool:
    done = run_command([nsys, "sessions", "list"], timeout=20, check=False)
    return session in done.stdout


def write_json(path: pathlib.Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def brief_service_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    """Keep evidence small and avoid copying bounded request history into every report."""
    return {
        "engine": metrics.get("engine"),
        "live": metrics.get("live"),
        "hardware": metrics.get("hardware"),
        "hardware_static": metrics.get("hardware_static"),
        "conversation_cache": metrics.get("conversation_cache"),
        "totals": metrics.get("totals"),
    }


def fix_ownership(paths: list[pathlib.Path], directory: pathlib.Path) -> None:
    uid_text = os.environ.get("SUDO_UID")
    gid_text = os.environ.get("SUDO_GID")
    if not uid_text or not gid_text:
        return
    uid = int(uid_text)
    gid = int(gid_text)
    os.chown(directory, uid, gid)
    os.chmod(directory, 0o750)
    for path in paths:
        if path.exists():
            os.chown(path, uid, gid)
            os.chmod(path, 0o640)


def preflight(
    options: argparse.Namespace,
    config: dict[str, Any],
    api_key: str,
    out_dir: pathlib.Path,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: Any) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})
        if not passed:
            raise ProbeError(f"{name}: {detail}")

    add("root", os.geteuid() == 0, {"uid": os.geteuid()})
    for command in ("nvidia-smi", "nsys", "pgrep", "systemctl"):
        add(f"command:{command}", shutil.which(command) is not None, shutil.which(command))
    nsys_version = run_command([options.nsys, "--version"], timeout=15).stdout.strip()
    add("nsys", bool(nsys_version), nsys_version)
    add("nsys:session_clear", not session_exists(options.nsys, options.session), options.session)

    gpus = query_gpus(options.nvidia_smi)
    add("gpu:count", len(gpus) == options.expected_gpus, {
        "expected": options.expected_gpus,
        "found": len(gpus),
        "gpus": gpus,
    })
    add("gpu:indices", len({gpu["index"] for gpu in gpus}) == len(gpus), [
        gpu["index"] for gpu in gpus
    ])
    add("config:model", bool(model_name(config)), model_name(config))
    health, metrics = fetch_health_and_metrics(options.base_url, api_key)
    add("service:loaded", health.get("loaded") is True, health)
    add("service:idle", live_is_idle(metrics), metrics.get("live"))
    add("service:engine", bool((metrics.get("engine") or {}).get("version")), metrics.get("engine"))
    stamp = running_stamp(options.config)

    preflight_session = NsysSession(
        options.nsys,
        options.session + "-preflight",
        out_dir / "preflight.nsys-rep",
    )
    preflight_sqlite: pathlib.Path | None = None
    try:
        preflight_session.start(min(options.gpu_metrics_hz, 100))
        time.sleep(0.5)
        preflight_session.stop()
        preflight_sqlite = preflight_session.export()
        con = sqlite3.connect(f"file:{preflight_sqlite.as_posix()}?mode=ro", uri=True)
        try:
            catalog = {
                str(row[0])
                for row in con.execute("select distinct metricName from TARGET_INFO_GPU_METRICS")
            }
            channel_count = int(
                con.execute("select count(distinct typeId) from GPU_METRICS").fetchone()[0]
            )
        finally:
            con.close()
        missing = sorted(set(REQUIRED_METRICS) - catalog)
        add("nsys:required_metrics", not missing, {"missing": missing})
        add("nsys:channels", channel_count == options.expected_gpus, {
            "expected": options.expected_gpus,
            "found": channel_count,
        })
    finally:
        if preflight_session.started:
            preflight_session.stop()

    return {
        "checks": checks,
        "gpus": gpus,
        "health": health,
        "metrics": brief_service_metrics(metrics),
        "running": stamp,
        "nsys_version": nsys_version,
        "preflight_report": str(out_dir / "preflight.nsys-rep"),
        "preflight_sqlite": str(preflight_sqlite) if preflight_sqlite else None,
    }


def run_probe(options: argparse.Namespace) -> int:
    started_ns = time.time_ns()
    run_id = options.run_id or f"gpu-util-{utc_stamp()}"
    out_dir = pathlib.Path(options.out_dir or DEFAULT_LOG_DIR) / run_id
    if out_dir.exists() and not options.force:
        raise ProbeError(f"output already exists (use --force): {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / "probe.nsys-rep"
    sqlite_path = out_dir / "probe.sqlite"
    json_path = out_dir / "probe.json"
    preflight_paths = [
        out_dir / "preflight.nsys-rep",
        out_dir / "preflight.sqlite",
    ]
    options.session = f"strata-gpu-util-{os.getpid()}-{utc_stamp()}"

    config = read_json_file(options.config)
    api_key = options.api_key or os.environ.get("STRATA_PROBE_API_KEY") or str(config.get("api_key") or "")
    if not api_key:
        raise ProbeError("no API key; set --api-key, STRATA_PROBE_API_KEY, or config api_key")

    result: dict[str, Any] = {
        "schema": "strata-gpu-utilization-v1",
        "status": "failed",
        "run_id": run_id,
        "started_at": utc_iso(started_ns),
        "finished_at": None,
        "host": os.uname().nodename if hasattr(os, "uname") else os.environ.get("COMPUTERNAME"),
        "profile": options.profile,
        "parameters": {
            "base_url": options.base_url,
            "config": options.config,
            "long_prompt_target_tokens": options.long_tokens,
            "short_max_tokens": options.short_max_tokens,
            "gpu_metrics_hz": options.gpu_metrics_hz,
            "expected_gpus": options.expected_gpus,
        },
        "problems": [],
        "warnings": [],
        "stages": [],
        "evidence": {
            "nsys_report": str(report_path),
            "nsys_sqlite": str(sqlite_path),
            "json": str(json_path),
        },
        "limits": [
            "One controlled short decode and one cold long prefill; not sustained mixed traffic.",
            "The SQLite export does not expose a stable Nsight channel-to-GPU-UUID mapping.",
            "SMs Active alone is not arithmetic or tensor utilization; read it with SM Issue, "
            "Tensor Active, warps in flight and DRAM.",
        ],
    }
    session = NsysSession(options.nsys, options.session, report_path)
    sampler = NvmlSampler(options.nvidia_smi)
    sampler_started = False
    try:
        result["preflight"] = preflight(options, config, api_key, out_dir)
        result["status"] = "running"
        result["preflight"]["session_before"] = session_exists(options.nsys, options.session)
        model = model_name(config)
        long_prompt: str | None = None
        long_tag: str | None = None
        if options.profile in {"long", "mixed"}:
            long_tag = f"L{secrets.token_hex(6)}"
            long_prompt, counted, repetitions = calibrate_prompt(
                target_tokens=options.long_tokens,
                repetitions=max(1, options.long_tokens // 10),
                render=lambda count: make_long_prompt(long_tag, count),
                count_fn=lambda prompt: count_prompt_tokens(
                    options.base_url,
                    api_key,
                    model,
                    prompt,
                    timeout=min(options.request_timeout, 120),
                ),
            )
            result["parameters"]["long_prompt_counted_tokens"] = counted
            result["parameters"]["long_prompt_repetitions"] = repetitions

        sampler.start()
        sampler_started = True
        session.start(options.gpu_metrics_hz)
        baseline_start = time.time_ns()
        time.sleep(options.baseline_s)
        baseline_end = time.time_ns()

        warmup_tag = f"W{secrets.token_hex(5)}"
        warmup = execute_request(
            kind="short",
            tag=warmup_tag,
            base_url=options.base_url,
            api_key=api_key,
            model=model,
            target_tokens=0,
            max_tokens=16,
            timeout=options.request_timeout,
        )
        if warmup.get("http_status") != 200:
            raise ProbeError(f"warmup returned HTTP {warmup.get('http_status')}")
        wait_idle(options.base_url, api_key)
        result["stages"].append({
            "kind": "idle_baseline",
            "start_epoch_ns": baseline_start,
            "end_epoch_ns": baseline_end,
        })

        planned: list[tuple[str, int, int]] = []
        if options.profile in {"short", "mixed"}:
            planned.append(("short", 0, options.short_max_tokens))
        if options.profile in {"long", "mixed"}:
            planned.append(("long", options.long_tokens, 64))

        for kind, target_tokens, max_tokens in planned:
            wait_idle(options.base_url, api_key)
            idle_start = time.time_ns()
            time.sleep(options.between_s)
            idle_end = time.time_ns()
            result["stages"].append({
                "kind": f"idle_before_{kind}",
                "start_epoch_ns": idle_start,
                "end_epoch_ns": idle_end,
            })
            tag = long_tag if kind == "long" and long_tag else f"{kind[0].upper()}{secrets.token_hex(6)}"
            request = execute_request(
                kind=kind,
                tag=tag,
                base_url=options.base_url,
                api_key=api_key,
                model=model,
                target_tokens=target_tokens,
                max_tokens=max_tokens,
                timeout=options.request_timeout,
                prompt_override=long_prompt if kind == "long" else None,
            )
            request, record = attach_server_metrics(options.base_url, api_key, request)
            if record is not None:
                request["server_metrics_id"] = record.get("id")
                request["server_prompt_ms"] = record.get("prompt_ms")
                request["server_decode_ms"] = record.get("decode_ms")
                request["server_decode_tok_s"] = record.get("decode_tok_s")
                request["server_reused_tokens"] = record.get("reused")
                if isinstance(record.get("reused"), int):
                    request["reused_tokens"] = record["reused"]
                if not isinstance(request.get("output_tokens"), int):
                    request["output_tokens"] = record.get("output_tokens")
                if not isinstance(request.get("prompt_tokens"), int):
                    request["prompt_tokens"] = record.get("prompt_tokens")
            request["problems"] = validate_request(kind, request, target_tokens=target_tokens)
            if record is None:
                request["problems"].append("completed request was not found in /metrics")
            else:
                windows = request_windows(request, record)
                request["server_record"] = record
                request["windows"] = {
                    name: {
                        "start_utc": utc_iso(window["start_epoch_ns"]),
                        "end_utc": utc_iso(window["end_epoch_ns"]),
                    }
                    for name, window in windows.items()
                }
            result["stages"].append({"kind": f"request_{kind}", "request": request})
            result["problems"].extend(
                f"{kind}: {problem}" for problem in request["problems"]
            )
            wait_idle(options.base_url, api_key)

        post_idle_start = time.time_ns()
        time.sleep(options.baseline_s)
        post_idle_end = time.time_ns()
        result["stages"].append({
            "kind": "idle_after",
            "start_epoch_ns": post_idle_start,
            "end_epoch_ns": post_idle_end,
        })
        session.stop()
        sqlite_path = session.export()
        sampler.stop()
        sampler_started = False

        for stage in result["stages"]:
            if "start_epoch_ns" not in stage:
                continue
            try:
                stage["nsys"] = summarize_nsys_metrics(
                    sqlite_path,
                    stage["start_epoch_ns"],
                    stage["end_epoch_ns"],
                    expected_channels=options.expected_gpus,
                )
                stage["nvml"] = summarize_nvml_window(
                    sampler.samples,
                    stage["start_epoch_ns"],
                    stage["end_epoch_ns"],
                )
                missing = stage["nsys"].get("missing_required_metrics") or []
                if missing:
                    result["problems"].append(
                        f"{stage['kind']}: missing required GPU metrics {missing}"
                    )
            except ProbeError as exc:
                result["problems"].append(f"{stage['kind']}: {exc}")

        for stage in result["stages"]:
            request = stage.get("request")
            if not isinstance(request, dict) or "server_record" not in request:
                continue
            record = request["server_record"]
            windows = request_windows(request, record)
            for name, window in windows.items():
                try:
                    metrics = summarize_nsys_metrics(
                        sqlite_path,
                        window["start_epoch_ns"],
                        window["end_epoch_ns"],
                        expected_channels=options.expected_gpus,
                    )
                    metrics["nvml"] = summarize_nvml_window(
                        sampler.samples,
                        window["start_epoch_ns"],
                        window["end_epoch_ns"],
                    )
                    missing = metrics.get("missing_required_metrics") or []
                    if missing:
                        result["problems"].append(
                            f"{request['kind']}/{name}: missing required GPU metrics {missing}"
                        )
                    request.setdefault("metric_windows", {})[name] = metrics
                except ProbeError as exc:
                    result["warnings"].append(
                        f"{request['kind']}/{name}: {exc}"
                    )

        after_health, after_metrics = fetch_health_and_metrics(
            options.base_url, api_key
        )
        after_stamp = running_stamp(options.config)
        result["after"] = {"health": after_health, "running": after_stamp}
        for component in ("engine", "server"):
            before = result["preflight"]["running"][component]
            after = after_stamp[component]
            if before["pid"] != after["pid"]:
                result["problems"].append(
                    f"{component} PID changed {before['pid']} -> {after['pid']}"
                )
            if before["hash"]["sha256"] != after["hash"]["sha256"]:
                result["problems"].append(f"{component} file hash changed during the probe")
        if result["preflight"]["running"]["config"]["sha256"] != after_stamp["config"]["sha256"]:
            result["problems"].append("config hash changed during the probe")
        if not live_is_idle(after_metrics):
            result["problems"].append("service was not idle after the probe")
        if sampler.error:
            result["problems"].append(f"NVML sampler failed: {sampler.error}")

        result["nvml_samples"] = sampler.samples
        result["status"] = "verified" if not result["problems"] else "rejected"
        result["finished_at"] = utc_iso()
        result["evidence"]["nsys_report_sha256"] = digest(str(report_path))["sha256"]
        result["evidence"]["nsys_sqlite_sha256"] = digest(str(sqlite_path))["sha256"]
        write_json(json_path, result)
        fix_ownership([report_path, sqlite_path, json_path, *preflight_paths], out_dir)
        print_summary(result)
        return 0 if result["status"] == "verified" else 1
    except KeyboardInterrupt:
        result["problems"].append("interrupted by signal")
        result["status"] = "failed"
        result["finished_at"] = utc_iso()
        raise
    except ProbeError as exc:
        result["problems"].append(str(exc))
        result["status"] = "failed"
        result["finished_at"] = utc_iso()
        print(f"failed: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        result["problems"].append(f"{type(exc).__name__}: {exc}")
        result["status"] = "failed"
        result["finished_at"] = utc_iso()
        print(f"failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        if sampler_started:
            sampler.stop()
        if session.started:
            try:
                session.stop()
            except ProbeError as exc:
                result["problems"].append(str(exc))
        if result.get("status") in {"failed", "rejected"}:
            write_json(json_path, result)
            paths = [report_path, sqlite_path, json_path, *preflight_paths]
            fix_ownership([path for path in paths if path.exists()], out_dir)


def print_summary(result: dict[str, Any]) -> None:
    print(f"status: {result['status']}")
    print(f"json: {result['evidence']['json']}")
    if pathlib.Path(result["evidence"]["nsys_report"]).exists():
        print(f"nsys: {result['evidence']['nsys_report']}")
    for stage in result.get("stages") or []:
        request = stage.get("request")
        if not isinstance(request, dict):
            continue
        print(
            f"{request['kind']}: HTTP {request.get('http_status')}  "
            f"prompt={request.get('prompt_tokens')}  output={request.get('output_tokens')}  "
            f"wall={request.get('wall_s'):.2f}s"
        )
        for name, window in (request.get("metric_windows") or {}).items():
            aggregate = window.get("aggregate") or {}
            sm_active = (aggregate.get("sm_active_pct") or {}).get("median")
            sm_issue = (aggregate.get("sm_issue_pct") or {}).get("median")
            tensor = (aggregate.get("tensor_fp16_active_pct") or {}).get("median")
            dram = (aggregate.get("dram_read_pct") or {}).get("median")
            print(
                f"  {name:8s} SMs Active {sm_active}%  SM Issue {sm_issue}%  "
                f"Tensor {tensor}%  DRAM read {dram}%"
            )
    for problem in result.get("problems") or []:
        print(f"PROBLEM: {problem}", file=sys.stderr)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--api-key", default=None, help="never written to the result")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--nsys", default=DEFAULT_NSYS)
    parser.add_argument("--nvidia-smi", default=DEFAULT_NVIDIA_SMI)
    parser.add_argument("--out-dir", default=DEFAULT_LOG_DIR)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--profile", choices=("short", "long", "mixed"), default="mixed")
    parser.add_argument("--long-tokens", type=int, default=32_768)
    parser.add_argument("--short-max-tokens", type=int, default=256)
    parser.add_argument("--gpu-metrics-hz", type=int, default=100)
    parser.add_argument("--request-timeout", type=float, default=1800)
    parser.add_argument("--baseline-s", type=float, default=3)
    parser.add_argument("--between-s", type=float, default=2)
    parser.add_argument("--expected-gpus", type=int, default=2)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--force", action="store_true")
    options = parser.parse_args(argv)
    if options.gpu_metrics_hz < 10 or options.gpu_metrics_hz > 1000:
        parser.error("--gpu-metrics-hz must be between 10 and 1000")
    if options.long_tokens < 4096:
        parser.error("--long-tokens must be at least 4096")
    if options.short_max_tokens < 32:
        parser.error("--short-max-tokens must be at least 32")

    if options.check_only:
        out_dir = pathlib.Path(options.out_dir) / (options.run_id or f"gpu-util-check-{utc_stamp()}")
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            config = read_json_file(options.config)
            api_key = options.api_key or os.environ.get("STRATA_PROBE_API_KEY") or str(config.get("api_key") or "")
            if not api_key:
                raise ProbeError("no API key")
            options.session = f"strata-gpu-util-check-{os.getpid()}"
            checks = preflight(options, config, api_key, out_dir)
            print(json.dumps(checks, indent=2, ensure_ascii=False))
            return 0
        except ProbeError as exc:
            print(f"preflight failed: {exc}", file=sys.stderr)
            return 2
        finally:
            preflight_paths = [
                out_dir / "preflight.nsys-rep",
                out_dir / "preflight.sqlite",
            ]
            fix_ownership([path for path in preflight_paths if path.exists()], out_dir)
    try:
        return run_probe(options)
    except ProbeError as exc:
        print(f"probe failed: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("probe interrupted; Nsight session cleanup was attempted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
