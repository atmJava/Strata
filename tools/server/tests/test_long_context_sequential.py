"""Tests for the fixed-prompt long-context sequential runner."""
from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "tools" / "server" / "long-context-sequential.py"
SPEC = importlib.util.spec_from_file_location("long_context_sequential", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeSampler:
    samples = [
        {
            "engine_rss_mib": 100,
            "memory": {"MemAvailable_MiB": 2000, "SwapFree_MiB": 4090},
            "gpus": [{"memory_mib": 1000}, {"memory_mib": 1010}],
        },
        {
            "engine_rss_mib": 120,
            "memory": {"MemAvailable_MiB": 1500, "SwapFree_MiB": 4089},
            "gpus": [{"memory_mib": 1100}, {"memory_mib": 1110}],
        },
    ]


def test_median_reads_nested_values() -> None:
    rows = [
        {"timings": {"prompt_ms": 30}},
        {"timings": {"prompt_ms": 10}},
        {"timings": {"prompt_ms": 20}},
    ]
    assert MODULE.median(rows, ("timings", "prompt_ms")) == 20
    assert MODULE.median(rows, ("timings", "missing")) is None


def test_summary_counts_failures_and_resources() -> None:
    rows = [
        {
            "status": "ok",
            "finish": "stop",
            "answer_ok": True,
            "tokens_prompt": 98353,
            "timings": {"prompt_ms": 40_000, "prompt_per_second": 2458.8},
            "wall_s": 40.1,
            "metrics": {"wall_ms": 40_200, "ttft_ms": 40_000},
        },
        {
            "status": "error",
            "finish": "length",
            "answer_ok": False,
            "tokens_prompt": 98350,
            "timings": {"prompt_ms": 41_000, "prompt_per_second": 2398.8},
            "wall_s": 41.1,
            "metrics": {"wall_ms": 41_200, "ttft_ms": 41_000},
        },
    ]
    result = MODULE.summary({"arm": "test"}, rows, FakeSampler(), 82.5)
    assert result["requests"] == 2
    assert result["errors"] == 1
    assert result["needle_failures"] == 1
    assert result["finish_length_count"] == 1
    assert result["engine_prompt_ms_median"] == 40_500
    assert result["resources"]["mem_available_mib_min"] == 1500
    assert result["resources"]["swap_free_mib_min"] == 4089
    assert result["resources"]["gpu_memory_mib_max"] == [1100, 1110]
