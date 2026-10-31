#!/usr/bin/env python3
"""Measure long-decode scaling without storing prompts or generated text.

The probe warms one deterministic prompt, then runs the same request at
several concurrency levels.  It records each request's engine timings and a
hash of its output, so a speculative-decoding candidate can be judged by both
aggregate decode throughput and exact output parity.

Example:
    python3 tools/server/probe-decode-concurrency.py \
      --config /opt/ai/logs/timing-config.json \
      --levels 1,2,4 --waves 3 --max-tokens 512 \
      --output /opt/ai/logs/decode-concurrency.json
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import pathlib
import statistics
import time
import urllib.request


DEFAULT_MODEL = "qwen3.8-flash-next-iq3_xxs-abliterated"
LONG_PROMPT = (
    "Produce a long numbered checklist. For every item write alpha bravo "
    "charlie delta echo foxtrot golf hotel india juliet kilo lima. Continue "
    "numbering without summary or conclusion until the output token limit. "
    "Do not stop early."
)


def api_key(args: argparse.Namespace) -> str:
    if args.config:
        try:
            value = json.loads(pathlib.Path(args.config).read_text(encoding="utf-8")).get("api_key")
            if isinstance(value, str) and value:
                return value
        except (OSError, ValueError):
            pass
    return os.environ.get(args.api_key_env, "")


def request_json(url: str, key: str, body: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode()
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode())


def run_request(url: str, key: str, model: str, max_tokens: int, index: int) -> dict:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a deterministic long-form text generator."},
            {"role": "user", "content": LONG_PROMPT},
        ],
        "max_tokens": max_tokens,
        "temperature": 0,
        "reasoning_effort": "none",
    }
    started = time.monotonic()
    try:
        payload = request_json(url, key, body)
    except Exception as exc:  # noqa: BLE001 - retain the failed run
        return {
            "index": index,
            "wall_s": round(time.monotonic() - started, 6),
            "started_monotonic": started,
            "finished_monotonic": time.monotonic(),
            "error": f"{type(exc).__name__}: {exc}",
        }
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    text = message.get("content") or ""
    usage = payload.get("usage") or {}
    timings = payload.get("timings") or {}
    metrics = payload.get("metrics") or {}
    prompt_ms = timings.get("prompt_ms")
    decode_ms = timings.get("predicted_ms")
    finished = time.monotonic()
    return {
        "index": index,
        "wall_s": round(finished - started, 6),
        "started_monotonic": started,
        "finished_monotonic": finished,
        "prompt_tokens": usage.get("prompt_tokens"),
        "cached_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "prompt_ms": prompt_ms,
        "decode_ms": decode_ms,
        "decode_tok_s": timings.get("predicted_per_second"),
        "ttft_ms": metrics.get("ttft_ms"),
        "slot_wait_ms": metrics.get("slot_wait_ms"),
        "itl_ms_mean": metrics.get("itl_ms_mean"),
        "finish": choice.get("finish_reason"),
        "output_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }


def run_wave(url: str, key: str, model: str, max_tokens: int, level: int) -> list[dict]:
    with concurrent.futures.ThreadPoolExecutor(max_workers=level) as pool:
        futures = [pool.submit(run_request, url, key, model, max_tokens, index) for index in range(level)]
        return [future.result() for future in futures]


def number(row: dict, key: str) -> float | None:
    value = row.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def wave_summary(rows: list[dict]) -> dict:
    good = [row for row in rows if not row.get("error")]
    decode_ms = [number(row, "decode_ms") for row in good]
    decode_ms = [value for value in decode_ms if value and value > 0]
    outputs = [number(row, "completion_tokens") for row in good]
    outputs = [value for value in outputs if value and value > 0]
    starts = [number(row, "started_monotonic") for row in good]
    finishes = [number(row, "finished_monotonic") for row in good]
    starts = [value for value in starts if value is not None]
    finishes = [value for value in finishes if value is not None]
    completion_span_s = max(finishes) - min(starts) if starts and finishes else None
    return {
        "requests": len(rows),
        "errors": len(rows) - len(good),
        # decode timings of concurrent requests overlap.  The longest decode
        # is the conservative observation window; summing them would count the
        # same wall time once per request.
        "decode_wall_s": max(decode_ms) / 1000.0 if decode_ms else None,
        "decode_aggregate_tok_s": sum(outputs) / (max(decode_ms) / 1000.0) if decode_ms and outputs else None,
        "completion_span_s": completion_span_s,
        "completion_aggregate_tok_s":
            sum(outputs) / completion_span_s if completion_span_s and completion_span_s > 0 and outputs else None,
        "wall_s_median": statistics.median([number(row, "wall_s") for row in good]) if good else None,
        "wall_s_max": max((number(row, "wall_s") for row in good if number(row, "wall_s") is not None),
                          default=None),
        "output_tokens": sorted({int(value) for value in outputs}),
        "finishes": sorted({str(row.get("finish")) for row in good}),
        "output_hashes": sorted({str(row.get("output_sha256")) for row in good}),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--config", help="Strata config JSON whose api_key is used when no env key is present")
    parser.add_argument("--api-key-env", default="STRATA_API_KEY")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--levels", default="1,2,4")
    parser.add_argument("--waves", type=int, default=3)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--settle-s", type=float, default=0.5)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    levels = sorted({int(value) for value in args.levels.split(",")})
    if not levels or any(value < 1 or value > 8 for value in levels):
        parser.error("--levels must contain values in 1..8")
    if args.waves < 1 or args.max_tokens < 1:
        parser.error("--waves and --max-tokens must be positive")

    key = api_key(args)
    url = args.base_url.rstrip("/") + "/v1/chat/completions"
    warmup = run_request(url, key, args.model, args.max_tokens, 0)
    if warmup.get("error"):
        print(json.dumps({"error": "warmup: " + warmup["error"]}))
        return 1
    time.sleep(args.settle_s)

    waves = []
    for level in levels:
        for wave in range(args.waves):
            rows = run_wave(url, key, args.model, args.max_tokens, level)
            waves.append({"level": level, "wave": wave, "summary": wave_summary(rows), "requests": rows})
            print(
                f"level={level} wave={wave + 1}/{args.waves} "
                f"errors={sum('error' in row for row in rows)}",
                flush=True,
            )
            time.sleep(args.settle_s)

    by_level = {}
    for level in levels:
        selected = [item["summary"] for item in waves if item["level"] == level]
        spans = [item["completion_span_s"] for item in selected if item["completion_span_s"] is not None]
        completion = [item["completion_aggregate_tok_s"] for item in selected
                      if item["completion_aggregate_tok_s"] is not None]
        by_level[str(level)] = {
            "waves": len(selected),
            "errors": sum(item["errors"] for item in selected),
            "decode_aggregate_tok_s_median": statistics.median(
                item["decode_aggregate_tok_s"] for item in selected
                if item["decode_aggregate_tok_s"] is not None
            ),
            "wall_s_median": statistics.median(
                item["wall_s_median"] for item in selected if item["wall_s_median"] is not None
            ),
            "completion_span_s_median": statistics.median(spans) if spans else None,
            "completion_aggregate_tok_s_median": statistics.median(completion) if completion else None,
        }
    output = {
        "scope": "same warm prompt and max tokens; metadata and output hashes only",
        "levels": levels,
        "waves_per_level": args.waves,
        "max_tokens": args.max_tokens,
        "warmup": warmup,
        "summary": by_level,
        "waves": waves,
    }
    path = pathlib.Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(path), "summary": by_level}, sort_keys=True))
    return 0 if not any(item["summary"]["errors"] for item in waves) else 1


if __name__ == "__main__":
    raise SystemExit(main())
