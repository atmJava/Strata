"""Tests for probe-context-diet-yield.py; no Codex runtime or model is needed."""
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def _load():
    path = ROOT / "tools" / "server" / "probe-context-diet-yield.py"
    spec = importlib.util.spec_from_file_location("probe_context_diet_yield", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


yield_probe = _load()


def stamp(value):
    return f"2026-10-31T00:00:{value:06.3f}Z"


def token(when, thread="thread-a", turn="turn-a", input_tokens=1000, output=20):
    return {
        "timestamp": stamp(when),
        "type": "token_usage_record",
        "payload": {
            "thread_id": thread,
            "turn_id": turn,
            "usage": {
                "input_tokens": input_tokens,
                "cached_input_tokens": input_tokens // 2,
                "output_tokens": output,
            },
        },
    }


def output(when, call_id, text, kind="function_call_output", item_id=None, turn="turn-a"):
    return {
        "timestamp": stamp(when),
        "type": "response_item",
        "payload": {
            "type": kind,
            "id": item_id,
            "call_id": call_id,
            "output": text,
            "internal_chat_message_metadata_passthrough": {"turn_id": turn},
        },
    }


def write_jsonl(path, rows):
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_foldable_exposure_stops_at_compaction_and_retains_errors(tmp_path):
    artifact = tmp_path / "artifacts"
    source = tmp_path / "session.jsonl"
    rows = [
        token(1, input_tokens=1000),
        output(2, "large", "a" * 5000, item_id="large-id"),
        {
            "timestamp": stamp(2),
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "thread_id": "thread-a",
                "item": {"id": "large-id"},
            },
        },
        token(3, input_tokens=6000),
        token(4, input_tokens=7000),
        {
            "timestamp": stamp(5),
            "type": "compacted",
            "payload": {"latest_token_usage_record": {"thread_id": "thread-a"}},
        },
        token(6, input_tokens=2000),
        output(7, "failed", "ERROR: build failed\n" + "b" * 5000),
        token(8, input_tokens=2500),
        output(9, "small", "ok"),
        output(
            10,
            "search",
            [{"name": "SECRET TOOL NAME"}],
            kind="tool_search_output",
        ),
    ]
    write_jsonl(source, rows)

    result = yield_probe.summarize(
        yield_probe.parse([source], artifact),
        4000,
        artifact,
    )

    assert result["requests"] == 5
    assert result["tool_outputs"] == 4
    assert result["large_outputs"] == 2
    assert result["foldable_outputs"] == 1
    assert result["visible_foldable_requests"] == 2
    assert result["sessions_with_foldable_output"] == 1
    assert result["sessions_at_or_above_1pct"] == 1
    assert result["estimated_saved_chars"] > 8000
    assert result["exposure_histogram"]["2-4"] == 1
    assert result["by_output_type"]["function_call_output"]["retained_large"] == 1
    assert result["by_output_type"]["tool_search_output"]["foldable"] == 0

    rendered = json.dumps(result)
    assert "SECRET TOOL NAME" not in rendered
    assert "build failed" not in rendered
    assert "aaaaaaaa" not in rendered


def test_unmapped_output_is_reported_without_guessing(tmp_path):
    source = tmp_path / "session.jsonl"
    write_jsonl(
        source,
        [
            token(1),
            output(2, "large", "x" * 5000, turn="unmapped-turn"),
            token(3, input_tokens=6000),
        ],
    )
    result = yield_probe.summarize(
        yield_probe.parse([source], tmp_path / "artifacts"),
        4000,
        tmp_path / "artifacts",
    )
    assert result["foldable_outputs"] == 1
    assert result["visible_foldable_requests"] == 0
    assert result["exposure_histogram"]["unmapped"] == 1


def test_cli_writes_aggregate_json_without_tool_text(tmp_path):
    source = tmp_path / "session.jsonl"
    write_jsonl(
        source,
        [
            token(1),
            output(2, "large", "PRIVATE-OUTPUT " * 1000),
            token(3, input_tokens=6000),
        ],
    )
    target = tmp_path / "yield.json"
    assert yield_probe.main(
        [
            str(source),
            "--artifact-root",
            str(tmp_path / "artifacts"),
            "--output",
            str(target),
        ]
    ) == 0
    text = target.read_text(encoding="utf-8")
    result = json.loads(text)
    assert result["scope"].startswith("stored Codex client events")
    assert "PRIVATE-OUTPUT" not in text
