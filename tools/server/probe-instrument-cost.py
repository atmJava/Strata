#!/usr/bin/env python3
"""probe-instrument-cost.py - V0-4: what does the per-request timing probe itself cost?

`serve/server.py` already answers with `done["metrics"]` (queue/ttft/itl/wall) and production's copy
has the same block.  Before using those numbers as evidence (`RULES.md` §5: measure the instrument
first), this times the probe's own work on this machine: three `time.time()` calls and the 5-field
dict, the exact statements the server runs per request.  It is a **local micro-benchmark**, not an
end-to-end A/B: it prices the arithmetic, not the scheduling around it, and it says so in the output.

Usage: probe-instrument-cost.py [--iterations 200000]
Exit: always 0 (it reports; it does not judge).
"""
from __future__ import annotations

import argparse
import json
import statistics
import time


def s2_block(started: float, admitted: float, first_at: float, now: float, tokens: int) -> dict:
    """The statements in serve/server.py's `done["metrics"]` block (kept in the same shape)."""
    return {
        "queue_ms": int(round((admitted - started) * 1000)),
        "ttft_ms": int(round((first_at - started) * 1000)) if first_at else None,
        "itl_ms_mean": round((now - first_at) * 1000 / (tokens - 1), 1) if first_at and tokens > 1 else None,
        "wall_ms": int(round((now - started) * 1000)),
        "tokens": tokens,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=200_000)
    options = parser.parse_args(argv)

    samples = []
    for _ in range(5):
        began = time.perf_counter()
        for _ in range(options.iterations):
            started = time.time()
            admitted = time.time()
            first_at = time.time()
            now = time.time()
            s2_block(started, admitted, first_at, now, 32)
        samples.append((time.perf_counter() - began) / options.iterations)

    median_s = statistics.median(samples)              # seconds per request
    spread = (max(samples) - min(samples)) / median_s * 100 if median_s else 0.0
    median = median_s * 1e6                            # microseconds per request
    request = 1.0                                        # a 1 s request is the small end here
    token_ms = 33.3                                      # 30 tok/s decode: one token's budget
    print(json.dumps({
        "iterations_per_sample": options.iterations,
        "samples_us": [round(s * 1e6, 3) for s in samples],
        "median_us_per_request": round(median, 3),
        "spread_pct": round(spread, 1),
        "share_of_1s_request_pct": round(median / (request * 1e6) * 100, 6),
        "share_of_one_token_pct": round(median / (token_ms * 1000) * 100, 5),
        "not_measured": "scheduler/IO around the probe, engine-side STRATA_*_TIMING, btrace writes",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))
