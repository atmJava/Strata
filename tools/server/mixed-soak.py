#!/usr/bin/env python3
"""Run a controlled mixed-traffic soak against one live Strata server.

The workload alternates two, three and four concurrent requests.  Each cycle mixes short JSON,
about-2K-token code, an agent turn after a tool result, and one controlled long-context needle
(approximately 32K, 64K or 96K tokens).  Request prefixes are unique, so prompt-cache reuse does
not hide cold prefill cost.

While requests run, a background sampler records /metrics, process RSS, MemAvailable, GPU
temperature/power/memory and incremental engine-log counters.  The output is JSON Lines plus a
compact summary in JSON and text.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import pathlib
import re
import statistics
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict


MODEL = "qwen3.8-flash-next-iq3_xxs"
ENGINE_PROCESS_PATTERN = "[s]trata --serve"
SERVER_PROCESS_PATTERN = "serve/[s]erver.py --engine"
TOOLS = [{
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read one repository file.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
}]


def utc_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def json_dump_line(handle, lock: threading.Lock, row: dict) -> None:
    with lock:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()


def get_json(url: str, api_key: str, timeout: float = 20) -> dict:
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + api_key})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def pid_rss_mib(pattern: str) -> tuple[int | None, float | None]:
    found = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, timeout=5).stdout.split()
    if not found:
        return None, None
    pid = int(found[0])
    try:
        text = pathlib.Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
    except OSError:
        return pid, None
    match = re.search(r"^VmRSS:\s+(\d+)\s+kB", text, re.M)
    return pid, round(int(match.group(1)) / 1024, 1) if match else None


def meminfo() -> dict:
    wanted = {"MemAvailable", "MemFree", "Cached", "SwapFree"}
    values = {}
    try:
        for line in pathlib.Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, _, rest = line.partition(":")
            if key in wanted:
                values[key + "_MiB"] = round(int(rest.strip().split()[0]) / 1024, 1)
    except (OSError, ValueError, IndexError):
        pass
    return values


def gpu_sample() -> list[dict]:
    query = "index,temperature.gpu,power.draw,memory.used,utilization.gpu"
    try:
        done = subprocess.run(
            ["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in done.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 5:
            continue
        try:
            rows.append({
                "index": int(parts[0]),
                "temp_c": float(parts[1]),
                "power_w": float(parts[2]),
                "memory_mib": int(parts[3]),
                "util_pct": float(parts[4]),
            })
        except ValueError:
            continue
    return rows


class Sampler(threading.Thread):
    RE_HIT = re.compile(r"decode expert cache hit rate: ([\d.]+)% \(([\d,]+) hits / ([\d,]+) lookups\)")
    RE_RESIDENT = re.compile(r"resident RAM: ([\d.]+) GiB of experts in RAM, (\d+) exchanged with the VRAM tier")
    RE_KV = re.compile(r"KV streaming: ([\d.]+)% of (\d+) block reads hit VRAM")
    RE_FILE_TIER = re.compile(r"expert tiers: GPU \d+ hits this request; since the start RAM (\d+) blobs, files (\d+) blobs ([\d.]+) MB read")

    def __init__(self, base_url: str, api_key: str, log_path: pathlib.Path, out, lock: threading.Lock,
                 interval: float):
        super().__init__(daemon=True)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.log_path = log_path
        self.out = out
        self.out_lock = lock
        self.interval = interval
        self.stop_event = threading.Event()
        self.samples: list[dict] = []
        self.log_events = 0
        self.log_errors: list[str] = []
        self.log_counts: Counter = Counter()
        self.last_hit: dict | None = None
        self.last_resident: dict | None = None
        self.last_kv: dict | None = None
        self.last_file_tier: dict | None = None
        self.log_position = log_path.stat().st_size if log_path.exists() else 0
        self._log_pending = ""

    def _read_log_increment(self) -> None:
        if not self.log_path.exists():
            return
        with self.log_path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(self.log_position)
            chunk = handle.read(2 * 1024 * 1024)
            self.log_position = handle.tell()
        if not chunk:
            return
        text = self._log_pending + chunk
        lines = text.splitlines(keepends=True)
        self._log_pending = ""
        if lines and not lines[-1].endswith(("\n", "\r")):
            self._log_pending = lines.pop()
        for line in lines:
            self.log_events += 1
            if "piped read " in line:
                self.log_counts["piped_read"] += 1
            if "piped read " in line and "then the slots decoded" in line:
                self.log_counts["piped_decode"] += 1
            if "on a thread of its own" in line:
                self.log_counts["concurrent_read"] += 1
            if "the prompt read gives way" in line:
                self.log_counts["give_way"] += 1
            if "a read right after a read" in line:
                self.log_counts["read_lead_in"] += 1
            if "concurrent read lead-in skipped" in line:
                self.log_counts["concurrent_leadin_skip"] += 1
            elif "concurrent read lead-in" in line:
                self.log_counts["concurrent_leadin"] += 1
            if match := self.RE_HIT.search(line):
                self.last_hit = {
                    "pct": float(match.group(1)),
                    "hits": int(match.group(2).replace(",", "")),
                    "lookups": int(match.group(3).replace(",", "")),
                }
            if match := self.RE_RESIDENT.search(line):
                self.last_resident = {
                    "gib": float(match.group(1)),
                    "exchanged": int(match.group(2)),
                }
            if match := self.RE_KV.search(line):
                self.last_kv = {
                    "hit_pct": float(match.group(1)),
                    "block_reads": int(match.group(2)),
                }
            if match := self.RE_FILE_TIER.search(line):
                self.last_file_tier = {
                    "ram_blobs": int(match.group(1)),
                    "file_blobs": int(match.group(2)),
                    "file_mb": float(match.group(3)),
                }
            low = line.lower()
            if any(word in low for word in ("panic", "nonfinite", "segmentation fault", "engine died")) or (
                " error" in low and "no error" not in low
            ):
                self.log_errors.append(line.strip()[:500])

    def _sample(self) -> dict:
        row: dict = {"type": "sample", "ts": iso_now()}
        try:
            metrics = get_json(self.base_url + "/metrics", self.api_key, timeout=12)
            row["engine"] = {
                key: metrics.get("engine", {}).get(key)
                for key in ("version", "batch_slots", "expert_slots", "expert_cache_mib", "vram_free_mib",
                            "max_context", "kv")
            }
            row["live"] = {
                key: metrics.get("live", {}).get(key)
                for key in ("state", "queued", "running", "outside_slots", "waiting", "admitting")
            }
            row["totals"] = metrics.get("totals") or {}
        except Exception as error:  # noqa: BLE001 - a soak must retain the failure sample
            row["metrics_error"] = f"{type(error).__name__}: {error}"

        row["memory"] = meminfo()
        row["engine_pid"], row["engine_rss_mib"] = pid_rss_mib(ENGINE_PROCESS_PATTERN)
        row["server_pid"], row["server_rss_mib"] = pid_rss_mib(SERVER_PROCESS_PATTERN)
        row["gpus"] = gpu_sample()
        self._read_log_increment()
        row["last_hit"] = self.last_hit
        row["last_resident"] = self.last_resident
        row["last_kv"] = self.last_kv
        row["last_file_tier"] = self.last_file_tier
        self.samples.append(row)
        json_dump_line(self.out, self.out_lock, row)
        return row

    def run(self) -> None:
        while not self.stop_event.wait(self.interval):
            try:
                self._sample()
            except Exception as error:  # noqa: BLE001 - keep the load running and record the sampler error
                row = {"type": "sample", "ts": iso_now(), "sampler_error": f"{type(error).__name__}: {error}"}
                self.samples.append(row)
                json_dump_line(self.out, self.out_lock, row)
        try:
            self._sample()
        except Exception:
            pass


def filler(req_id: str, target_tokens: int, topic: str) -> str:
    # The benchmark records actual prompt tokens from usage.  This is intentionally approximate:
    # the unique first line makes every prompt cold, while short repeated lines keep target_token
    # and actual prompt_tokens within the same range instead of amplifying IDs into many tokens.
    line_count = max(32, int(target_tokens / 17))
    lines = [f"{req_id} {topic}."]
    lines.extend(
        "alpha bravo charlie delta echo foxtrot golf hotel kilo lima mike november."
        for _ in range(line_count - 1)
    )
    return "\n".join(lines)


def task_short(req_id: str) -> dict:
    return {
        "kind": "short_json",
        "target_context": 128,
        "max_tokens": 96,
        "messages": [{
            "role": "user",
            "content": (
                f"Request {req_id}. Return only compact JSON with keys status, score, note. "
                "status must be ok, score must be the number 7, note must be stable."
            ),
        }],
    }


def task_medium(req_id: str) -> dict:
    body = filler(req_id, 1800, "pipeline backlog code review")
    return {
        "kind": "medium_code",
        "target_context": 2048,
        "max_tokens": 256,
        "messages": [{
            "role": "user",
            "content": (
                "Inspect these synthetic backlog records and return a short Python function named "
                f"score_{req_id[-6:]} that counts records whose score is above 10. Do not explain.\n" + body
            ),
        }],
    }


def task_agent(req_id: str) -> dict:
    tool_text = filler(req_id, 3200, "tool output server log and source")
    return {
        "kind": "agent_tool",
        "target_context": 4096,
        "max_tokens": 384,
        "messages": [
            {
                "role": "system",
                "content": "You are a coding agent. Read tool results, identify the fault, and give one concrete next step.",
            },
            {"role": "user", "content": f"Investigate request {req_id} and summarize the next action."},
            {
                "role": "assistant",
                "content": "I will read the relevant file.",
                "tool_calls": [{
                    "id": "call_" + req_id,
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path":"serve/server.py"}'},
                }],
            },
            {"role": "tool", "tool_call_id": "call_" + req_id, "content": tool_text},
        ],
        "tools": TOOLS,
    }


def task_long(req_id: str, target_tokens: int) -> dict:
    secret = "P2-" + hashlib.sha256(req_id.encode()).hexdigest()[:12].upper()
    lines = filler(req_id, target_tokens, "long-context stability record").splitlines()
    lines[len(lines) // 2] = f"The access code for {req_id} is {secret}."
    return {
        "kind": f"long_{target_tokens // 1024}k",
        "target_context": target_tokens,
        "max_tokens": 32,
        "expected_answer": secret,
        "messages": [{
            "role": "user",
            "content": (
                "\n".join(lines) +
                f"\nReturn only the access code recorded for {req_id}. No explanation."
            ),
        }],
    }


WAVE_SHAPES = [
    ("short_json", "medium_code", "agent_tool", "long_32k"),
    ("short_json", "long_64k"),
    ("short_json", "medium_code", "agent_tool"),
    ("short_json", "short_json", "medium_code", "agent_tool"),
    ("short_json", "long_32k"),
    ("medium_code", "agent_tool", "short_json"),
    ("short_json", "short_json", "medium_code", "long_96k"),
    ("short_json", "medium_code"),
]
FAIRNESS_SHAPE = ("short_json", "short_json", "medium_code", "long_96k")
CTX256_SHAPES = WAVE_SHAPES + [("short_json", "medium_code", "agent_tool", "long_240k")]


def make_task(kind: str, wave: int, lane: int) -> dict:
    req_id = f"p2-{wave:04d}-{lane}-{time.time_ns()}"
    if kind == "short_json":
        return task_short(req_id)
    if kind == "medium_code":
        return task_medium(req_id)
    if kind == "agent_tool":
        return task_agent(req_id)
    return task_long(req_id, int(kind.split("_")[-1][:-1]) * 1024)


def post_task(base_url: str, api_key: str, task: dict, timeout: float) -> dict:
    body = {
        "model": MODEL,
        "messages": task["messages"],
        "max_tokens": task["max_tokens"],
        "temperature": 0,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if task.get("tools"):
        body["tools"] = task["tools"]
    payload = json.dumps(body, ensure_ascii=False).encode()
    request = urllib.request.Request(
        base_url.rstrip("/") + "/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key},
    )
    started_wall = time.time()
    started = time.monotonic()
    row = {
        "type": "request",
        "ts": iso_now(),
        "kind": task["kind"],
        "target_context": task["target_context"],
        "max_tokens": task["max_tokens"],
        "expected_answer": task.get("expected_answer"),
    }
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            row["http_status"] = response.status
            row["request_id"] = response.headers.get("x-request-id")
            parsed = json.loads(raw.decode())
        choice = (parsed.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        row.update({
            "status": "ok",
            "usage": parsed.get("usage"),
            "metrics": parsed.get("metrics"),
            "timings": parsed.get("timings"),
            "finish": choice.get("finish_reason"),
            "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "content_len": len(content),
            "tool_calls": len(message.get("tool_calls") or []),
            "answer_ok": None if not task.get("expected_answer") else content.strip() == task["expected_answer"],
            "content_prefix": content[:160],
        })
    except urllib.error.HTTPError as error:
        raw = error.read().decode(errors="replace")
        row.update({"status": "http_error", "http_status": error.code, "error": raw[:1000]})
    except Exception as error:  # noqa: BLE001 - a failed request is soak evidence
        row.update({"status": "error", "error": f"{type(error).__name__}: {error}"})
    row["started_at"] = started_wall
    row["wall_s"] = round(time.monotonic() - started, 3)
    row["tokens_out"] = int((row.get("usage") or {}).get("completion_tokens") or 0)
    row["tokens_prompt"] = int((row.get("usage") or {}).get("prompt_tokens") or 0)
    return row


def median(rows: list[dict], key: str) -> float | None:
    values = [row[key] for row in rows if isinstance(row.get(key), (int, float))]
    return round(statistics.median(values), 3) if values else None


def nested_median(rows: list[dict], outer: str, key: str) -> float | None:
    values = [
        row[outer][key]
        for row in rows
        if isinstance(row.get(outer), dict) and isinstance(row[outer].get(key), (int, float))
    ]
    return round(statistics.median(values), 3) if values else None


def summarize(meta: dict, requests: list[dict], waves: list[dict], samples: list[dict],
              sampler: Sampler) -> dict:
    errors = [row for row in requests if row.get("status") != "ok"]
    finishes = Counter(row.get("finish") for row in requests)
    by_kind = defaultdict(list)
    for row in requests:
        by_kind[row["kind"]].append(row)
    kind_summary = {}
    for kind, rows in sorted(by_kind.items()):
        ok_rows = [row for row in rows if row.get("status") == "ok"]
        kind_summary[kind] = {
            "requests": len(rows),
            "errors": len(rows) - len(ok_rows),
            "prompt_tokens_median": median(ok_rows, "tokens_prompt"),
            "prompt_tokens_min": min((row["tokens_prompt"] for row in ok_rows), default=None),
            "prompt_tokens_max": max((row["tokens_prompt"] for row in ok_rows), default=None),
            "wall_s_median": median(ok_rows, "wall_s"),
            "ttft_ms_median": nested_median(ok_rows, "metrics", "ttft_ms"),
            "slot_wait_ms_median": nested_median(ok_rows, "metrics", "slot_wait_ms"),
            "decode_tok_s_median": nested_median(ok_rows, "timings", "predicted_per_second"),
            "prompt_tok_s_median": nested_median(ok_rows, "timings", "prompt_per_second"),
            "finishes": dict(Counter(row.get("finish") for row in rows)),
        }

    wave_summary = {}
    for concurrency in range(1, 9):
        rows = [row for row in waves if row.get("concurrency") == concurrency]
        if rows:
            wave_summary[str(concurrency)] = {
                "waves": len(rows),
                "completion_tok_s_median": median(rows, "completion_tok_s"),
                "wall_s_median": median(rows, "wall_s"),
            }

    resource = {}
    if samples:
        first, last = samples[0], samples[-1]
        resource = {
            "engine_rss_mib_first": first.get("engine_rss_mib"),
            "engine_rss_mib_last": last.get("engine_rss_mib"),
            "mem_available_mib_first": (first.get("memory") or {}).get("MemAvailable_MiB"),
            "mem_available_mib_last": (last.get("memory") or {}).get("MemAvailable_MiB"),
            "mem_available_mib_min": min(
                ((sample.get("memory") or {}).get("MemAvailable_MiB") for sample in samples
                 if (sample.get("memory") or {}).get("MemAvailable_MiB") is not None),
                default=None,
            ),
            "max_live_running": max(
                (int((sample.get("live") or {}).get("running") or 0) for sample in samples), default=0
            ),
            "max_live_outside_slots": max(
                (int((sample.get("live") or {}).get("outside_slots") or 0) for sample in samples), default=0
            ),
            "gpus": {},
        }
        for index in range(2):
            gpu_rows = [sample["gpus"][index] for sample in samples if len(sample.get("gpus") or []) > index]
            if gpu_rows:
                resource["gpus"][str(index)] = {
                    "temp_c_min": min(row["temp_c"] for row in gpu_rows),
                    "temp_c_max": max(row["temp_c"] for row in gpu_rows),
                    "power_w_median": round(statistics.median(row["power_w"] for row in gpu_rows), 1),
                    "power_w_max": max(row["power_w"] for row in gpu_rows),
                    "memory_mib_min": min(row["memory_mib"] for row in gpu_rows),
                    "memory_mib_max": max(row["memory_mib"] for row in gpu_rows),
                }

    finish_length_count = sum(1 for row in requests if row.get("finish") == "length")
    answer_failures = sum(1 for row in requests if row.get("answer_ok") is False)
    return {
        "type": "summary",
        "meta": meta,
        "requests": len(requests),
        "waves": len(waves),
        "errors": len(errors),
        "error_samples": errors[:5],
        "finishes": dict(finishes),
        "finish_length_count": finish_length_count,
        "needle_answer_failures": answer_failures,
        "by_kind": kind_summary,
        "by_wave_concurrency": wave_summary,
        "resources": resource,
        "samples": len(samples),
        "max_sampled_engine_hit_pct": (sampler.last_hit or {}).get("pct"),
        "last_resident": sampler.last_resident,
        "last_kv": sampler.last_kv,
        "last_file_tier": sampler.last_file_tier,
        "log_events_scanned": sampler.log_events,
        "log_counts": dict(sampler.log_counts),
        "log_errors": sampler.log_errors[-20:],
        "log_error_count": len(sampler.log_errors),
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--api-key", default=os.environ.get("STRATA_API_KEY", "111"))
    parser.add_argument("--duration", type=int, default=3600, help="seconds of load")
    parser.add_argument("--sample-every", type=float, default=10)
    parser.add_argument("--request-timeout", type=float, default=1800)
    parser.add_argument("--profile", choices=("mixed", "fairness", "ctx256"), default="mixed",
                        help="mixed: the 8-wave soak; fairness: repeat the 4-way 96K-neighbor wave; "
                             "ctx256: mixed plus a near-256K cold read each cycle")
    parser.add_argument("--waves", type=int, default=0, help="stop after N waves (0: use duration)")
    parser.add_argument("--engine-log", type=pathlib.Path,
                        default=pathlib.Path("/opt/ai/src/strata-0.2.2/strata-022.log"))
    parser.add_argument("--out", type=pathlib.Path)
    parser.add_argument("--summary", type=pathlib.Path)
    parser.add_argument("--dry-run", action="store_true", help="build one cycle and print sizes; no requests")
    options = parser.parse_args(argv)

    stamp = utc_stamp()
    out_path = options.out or pathlib.Path(f"/opt/ai/logs/P2-mixed-soak-{stamp}.jsonl")
    summary_path = options.summary or out_path.with_suffix(".summary.json")
    text_path = summary_path.with_suffix(".txt")
    shapes = {
        "mixed": WAVE_SHAPES,
        "fairness": [FAIRNESS_SHAPE],
        "ctx256": CTX256_SHAPES,
    }[options.profile]

    if options.dry_run:
        for wave, shape in enumerate(shapes):
            rows = []
            for lane, kind in enumerate(shape):
                task = make_task(kind, wave, lane)
                body_bytes = len(json.dumps(task, ensure_ascii=False).encode())
                rows.append({"kind": kind, "target_context": task["target_context"], "bytes": body_bytes})
            print(json.dumps({"wave": wave, "shape": rows}, ensure_ascii=False))
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() or summary_path.exists() or text_path.exists():
        parser.error("output paths already exist; use a new run stamp")
    meta = {
        "started_at": iso_now(),
        "duration_s": options.duration,
        "base_url": options.base_url,
        "sample_every_s": options.sample_every,
        "model": MODEL,
        "shape": "2/3/4 concurrent; short JSON + 2K code + agent tool turn + 32K/64K/96K needle",
        "profile": options.profile,
        "out": str(out_path),
        "engine_log": str(options.engine_log),
    }
    requests: list[dict] = []
    waves: list[dict] = []
    lock = threading.Lock()
    with out_path.open("w", encoding="utf-8") as out:
        json_dump_line(out, lock, {"type": "meta", **meta})
        sampler = Sampler(options.base_url, options.api_key, options.engine_log, out, lock, options.sample_every)
        sampler.start()
        ready = False
        try:
            health = get_json(options.base_url.rstrip("/") + "/health", options.api_key)
            ready = health.get("status") == "ok" and health.get("loaded") is True
            json_dump_line(out, lock, {"type": "health", "ts": iso_now(), "health": health})
        except Exception as error:  # noqa: BLE001
            json_dump_line(out, lock, {
                "type": "health", "ts": iso_now(),
                "error": f"{type(error).__name__}: {error}",
            })
        if not ready:
            sampler.stop_event.set()
            sampler.join(30)
            print("health is not ready; no workload sent", flush=True)
            return 2

        started = time.monotonic()
        wave_index = 0
        try:
            while (time.monotonic() - started < options.duration and
                   (options.waves <= 0 or wave_index < options.waves)):
                shape = shapes[wave_index % len(shapes)]
                tasks = [make_task(kind, wave_index, lane) for lane, kind in enumerate(shape)]
                wave_started_wall = time.time()
                wave_started = time.monotonic()
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as pool:
                    futures = [
                        pool.submit(post_task, options.base_url, options.api_key, task, options.request_timeout)
                        for task in tasks
                    ]
                    rows = [future.result() for future in futures]
                wave_wall = time.monotonic() - wave_started
                for row in rows:
                    json_dump_line(out, lock, row)
                requests.extend(rows)
                wave = {
                    "type": "wave",
                    "ts": iso_now(),
                    "wave": wave_index,
                    "concurrency": len(rows),
                    "kinds": [row["kind"] for row in rows],
                    "started_at": wave_started_wall,
                    "wall_s": round(wave_wall, 3),
                    "completion_tokens": sum(row["tokens_out"] for row in rows),
                    "prompt_tokens": sum(row["tokens_prompt"] for row in rows),
                    "errors": sum(1 for row in rows if row.get("status") != "ok"),
                    "completion_tok_s": round(sum(row["tokens_out"] for row in rows) / wave_wall, 2),
                }
                waves.append(wave)
                json_dump_line(out, lock, wave)
                print(
                    f"wave {wave_index:04d} n={len(rows)} wall={wave_wall:6.1f}s "
                    f"prompt={wave['prompt_tokens']:6d} out={wave['completion_tokens']:5d} "
                    f"agg={wave['completion_tok_s']:6.1f} tok/s "
                    f"kinds={','.join(row['kind'] for row in rows)}",
                    flush=True,
                )
                wave_index += 1
                if (time.monotonic() - started < options.duration and
                        (options.waves <= 0 or wave_index < options.waves)):
                    time.sleep(1)
        finally:
            sampler.stop_event.set()
            sampler.join(30)

    summary = summarize(meta, requests, waves, sampler.samples, sampler)
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = [
        f"requests: {summary['requests']} in {summary['waves']} waves; errors: {summary['errors']}",
        f"finishes: {summary['finishes']}; finish=length: {summary['finish_length_count']}",
        f"needle answer failures: {summary['needle_answer_failures']}",
        f"samples: {summary['samples']}; max running: {summary['resources'].get('max_live_running')}; "
        f"max outside slots: {summary['resources'].get('max_live_outside_slots')}",
        f"engine RSS MiB: {summary['resources'].get('engine_rss_mib_first')} -> "
        f"{summary['resources'].get('engine_rss_mib_last')}",
        f"MemAvailable MiB: {summary['resources'].get('mem_available_mib_first')} -> "
        f"{summary['resources'].get('mem_available_mib_last')} (min "
        f"{summary['resources'].get('mem_available_mib_min')})",
        f"last expert cache hit: {summary['max_sampled_engine_hit_pct']}%",
        f"last resident: {summary['last_resident']}",
        f"last KV: {summary['last_kv']}",
        f"log errors: {summary['log_error_count']}",
        f"jsonl: {out_path}",
        f"summary: {summary_path}",
    ]
    for kind, data in summary["by_kind"].items():
        lines.append(
            f"{kind:12s} n={data['requests']:3d} err={data['errors']} "
            f"prompt={data['prompt_tokens_median']} ttft={data['ttft_ms_median']}ms "
            f"decode={data['decode_tok_s_median']} tok/s wall={data['wall_s_median']}s"
        )
    text_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines), flush=True)
    return 0 if summary["errors"] == 0 and summary["needle_answer_failures"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))
