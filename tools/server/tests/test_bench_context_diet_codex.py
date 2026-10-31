"""Unit tests for bench-context-diet-codex.py; no model is called."""
import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def _load():
    path = ROOT / "tools" / "server" / "bench-context-diet-codex.py"
    spec = importlib.util.spec_from_file_location("bench_context_diet_codex", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bench = _load()


def test_build_task_initial_state_fails_and_report_is_large(tmp_path):
    bench.build_task("simple", tmp_path / "fixture")
    report = (tmp_path / "fixture" / "tools" / "report.py").read_text()
    assert bench.REPORT_MARKER in report
    assert len(report) >= 50_000
    passed, _ = bench.expected_pass(tmp_path / "fixture")
    assert not passed


def test_proxy_summary_reads_only_new_bytes(tmp_path):
    path = tmp_path / "proxy.jsonl"
    first = {
        "response": {
            "http_status": 200,
            "duration_s": 1.5,
            "usage": {
                "input_tokens": 100,
                "cached_tokens": 80,
                "output_tokens": 5,
            },
        }
    }
    path.write_text(json.dumps(first) + "\n")
    offset = bench.proxy_log_offset(path)
    second = {
        "response": {
            "http_status": 500,
            "duration_s": 2.25,
            "usage": {
                "input_tokens": 200,
                "cached_tokens": 150,
                "output_tokens": 7,
            },
        },
        "context_diet": {
            "candidates": 5,
            "replaced": 3,
            "saved_chars": 9000,
        },
    }
    with path.open("a") as handle:
        handle.write(json.dumps(second) + "\n")
    result = bench.read_proxy_summary(path, offset)
    assert result["requests"] == 1
    assert result["http_errors"] == 1
    assert result["input_tokens"] == 200
    assert result["diet_saved_chars"] == 9000


def test_artifact_summary_checks_marker_and_mode(tmp_path):
    artifact = tmp_path / "artifact.txt"
    artifact.write_text("x" * 5000 + bench.REPORT_MARKER + "\n")
    artifact.chmod(0o600)
    result = bench.artifact_summary(tmp_path, "simple")
    assert result["files"] == 1
    assert result["min_mode"] == "600"
    assert result["marker_recovered"] is True
    assert result["marker_files"] == 1
