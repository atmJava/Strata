#!/usr/bin/env python3
"""Run unique sequential long-context requests against one Strata server.

The prompt-attention A/B uses three cold 96K requests per arm. A reusable prompt
sequence is generated from a fixed salt, so every arm after a clean restart sees
the same text without relying on request timestamps.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import statistics
import sys
import threading
import time


ROOT = pathlib.Path(__file__).resolve().parents[2]
MIXED_SOAK = ROOT / "tools" / "server" / "mixed-soak.py"


def load_mixed_soak(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location("mixed_soak", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def median(rows: list[dict], path: tuple[str, ...]) -> float | None:
    values = []
    for row in rows:
        value = row
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        if isinstance(value, (int, float)):
            values.append(value)
    return round(statistics.median(values), 3) if values else None


def process_env(pid: int | None) -> dict[str, object]:
    if pid is None:
        return {}
    try:
        raw = pathlib.Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return {}
    values: dict[str, str] = {}
    for item in raw.split(b"\0"):
        key, separator, value = item.partition(b"=")
        if separator:
            values[key.decode(errors="replace")] = value.decode(errors="replace")
    names = (
        "STRATA_PA_V2T",
        "STRATA_PROMPT_ATTN_V1",
        "STRATA_SM75_PERF",
        "STRATA_SELECT_SIMT",
    )
    return {name: values.get(name) for name in names}


def summary(meta: dict, rows: list[dict], sampler, elapsed: float) -> dict:
    errors = [row for row in rows if row.get("status") != "ok"]
    resources = {}
    if sampler.samples:
        first, last = sampler.samples[0], sampler.samples[-1]
        gpu_rows = [sample.get("gpus") or [] for sample in sampler.samples]
        resources = {
            "engine_rss_mib_first": first.get("engine_rss_mib"),
            "engine_rss_mib_last": last.get("engine_rss_mib"),
            "mem_available_mib_first": (first.get("memory") or {}).get("MemAvailable_MiB"),
            "mem_available_mib_last": (last.get("memory") or {}).get("MemAvailable_MiB"),
            "mem_available_mib_min": min(
                (
                    (sample.get("memory") or {}).get("MemAvailable_MiB")
                    for sample in sampler.samples
                    if (sample.get("memory") or {}).get("MemAvailable_MiB") is not None
                ),
                default=None,
            ),
            "swap_free_mib_min": min(
                (
                    (sample.get("memory") or {}).get("SwapFree_MiB")
                    for sample in sampler.samples
                    if (sample.get("memory") or {}).get("SwapFree_MiB") is not None
                ),
                default=None,
            ),
            "gpu_memory_mib_max": [
                max((row[index]["memory_mib"] for row in gpu_rows if len(row) > index), default=None)
                for index in range(2)
            ],
        }
    return {
        "type": "summary",
        "meta": meta,
        "requests": len(rows),
        "errors": len(errors),
        "error_samples": errors[:5],
        "needle_failures": sum(1 for row in rows if row.get("answer_ok") is False),
        "finish_length_count": sum(1 for row in rows if row.get("finish") == "length"),
        "prompt_tokens": [row.get("tokens_prompt") for row in rows],
        "engine_prompt_ms_median": median(rows, ("timings", "prompt_ms")),
        "engine_prompt_tps_median": median(rows, ("timings", "prompt_per_second")),
        "wall_s_median": median(rows, ("wall_s",)),
        "metrics_wall_ms_median": median(rows, ("metrics", "wall_ms")),
        "metrics_ttft_ms_median": median(rows, ("metrics", "ttft_ms")),
        "elapsed_s": round(elapsed, 3),
        "resources": resources,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=pathlib.Path)
    parser.add_argument("--mixed-soak", type=pathlib.Path, default=MIXED_SOAK)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--api-key")
    parser.add_argument("--label", required=True)
    parser.add_argument("--target-tokens", type=int, default=98304)
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--salt", default="p6-20261031-current")
    parser.add_argument("--engine-log", type=pathlib.Path)
    parser.add_argument("--out", required=True, type=pathlib.Path)
    parser.add_argument("--summary", required=True, type=pathlib.Path)
    parser.add_argument("--request-timeout", type=float, default=1800)
    parser.add_argument("--sample-every", type=float, default=2)
    options = parser.parse_args(argv)

    config = json.loads(options.config.read_text(encoding="utf-8"))
    api_key = options.api_key or config.get("api_key") or os.environ.get("STRATA_API_KEY", "111")
    engine_log = options.engine_log or pathlib.Path(config.get("log") or "/dev/null")
    if options.out.exists() or options.summary.exists():
        parser.error("output paths already exist; use a new run stamp")

    mixed = load_mixed_soak(options.mixed_soak)
    options.out.parent.mkdir(parents=True, exist_ok=True)
    options.summary.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    rows: list[dict] = []
    started = time.monotonic()
    with options.out.open("w", encoding="utf-8") as handle:
        engine_pid, _ = mixed.pid_rss_mib(mixed.ENGINE_PROCESS_PATTERN)
        meta = {
            "type": "meta",
            "arm": options.label,
            "profile": "sequential-long-context",
            "started_at": mixed.iso_now(),
            "base_url": options.base_url,
            "config": str(options.config),
            "config_sha256": hashlib.sha256(options.config.read_bytes()).hexdigest(),
            "engine_pid": engine_pid,
            "runtime_env": process_env(engine_pid),
            "target_tokens": options.target_tokens,
            "count": options.count,
            "salt": options.salt,
        }
        mixed.json_dump_line(handle, lock, meta)
        sampler = mixed.Sampler(
            options.base_url,
            api_key,
            engine_log,
            handle,
            lock,
            options.sample_every,
        )
        sampler.start()
        try:
            health = mixed.get_json(options.base_url.rstrip("/") + "/health", api_key)
            mixed.json_dump_line(handle, lock, {"type": "health", "ts": mixed.iso_now(), "health": health})
            if health.get("status") != "ok" or health.get("loaded") is not True:
                print("health is not ready; no workload sent", file=sys.stderr)
                return 2
            for sequence in range(options.count):
                request_id = f"{options.salt}-{sequence}"
                task = mixed.task_long(request_id, options.target_tokens)
                row = mixed.post_task(options.base_url, api_key, task, options.request_timeout)
                row.update({"arm": options.label, "sequence": sequence, "runner": "sequential-long-context"})
                rows.append(row)
                mixed.json_dump_line(handle, lock, row)
                print(
                    f"{options.label} {sequence + 1}/{options.count}: "
                    f"prompt={row.get('tokens_prompt')} "
                    f"read={((row.get('timings') or {}).get('prompt_ms'))}ms "
                    f"wall={row.get('wall_s')}s status={row.get('status')} "
                    f"needle={row.get('answer_ok')}",
                    flush=True,
                )
        finally:
            sampler.stop_event.set()
            sampler.join(30)

    result = summary(meta, rows, sampler, time.monotonic() - started)
    options.summary.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["errors"] == 0 and result["needle_failures"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
