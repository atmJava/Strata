"""Tests for trace-agent-tasks.py; no Codex runtime, server or model is needed."""
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def _load():
    path = ROOT / "tools" / "server" / "trace-agent-tasks.py"
    spec = importlib.util.spec_from_file_location("trace_agent_tasks", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


trace = _load()


def stamp(value):
    return f"2026-10-31T00:00:{value:06.3f}Z"


def metadata(turn):
    return {"internal_chat_message_metadata_passthrough": {"turn_id": turn}}


def token(when, turn="turn-1", root="root-a", thread="thread-a", response="r1",
          input_tokens=1000, cached=900, output=50, reasoning=20):
    return {
        "timestamp": stamp(when),
        "type": "token_usage_record",
        "payload": {
            "thread_id": thread,
            "root_turn_id": root,
            "turn_id": turn,
            "response_id": response,
            "usage": {
                "input_tokens": input_tokens,
                "cached_input_tokens": cached,
                "cache_write_input_tokens": 0,
                "output_tokens": output,
                "reasoning_output_tokens": reasoning,
            },
        },
    }


def response(when, payload_type, turn="turn-1", **payload):
    return {
        "timestamp": stamp(when),
        "type": "response_item",
        "payload": {
            "type": payload_type,
            **metadata(turn),
            **payload,
        },
    }


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def basic_session(path):
    return write_jsonl(path, [
        {
            "timestamp": stamp(0),
            "type": "event_msg",
            "payload": {
                "type": "task_started",
                "turn_id": "turn-1",
                "root_turn_id": "root-a",
                "started_at": datetime(2026, 10, 31, tzinfo=timezone.utc).timestamp(),
            },
        },
        response(0.1, "message", role="user", content=[{"type": "input_text", "text": "SECRET USER"}]),
        token(2, "turn-1", response="r1"),
        response(2.1, "function_call", call_id="call-1", name="exec_command",
                 arguments='{"cmd":"SECRET COMMAND"}'),
        response(5, "function_call_output", call_id="call-1", output="SECRET TOOL OUTPUT"),
        token(7, "turn-2", response="r2", input_tokens=1200, cached=1000, output=200, reasoning=80),
        {
            "timestamp": stamp(7.5),
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "turn_id": "turn-1",
                "root_turn_id": "root-a",
                "started_at": datetime(2026, 10, 31, tzinfo=timezone.utc).timestamp(),
                "completed_at": datetime.fromtimestamp(
                    datetime(2026, 10, 31, tzinfo=timezone.utc).timestamp() + 7.5,
                    timezone.utc,
                ).timestamp(),
            },
        },
    ])


def test_root_timeline_separates_model_tool_and_gap(tmp_path):
    path = basic_session(tmp_path / "session.jsonl")
    result = trace.summarize(trace.parse([path]))
    assert result["roots"] == 1
    row = result["roots_by_wall"][0]
    assert row["wall_s"] == 7.5
    assert row["model_requests"] == 2
    assert row["tool_calls"] == 1
    assert row["model_union_s"] == 3.9
    assert row["tool_union_s"] == 2.9
    assert row["covered_union_s"] == 6.8
    assert row["unattributed_gap_s"] == 0.7
    assert row["tool_output_chars"] == len("SECRET TOOL OUTPUT")
    assert result["tool_accounting"] == {"calls": 1, "unpaired": 0}
    assert result["tool_by_name"][0]["name"] == "exec_command"
    assert result["model_calls"]["ordinary"]["requests"] == 2


def test_overlapping_tools_use_union_not_sum(tmp_path):
    rows = [
        {
            "timestamp": stamp(0),
            "type": "event_msg",
            "payload": {"type": "task_started", "turn_id": "turn-1", "root_turn_id": "root-a",
                        "started_at": datetime(2026, 10, 31, tzinfo=timezone.utc).timestamp()},
        },
        token(1, "turn-1", response="r1"),
        response(1.1, "function_call", call_id="a", name="tool-a"),
        response(1.2, "function_call", call_id="b", name="tool-b"),
        response(3.1, "function_call_output", call_id="a", output="a"),
        response(4.2, "function_call_output", call_id="b", output="b"),
        token(5, "turn-2", response="r2"),
        {
            "timestamp": stamp(5),
            "type": "event_msg",
            "payload": {"type": "task_complete", "turn_id": "turn-1", "root_turn_id": "root-a",
                        "started_at": datetime(2026, 10, 31, tzinfo=timezone.utc).timestamp(),
                        "completed_at": datetime.fromtimestamp(
                            datetime(2026, 10, 31, tzinfo=timezone.utc).timestamp() + 5,
                            timezone.utc,
                        ).timestamp()},
        },
    ]
    path = write_jsonl(tmp_path / "overlap.jsonl", rows)
    result = trace.summarize(trace.parse([path]))
    row = result["roots_by_wall"][0]
    assert row["tool_sum_s"] == 5.0
    assert row["tool_union_s"] == 3.1


def test_custom_and_tool_search_calls_are_counted(tmp_path):
    rows = [
        {
            "timestamp": stamp(0),
            "type": "event_msg",
            "payload": {"type": "task_started", "turn_id": "turn-1", "root_turn_id": "root-a",
                        "started_at": datetime(2026, 10, 31, tzinfo=timezone.utc).timestamp()},
        },
        response(1, "custom_tool_call", call_id="patch", name="apply_patch",
                 input="SECRET PATCH"),
        response(1.5, "custom_tool_call_output", call_id="patch", output="SECRET PATCH OUTPUT"),
        response(2, "tool_search_call", call_id="search",
                 arguments='{"query":"SECRET SEARCH"}'),
        response(2.25, "tool_search_output", call_id="search",
                 tools=[{"name": "SECRET TOOL"}]),
        token(3, "turn-1", response="r1"),
        {
            "timestamp": stamp(3),
            "type": "event_msg",
            "payload": {"type": "task_complete", "turn_id": "turn-1", "root_turn_id": "root-a",
                        "started_at": datetime(2026, 10, 31, tzinfo=timezone.utc).timestamp(),
                        "completed_at": datetime.fromtimestamp(
                            datetime(2026, 10, 31, tzinfo=timezone.utc).timestamp() + 3,
                            timezone.utc,
                        ).timestamp()},
        },
    ]
    path = write_jsonl(tmp_path / "all-tools.jsonl", rows)
    result = trace.summarize(trace.parse([path]))
    row = result["roots_by_wall"][0]
    assert row["tool_calls"] == 2
    assert row["tool_sum_s"] == 0.75
    assert row["tool_union_s"] == 0.75
    assert result["tool_accounting"] == {"calls": 2, "unpaired": 0}
    assert {entry["name"] for entry in result["tool_by_name"]} == {"apply_patch", "tool_search"}
    rendered = json.dumps(result, ensure_ascii=False)
    for secret in ("SECRET PATCH", "SECRET SEARCH", "SECRET TOOL"):
        assert secret not in rendered


def test_compaction_and_abort_are_counted_without_text(tmp_path):
    rows = [
        {
            "timestamp": stamp(0),
            "type": "event_msg",
            "payload": {"type": "task_started", "turn_id": "turn-1", "root_turn_id": "root-a",
                        "started_at": datetime(2026, 10, 31, tzinfo=timezone.utc).timestamp()},
        },
        token(2, "turn-1", response="compact"),
        {
            "timestamp": stamp(2.1),
            "type": "compacted",
            "payload": {
                "message": "NEVER PRINT THIS PROMPT",
                "latest_token_usage_record": {
                    "thread_id": "thread-a", "root_turn_id": "root-a", "response_id": "compact",
                },
            },
        },
        {
            "timestamp": stamp(2.2),
            "type": "event_msg",
            "payload": {"type": "turn_aborted", "turn_id": "turn-1", "root_turn_id": "root-a",
                        "reason": "interrupted",
                        "started_at": datetime(2026, 10, 31, tzinfo=timezone.utc).timestamp(),
                        "completed_at": datetime.fromtimestamp(
                            datetime(2026, 10, 31, tzinfo=timezone.utc).timestamp() + 2.2,
                            timezone.utc,
                        ).timestamp()},
        },
    ]
    path = write_jsonl(tmp_path / "abort.jsonl", rows)
    result = trace.summarize(trace.parse([path]))
    row = result["roots_by_wall"][0]
    assert row["status"] == "aborted"
    assert row["aborts"] == 1
    assert row["compactions"] == 1
    assert result["model_calls"]["compaction"]["requests"] == 1
    assert result["model_calls"]["compaction"]["reuse_50pct_requests"] == 1
    assert result["model_calls"]["compaction"]["miss_50pct_requests"] == 0
    rendered = json.dumps(result, ensure_ascii=False)
    for secret in ("NEVER PRINT THIS PROMPT", "SECRET USER", "SECRET COMMAND", "SECRET TOOL OUTPUT"):
        assert secret not in rendered


def test_optional_server_metrics_match_one_to_one(tmp_path):
    path = basic_session(tmp_path / "session.jsonl")
    day = datetime(2026, 10, 31, tzinfo=timezone.utc).timestamp()
    metrics = {
        "requests": [
            {"time": day, "duration_s": 2, "prompt_ms": 900, "decode_ms": 100,
             "prompt_total": 1000, "reused": 900, "finish": "tool_calls"},
            {"time": day + 5, "duration_s": 2, "prompt_ms": 400, "decode_ms": 1600,
             "prompt_total": 1200, "reused": 1000, "finish": "stop"},
        ],
    }
    parsed = trace.parse([path])
    result = trace.summarize(parsed, metrics)
    assert result["server_matched"] == 2
    assert result["server_unmatched"] == 0
    assert result["roots_by_wall"][0]["server_matched"] == 2
    assert result["server_timing"]["prompt_ms_sum"] == 1300
    assert result["server_timing"]["decode_ms_sum"] == 1700


def test_directory_scan_and_cli_output(tmp_path, capsys):
    basic_session(tmp_path / "nested" / "one.jsonl")
    output = tmp_path / "timeline.json"
    assert trace.main([str(tmp_path), "--output", str(output), "--top", "0"]) == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["files"] == 1
    assert result["roots"] == 1
    assert result["top_tool_calls"] == []
    assert "not Strata server timing" in result["scope"]
    summary = json.loads(capsys.readouterr().out)
    assert summary["roots"] == 1
