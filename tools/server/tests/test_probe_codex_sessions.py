"""Tests for probe-codex-sessions.py; no Codex runtime or model is needed."""
import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def _load():
    path = ROOT / "tools" / "server" / "probe-codex-sessions.py"
    spec = importlib.util.spec_from_file_location("probe_codex_sessions", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load()


def usage(input_tokens, cached, output=10, reasoning=2):
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "cache_write_input_tokens": 0,
        "output_tokens": output,
        "reasoning_output_tokens": reasoning,
    }


def record(timestamp, input_tokens, cached, root="root-a", response="r"):
    return {
        "timestamp": timestamp,
        "type": "token_usage_record",
        "payload": {
            "thread_id": "thread-a",
            "root_turn_id": root,
            "turn_id": root,
            "response_id": response,
            "usage": usage(input_tokens, cached),
        },
    }


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def test_totals_roots_and_compaction_association(tmp_path):
    path = write_jsonl(tmp_path / "session.jsonl", [
        record("2026-10-31T00:00:00Z", 300_000, 299_000, response="turn"),
        record("2026-10-31T00:01:00Z", 290_000, 7_000, response="compact"),
        {"timestamp": "2026-10-31T00:01:00.001Z", "type": "compacted",
         "payload": {"message": "SECRET PROMPT TEXT"}},
        record("2026-10-31T00:02:00Z", 14_000, 12_000, response="after"),
    ])
    records, compactions, notes = probe.parse([path])
    result = probe.summarize(records, compactions, notes)
    assert result["requests"] == 3
    assert result["roots"] == 1
    assert result["totals"]["input"] == 604_000
    assert result["totals"]["cached"] == 318_000
    assert result["totals"]["uncached"] == 286_000
    assert result["compaction"]["events"] == 1
    assert result["compaction"]["associated"] == 1
    assert result["compaction"]["uncached_input"] == 283_000
    row = result["compaction"]["rows"][0]
    assert row["record"]["response"] == "compact"
    assert row["prior"]["response"] == "turn"
    assert row["prior_uncached"] == 1_000


def test_malformed_lines_are_notes_and_nested_cached_name_is_read(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text("not json\n" + json.dumps({
        "timestamp": "2026-10-31T00:00:00Z",
        "type": "token_usage_record",
        "payload": {"thread_id": "t", "turn_id": "root", "usage": {
            "input_tokens": 100, "cached_tokens": 80, "output_tokens": 5,
        }},
    }) + "\n", encoding="utf-8")
    records, compactions, notes = probe.parse([path])
    assert len(records) == 1
    assert records[0]["cached"] == 80
    assert compactions == []
    assert any("invalid JSON" in note for note in notes)


def test_prompt_text_is_never_copied_into_the_summary(tmp_path):
    path = write_jsonl(tmp_path / "session.jsonl", [
        record("2026-10-31T00:00:00Z", 100, 90),
        {"timestamp": "2026-10-31T00:00:01Z", "type": "compacted",
         "payload": {"message": "NEVER PRINT THIS PROMPT"}},
    ])
    records, compactions, notes = probe.parse([path])
    rendered = json.dumps(probe.summarize(records, compactions, notes), ensure_ascii=False)
    assert "NEVER PRINT THIS PROMPT" not in rendered


def test_directory_scan_and_cli_output(tmp_path, capsys):
    write_jsonl(tmp_path / "nested" / "one.jsonl",
                [record("2026-10-31T00:00:00Z", 120, 100)])
    output = tmp_path / "summary.json"
    assert probe.main([str(tmp_path), "--output", str(output), "--top", "0"]) == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["requests"] == 1
    assert result["top_uncached"] == []
    assert "Codex client token records" in result["scope"]
    assert json.loads(capsys.readouterr().out)["requests"] == 1
