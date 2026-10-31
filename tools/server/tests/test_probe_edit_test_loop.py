"""Offline tests for privacy-safe edit/test loop analysis."""
import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def _load():
    path = ROOT / "tools" / "server" / "probe-edit-test-loop.py"
    spec = importlib.util.spec_from_file_location("probe_edit_test_loop", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load()


def stamp(value):
    return f"2026-10-31T00:00:{value:06.3f}Z"


def metadata(turn="turn-1"):
    return {"internal_chat_message_metadata_passthrough": {"turn_id": turn}}


def token(when, response):
    return {
        "timestamp": stamp(when),
        "type": "token_usage_record",
        "payload": {
            "thread_id": "thread-a",
            "root_turn_id": "root-a",
            "turn_id": "turn-1",
            "response_id": response,
            "usage": {
                "input_tokens": 1000,
                "cached_input_tokens": 900,
                "output_tokens": 20,
                "reasoning_output_tokens": 0,
            },
        },
    }


def call(when, call_id, name, value):
    return {
        "timestamp": stamp(when),
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "call_id": call_id,
            "name": name,
            "arguments": json.dumps(value),
            **metadata(),
        },
    }


def output(when, call_id, value):
    return {
        "timestamp": stamp(when),
        "type": "response_item",
        "payload": {
            "type": "function_call_output",
            "call_id": call_id,
            "output": value,
            **metadata(),
        },
    }


def test_command_classification_is_conservative():
    assert probe.trace.command_kind("exec_command", "pytest -q tests") == "test"
    assert probe.trace.command_kind("exec_command", "npm run test:unit") == "test"
    assert probe.trace.command_kind("exec_command", "cargo test --all") == "test"
    assert probe.trace.command_kind("exec_command", "rg pytest docs") == "read"
    assert probe.trace.command_kind("exec_command", "sed -i s/a/b/ file") == "edit"
    assert probe.trace.command_kind("apply_patch", "") == "edit"


def test_edit_test_loop_counts_repeat_and_failure_without_text(tmp_path):
    path = tmp_path / "session.jsonl"
    rows = [
        {
            "timestamp": stamp(0),
            "type": "event_msg",
            "payload": {
                "type": "task_started",
                "turn_id": "turn-1",
                "root_turn_id": "root-a",
            },
        },
        token(1.0, "before"),
        call(1.1, "edit", "apply_patch", {"input": "SECRET PATCH"}),
        output(1.2, "edit", "SECRET PATCH OUTPUT"),
        token(2.0, "after-edit"),
        call(2.1, "test-1", "exec_command", {"cmd": "pytest -q SECRET_TEST_PATH"}),
        output(3.0, "test-1", "SECRET FAILURE\nFAILED test_x\nProcess exited with code 1"),
        token(4.0, "after-failure"),
        call(4.1, "test-2", "exec_command", {"cmd": "pytest -q SECRET_TEST_PATH"}),
        output(5.0, "test-2", "Process exited with code 0"),
        token(6.0, "after-success"),
        {
            "timestamp": stamp(6),
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "turn_id": "turn-1",
                "root_turn_id": "root-a",
            },
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    parsed = probe.trace.parse([path])
    parsed["files"] = 1
    result = probe.analyze(parsed)

    assert result["coverage"]["roots_with_edit_and_test"] == 1
    assert result["edits"] == 1
    assert result["tests"]["calls"] == 2
    assert result["tests"]["failed"] == 1
    assert result["tests"]["succeeded"] == 1
    assert result["tests"]["exact_repeats"] == 1
    assert result["tests"]["exact_repeats_after_failure"] == 1
    assert result["tests"]["exact_repeats_after_failure_without_edit"] == 1
    assert result["tests"]["exact_repeats_after_edit"] == 0
    assert result["post_failure_unique_model_requests"] == 1
    assert result["model_after_test"]["after_failure"]["requests"] == 1
    assert result["model_after_test"]["after_success"]["requests"] == 1
    rendered = json.dumps(result, ensure_ascii=False)
    for secret in ("SECRET PATCH", "SECRET TEST", "SECRET FAILURE"):
        assert secret not in rendered


def test_repeat_after_another_edit_is_not_counted_as_waste(tmp_path):
    path = tmp_path / "session.jsonl"
    rows = [
        {
            "timestamp": stamp(0),
            "type": "event_msg",
            "payload": {
                "type": "task_started",
                "turn_id": "turn-1",
                "root_turn_id": "root-a",
            },
        },
        token(1.0, "before"),
        call(1.1, "edit-1", "apply_patch", {"input": "FIRST"}),
        output(1.2, "edit-1", "ok"),
        call(2.0, "test-1", "exec_command", {"cmd": "pytest -q same"}),
        output(3.0, "test-1", "Process exited with code 0"),
        call(4.0, "edit-2", "apply_patch", {"input": "SECOND"}),
        output(4.1, "edit-2", "ok"),
        call(5.0, "test-2", "exec_command", {"cmd": "pytest -q same"}),
        output(6.0, "test-2", "Process exited with code 0"),
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    parsed = probe.trace.parse([path])
    parsed["files"] = 1
    result = probe.analyze(parsed)

    assert result["tests"]["exact_repeats"] == 1
    assert result["tests"]["exact_repeats_after_edit"] == 1
    assert result["tests"]["exact_repeats_without_edit"] == 0
    assert result["tests"]["exact_repeats_after_success_without_edit"] == 0


def test_partial_records_stay_explicit(tmp_path):
    path = tmp_path / "partial.jsonl"
    path.write_text(json.dumps(token(1, "orphan")) + "\n", encoding="utf-8")
    parsed = probe.trace.parse([path])
    parsed["files"] = 1
    result = probe.analyze(parsed)
    assert result["roots"] == 1
    assert result["coverage"]["roots_with_tools"] == 0
    assert result["coverage"]["roots_with_edit"] == 0
    assert result["coverage"]["roots_with_edit_and_test"] == 0
    assert result["tests"]["calls"] == 0
    assert result["first_edit"]["samples"] == 0
    assert result["roots_by_loop_cost"][0]["post_test_model"] == {}
