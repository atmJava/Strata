#!/usr/bin/env python3
"""Collect a bounded 2/4/6/8-stream decode-window profile.

This tool does not enable instrumentation itself. Run it against an engine whose config has already
been started with STRATA_VERIFY_PROFILE / STRATA_BATCH_TELEMETRY when those records are wanted.
It keeps the inference load frozen, records every response, samples live slot occupancy, and joins
the raw BSTAT/PSTAT/stage records from the server log to each repetition.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import pathlib
import re
import statistics
import threading
import time
import urllib.error
import urllib.request


PROMPTS = [
    "用大约 400 字解释混合专家（MoE）模型的工作原理，包括路由与负载均衡。",
    "用大约 400 字解释为什么 NVMe SSD 的随机读延迟比机械硬盘低两个数量级。",
    "用大约 400 字解释 Transformer 里 KV 缓存的作用，以及它为什么随上下文线性增长。",
    "用大约 400 字解释 speculative decoding（投机解码）如何在不改变输出的前提下加速。",
    "用大约 400 字解释为什么 PCIe 4.0 x16 的双向带宽要按单向标称值的一半来算。",
    "用大约 400 字解释 NUMA 架构下跨节点访存为什么比本地访存慢。",
    "用大约 400 字解释为什么 batch size 越大，单 token 的边际延迟越低。",
    "用大约 400 字解释操作系统页缓存（page cache）对模型权重读取的影响。",
]
WARMUP_PROMPT = "用一句话说明什么是 DNS 解析。"

PROFILE_NAMES = [
    "q8+qkv/q-idx gemv", "k/v+norm-rope", "kv+idx append", "q+q-idx", "scores+topk",
    "router+plan+bell", "shared+quant", "VRAM hits", "PCIe grp", "copy+combine", "head",
    "hc-read0", "conv", "rec", "kv-resolve", "attention", "gate", "out-proj", "waitA",
    "waitB", "waitCPU", "(gap)", "hc0 norm", "hc0 down", "hc0 up", "hc1 norm", "hc1 down",
    "hc1 up", "ab", "z",
]
PROFILE_TOKEN = re.compile(
    r"(?P<name>" + "|".join(re.escape(name) for name in sorted(PROFILE_NAMES, key=len, reverse=True)) +
    r")\s+(?P<value>-?\d+(?:\.\d+)?)"
)
BSTAT_LINE = re.compile(r"^BSTAT ([1-9][0-9]*) ([1-9][0-9]*) ([1-9][0-9]*) ([1-9][0-9]*)\r?$")
PSTAT_LINE = re.compile(r"^PSTAT ([1-9][0-9]*) ([1-9][0-9]*) ((?:0|[1-9][0-9]*)(?: (?:0|[1-9][0-9]*))*)\r?$")
STAGE_LINE = re.compile(r"strata batch GPU stages, stage ([0-9]+) \(ms/window\):(.*)")
TOTAL_STAGE = re.compile(r"total (-?\d+(?:\.\d+)?) ms/window over ([0-9]+) windows")


def request_json(url: str, body: dict | None, api_key: str, timeout: float) -> dict:
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    request = urllib.request.Request(
        url,
        data=None if body is None else json.dumps(body).encode(),
        headers=headers,
        method="GET" if body is None else "POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def chat(base: str, model: str, prompt: str, api_key: str, max_tokens: int) -> dict:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    started = time.monotonic()
    response = request_json(base + "/v1/chat/completions", body, api_key, 900)
    return {
        "started": started,
        "ended": time.monotonic(),
        "wall_s": time.monotonic() - started,
        "tokens": (response.get("usage") or {}).get("completion_tokens"),
        "prompt_tokens": (response.get("usage") or {}).get("prompt_tokens"),
        "cached_tokens": ((response.get("usage") or {}).get("prompt_tokens_details") or {}).get("cached_tokens"),
        "finish": (response.get("choices") or [{}])[0].get("finish_reason"),
        "metrics": response.get("metrics") or {},
        "timings": response.get("timings") or {},
    }


def totals(base: str, api_key: str) -> dict:
    response = request_json(base + "/metrics", None, api_key, 10)
    return response.get("totals") or {}


def telemetry(base: str, api_key: str) -> dict:
    return request_json(base + "/v1/telemetry", None, api_key, 10)


class SlotWatcher:
    def __init__(self, base: str, api_key: str, interval: float = 0.25):
        self.base = base
        self.api_key = api_key
        self.interval = interval
        self.started = 0.0
        self.stop_event = threading.Event()
        self.samples: list[dict] = []
        self.errors = 0
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self.stop_event.wait(self.interval):
            try:
                snapshot = telemetry(self.base, self.api_key)
                work = snapshot.get("work") or {}
                slots = work.get("slots") or []
                self.samples.append({
                    "t": time.monotonic() - self.started,
                    "occupied": int(work.get("occupied_slots") or 0),
                    "waiting": int(work.get("waiting") or 0),
                    "queued": int(work.get("queued") or 0),
                    "states": [slot.get("state") for slot in slots],
                    "generated": [slot.get("generated") for slot in slots],
                    "groups": [slot.get("group") for slot in slots],
                    "batch": {
                        key: work.get(key) for key in (
                            "batch_sample_windows", "batch_sample_rows", "batch_sample_active_rows",
                            "batch_sample_span_s", "batch_sample_age_s",
                        )
                    } if work.get("batch_sample_windows") is not None else None,
                    "stage": {
                        "in_flight_us": work.get("stage_in_flight"),
                        "span_s": work.get("stage_sample_span_s"),
                        "age_s": work.get("stage_sample_age_s"),
                    } if work.get("stage_in_flight") else None,
                })
            except Exception:
                self.errors += 1

    def start(self) -> None:
        self.started = time.monotonic()
        self.thread.start()

    def finish(self, wall_s: float) -> dict:
        self.stop_event.set()
        self.thread.join(timeout=5)
        samples = self.samples
        times = [0.0] + [float(sample["t"]) for sample in samples] + [wall_s]
        occupied = [samples[0]["occupied"] if samples else 0] + \
                   [int(sample["occupied"]) for sample in samples] + \
                   [samples[-1]["occupied"] if samples else 0]
        weighted_occupied = 0.0
        total_time = 0.0
        for index in range(len(occupied) - 1):
            delta = max(0.0, times[index + 1] - times[index])
            weighted_occupied += occupied[index] * delta
            total_time += delta
        state_seconds: dict[str, float] = {}
        for index, sample in enumerate(samples):
            if index + 1 < len(samples):
                delta = max(0.0, samples[index + 1]["t"] - sample["t"])
            else:
                delta = max(0.0, wall_s - sample["t"])
            for state, group in zip(sample["states"], sample["groups"]):
                key = f"group{group}:{state}"
                state_seconds[key] = state_seconds.get(key, 0.0) + delta
        group_occupied: dict[str, list[int]] = {}
        for sample in samples:
            counts = [0, 0]
            for state, group in zip(sample["states"], sample["groups"]):
                if isinstance(group, int) and 0 <= group < len(counts):
                    counts[group] += int(state not in (None, "idle"))
            for group, count in enumerate(counts):
                group_occupied.setdefault(str(group), []).append(count)
        batch_samples = unique_consecutive(
            [sample["batch"] for sample in samples if sample.get("batch")], lambda row: tuple(sorted(row.items()))
        )
        stage_samples = unique_consecutive(
            [sample["stage"] for sample in samples if sample.get("stage")],
            lambda row: (tuple(row.get("in_flight_us") or []), row.get("span_s")),
        )
        return {
            "samples": len(samples),
            "errors": self.errors,
            "mean_occupied": round(weighted_occupied / total_time, 3) if total_time > 0 else None,
            "mean_occupied_fraction": round(weighted_occupied / total_time / 6.0, 4) if total_time > 0 else None,
            "fraction_at_six": round(sum(sample["occupied"] >= 6 for sample in samples) / len(samples), 4)
            if samples else None,
            "fraction_below_six": round(sum(sample["occupied"] < 6 for sample in samples) / len(samples), 4)
            if samples else None,
            "peak_waiting": max((sample["waiting"] for sample in samples), default=0),
            "peak_queued": max((sample["queued"] for sample in samples), default=0),
            "mean_group_occupied": {
                group: round(statistics.fmean(values), 3) for group, values in group_occupied.items()
            },
            "state_seconds": {key: round(value, 3) for key, value in sorted(state_seconds.items())},
            "bstat": aggregate_bstat_samples(batch_samples),
            "pstat": aggregate_pstat_samples(stage_samples),
        }


def unique_consecutive(items: list[dict], key) -> list[dict]:
    output = []
    previous = None
    for item in items:
        marker = key(item)
        if marker != previous:
            output.append(item)
            previous = marker
    return output


def aggregate_bstat_samples(samples: list[dict]) -> dict | None:
    windows = sum(int(row.get("batch_sample_windows") or 0) for row in samples)
    processed = sum(int(row.get("batch_sample_rows") or 0) for row in samples)
    active = sum(int(row.get("batch_sample_active_rows") or 0) for row in samples)
    elapsed_ms = round(1000.0 * sum(float(row.get("batch_sample_span_s") or 0.0) for row in samples))
    if windows < 1 or processed < 1 or active < 1:
        return None
    return {
        "samples": len(samples),
        "windows": windows,
        "processed_rows": processed,
        "active_rows": active,
        "elapsed_ms": elapsed_ms,
        "processed_rows_per_window": round(processed / windows, 4),
        "active_rows_per_window": round(active / windows, 4),
        "padding_rows_per_window": round((processed - active) / windows, 4),
        "active_to_processed": round(active / processed, 6),
    }


def aggregate_pstat_samples(samples: list[dict]) -> dict | None:
    if not samples:
        return None
    counts = [len(row.get("in_flight_us") or []) for row in samples]
    if not counts or any(count != counts[0] for count in counts):
        return None
    count = counts[0]
    elapsed_us = round(1_000_000.0 * sum(float(row.get("span_s") or 0.0) for row in samples))
    busy_us = [
        sum(int(row["in_flight_us"][index]) for row in samples)
        for index in range(count)
    ]
    return {
        "samples": len(samples),
        "elapsed_us": elapsed_us,
        "busy_us": busy_us,
        "busy_fraction": [round(value / elapsed_us, 6) if elapsed_us else None for value in busy_us],
    }


def parse_log(text: str) -> dict:
    bstat = []
    pstat = []
    stages = []
    bdones = []
    slot_events = []
    for raw in text.splitlines():
        line = raw.strip()
        match = BSTAT_LINE.fullmatch(line)
        if match:
            windows, rows, active, elapsed_ms = map(int, match.groups())
            bstat.append({"windows": windows, "processed_rows": rows, "active_rows": active,
                          "elapsed_ms": elapsed_ms})
            continue
        match = PSTAT_LINE.fullmatch(line)
        if match:
            count, elapsed_us = int(match.group(1)), int(match.group(2))
            busy = [int(value) for value in match.group(3).split()]
            if len(busy) == count:
                pstat.append({"elapsed_us": elapsed_us, "in_flight_us": busy})
            continue
        match = STAGE_LINE.search(line)
        if match:
            section = match.group(2)
            row = {"stage": int(match.group(1)), "kinds": {}, "total_ms": None, "windows": None}
            for part in section.split("|"):
                part = part.strip()
                kind = next((name for name in ("GDN", "QSA") if part.startswith(name + " layers:")), None)
                if kind:
                    body = part.split(":", 1)[1]
                    row["kinds"][kind] = {
                        name: float(value) for name, value in PROFILE_TOKEN.findall(body)
                    }
                    continue
                total = TOTAL_STAGE.search(part)
                if total:
                    row["total_ms"] = float(total.group(1))
                    row["windows"] = int(total.group(2))
            stages.append(row)
            continue
        if line.startswith("BDONE "):
            fields = line.split()
            if len(fields) >= 4:
                bdones.append({"slot": int(fields[1]), "tokens": int(fields[2]), "finish": fields[3],
                               "wall_ms": float(fields[4]) if len(fields) > 4 else None})
            continue
        if line.startswith("strata batch: slot "):
            slot_events.append(line)
    aggregate_bstat = None
    if bstat:
        windows = sum(row["windows"] for row in bstat)
        processed = sum(row["processed_rows"] for row in bstat)
        active = sum(row["active_rows"] for row in bstat)
        elapsed = sum(row["elapsed_ms"] for row in bstat)
        aggregate_bstat = {
            "samples": len(bstat),
            "windows": windows,
            "processed_rows": processed,
            "active_rows": active,
            "elapsed_ms": elapsed,
            "processed_rows_per_window": round(processed / windows, 4),
            "active_rows_per_window": round(active / windows, 4),
            "padding_rows_per_window": round((processed - active) / windows, 4),
            "active_to_processed": round(active / processed, 6),
        }
    aggregate_pstat = None
    if pstat:
        elapsed = sum(row["elapsed_us"] for row in pstat)
        stage_count = len(pstat[0]["in_flight_us"])
        busy = [sum(row["in_flight_us"][index] for row in pstat if len(row["in_flight_us"]) == stage_count)
                for index in range(stage_count)]
        aggregate_pstat = {
            "samples": len(pstat),
            "elapsed_us": elapsed,
            "busy_us": busy,
            "busy_fraction": [round(value / elapsed, 6) if elapsed else None for value in busy],
        }
    return {
        "bstat": aggregate_bstat,
        "pstat": aggregate_pstat,
        "stages": stages,
        "bdones": bdones,
        "slot_event_count": len(slot_events),
        "slot_events": slot_events[-40:],
    }


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def request_summary(runs: list[dict], wave_started: float, wall_s: float) -> dict:
    values: dict[str, list[float]] = {
        "ttft_ms": [], "slot_wait_ms": [], "itl_ms_mean": [], "wall_ms": [], "decode_tok_s": [],
        "prompt_ms": [],
    }
    normalized = []
    for run in runs:
        metrics = run["metrics"]
        timings = run["timings"]
        for source, key, target in (
            (metrics, "ttft_ms", "ttft_ms"),
            (metrics, "slot_wait_ms", "slot_wait_ms"),
            (metrics, "itl_ms_mean", "itl_ms_mean"),
            (metrics, "wall_ms", "wall_ms"),
            (timings, "predicted_per_second", "decode_tok_s"),
            (timings, "prompt_ms", "prompt_ms"),
        ):
            if isinstance(source.get(key), (int, float)):
                values[target].append(float(source[key]))
        normalized.append({
            "started_offset_s": round(run["started"] - wave_started, 3),
            "ended_offset_s": round(run["ended"] - wave_started, 3),
            "wall_s": round(run["wall_s"], 3),
            "tokens": run["tokens"],
            "prompt_tokens": run["prompt_tokens"],
            "cached_tokens": run["cached_tokens"],
            "finish": run["finish"],
            "ttft_ms": metrics.get("ttft_ms"),
            "slot_wait_ms": metrics.get("slot_wait_ms"),
            "itl_ms_mean": metrics.get("itl_ms_mean"),
            "wall_ms": metrics.get("wall_ms"),
            "decode_tok_s": timings.get("predicted_per_second"),
            "prompt_ms": timings.get("prompt_ms"),
            "decode_ms": timings.get("predicted_ms"),
        })
    tokens = sum(run["tokens"] or 0 for run in runs)
    walls = [run["wall_s"] for run in runs]
    median_wall = statistics.median(walls) if walls else None
    return {
        "requests": len(runs),
        "aggregate_tok_s": round(tokens / wall_s, 3) if wall_s > 0 else None,
        "wall_s": round(wall_s, 3),
        "tokens": tokens,
        "finish": sorted({run["finish"] for run in runs}, key=lambda value: str(value)),
        "median": {key: round(statistics.median(items), 3) if items else None for key, items in values.items()},
        "p90": {key: round(percentile(items, 0.9), 3) if items else None for key, items in values.items()},
        "max": {key: round(max(items), 3) if items else None for key, items in values.items()},
        "tail_wall_ratio": round(max(walls) / median_wall, 4) if median_wall else None,
        "request_rows": sorted(normalized, key=lambda row: (row["ended_offset_s"], row["started_offset_s"])),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--api-key", default=os.environ.get("STRATA_API_KEY", "111"))
    parser.add_argument("--model", default="qwen3.8-flash-next-iq3_xxs")
    parser.add_argument("--label", required=True)
    parser.add_argument("--engine-log", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--levels", default="2,4,6,8")
    parser.add_argument("--max-tokens", type=int, default=400)
    parser.add_argument("--settle-s", type=float, default=2.0)
    args = parser.parse_args()
    levels = [int(value) for value in args.levels.split(",")]
    if any(value < 1 or value > len(PROMPTS) for value in levels):
        parser.error(f"levels must be within 1..{len(PROMPTS)}")

    output = {
        "label": args.label,
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base_url": args.base_url,
        "model": args.model,
        "reps": args.reps,
        "levels": levels,
        "max_tokens": args.max_tokens,
        "requests": [],
        "errors": [],
        "warmup": None,
        "waves": [],
    }

    try:
        output["identity_before"] = telemetry(args.base_url, args.api_key)
        warmup = chat(args.base_url, args.model, WARMUP_PROMPT, args.api_key, args.max_tokens)
        output["warmup"] = {
            "tokens": warmup["tokens"], "wall_s": round(warmup["wall_s"], 3), "finish": warmup["finish"]
        }
    except Exception as error:
        output["errors"].append(f"warmup: {type(error).__name__}: {error}")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(output, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(json.dumps({"label": args.label, "output": str(args.output), "errors": output["errors"]}))
        return 1

    for level in levels:
        for repetition in range(1, args.reps + 1):
            wave: dict = {"level": level, "repetition": repetition}
            before_totals = totals(args.base_url, args.api_key)
            log_offset = args.engine_log.stat().st_size if args.engine_log.exists() else 0
            watcher = SlotWatcher(args.base_url, args.api_key)
            barrier = threading.Barrier(level + 1)
            runs: list[dict] = []
            run_errors: list[str] = []

            def worker(index: int) -> None:
                barrier.wait()
                try:
                    runs.append(chat(args.base_url, args.model, PROMPTS[index], args.api_key, args.max_tokens))
                except Exception as error:
                    run_errors.append(f"request {index}: {type(error).__name__}: {error}")

            watcher.start()
            wave_started = time.monotonic()
            with concurrent.futures.ThreadPoolExecutor(max_workers=level) as pool:
                futures = [pool.submit(worker, index) for index in range(level)]
                barrier.wait()
                for future in futures:
                    future.result()
            wall_s = time.monotonic() - wave_started
            watcher_summary = watcher.finish(wall_s)
            after_totals = totals(args.base_url, args.api_key)
            time.sleep(args.settle_s)

            with args.engine_log.open("rb") as source:
                source.seek(log_offset)
                bytes_after = source.read()
            parsed = parse_log(bytes_after.decode("utf-8", "replace"))
            wave.update({
                "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "wall_s": round(wall_s, 3),
                "log_offset": log_offset,
                "log_bytes": len(bytes_after),
                "slot_watcher": watcher_summary,
                "requests": request_summary(runs, wave_started, wall_s) if runs else None,
                "bstat": watcher_summary["bstat"] or parsed["bstat"],
                "pstat": watcher_summary["pstat"] or parsed["pstat"],
                "stages": parsed["stages"],
                "bdones": parsed["bdones"],
                "slot_event_count": parsed["slot_event_count"],
                "slot_events": parsed["slot_events"],
                "foreign_requests": max(0, int(after_totals.get("requests") or 0) -
                                          int(before_totals.get("requests") or 0) - level),
                "request_errors": run_errors,
            })
            output["requests"].extend(runs)
            output["errors"].extend(run_errors)
            output["waves"].append(wave)
            request_row = wave["requests"]
            print(
                f"{args.label} N={level} rep={repetition}: "
                f"agg={request_row['aggregate_tok_s'] if request_row else None} tok/s "
                f"occupied={watcher_summary['mean_occupied']} "
                f"tail={request_row['tail_wall_ratio'] if request_row else None} "
                f"bstat={'yes' if wave['bstat'] else 'no'} "
                f"stages={len(wave['stages'])} errors={len(run_errors)}",
                flush=True,
            )
            time.sleep(args.settle_s)

    try:
        output["identity_after"] = telemetry(args.base_url, args.api_key)
    except Exception as error:
        output["errors"].append(f"identity_after: {type(error).__name__}: {error}")
    output["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"label": args.label, "output": str(args.output), "errors": output["errors"]}))
    return 0 if not output["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
