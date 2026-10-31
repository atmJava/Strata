import importlib.util
import json
import pathlib
import sys


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "probe-dsh-session-cost.py"
SPEC = importlib.util.spec_from_file_location("probe_dsh_session_cost", SCRIPT)
PROBE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = PROBE
SPEC.loader.exec_module(PROBE)


def test_interval_union_does_not_double_count_parallel_tools():
    assert PROBE.interval_union([(1, 4), (2, 3), (5, 7)]) == 5


def test_summary_separates_model_tool_and_client_time(tmp_path):
    session = tmp_path / "task" / "session-example" / "session.v4.jsonl.zstd"
    session.parent.mkdir(parents=True)
    rows = [
        {"type": "session", "version": 4, "id": "session-example"},
        {"type": "turn/start", "time": 1000, "data": {"turn": 1}},
        {"type": "step/start", "time": 1010, "data": {"turn": 1, "step": 1}},
        {
            "type": "assistant/message",
            "time": 2100,
            "data": {
                "turn": 1,
                "step": 1,
                "usage": {"inputTokens": 100, "cacheReadTokens": 900, "outputTokens": 20},
                "message": {
                    "stream": [
                        {"time": 1300, "chunk": {"type": "block-start"}},
                        {"time": 2100, "chunk": {"type": "finish"}},
                    ]
                },
            },
        },
        {"type": "step/end", "time": 2100, "data": {"turn": 1, "step": 1}},
        {"type": "tool/call", "time": 2110, "data": {"callId": "a", "name": "read"}},
        {"type": "tool/call", "time": 2110, "data": {"callId": "b", "name": "read"}},
        {
            "type": "tool/result",
            "time": 2310,
            "data": {"message": {"toolCallId": "a"}},
        },
        {
            "type": "tool/result",
            "time": 2360,
            "data": {"message": {"toolCallId": "b"}},
        },
        {"type": "step/start", "time": 2400, "data": {"turn": 1, "step": 2}},
        {
            "type": "assistant/message",
            "time": 3000,
            "data": {
                "turn": 1,
                "step": 2,
                "usage": {"inputTokens": 50, "cacheReadTokens": 1200, "outputTokens": 10},
                "message": {
                    "stream": [
                        {"time": 2600, "chunk": {"type": "block-start"}},
                        {"time": 3000, "chunk": {"type": "finish"}},
                    ]
                },
            },
        },
        {"type": "step/end", "time": 3000, "data": {"turn": 1, "step": 2}},
        {"type": "turn/end", "time": 3200, "data": {"turn": 1}},
    ]
    text = "\n".join(json.dumps(row) for row in rows) + "\n"
    plain = session.with_suffix("")
    plain.write_text(text, encoding="utf-8")

    result = PROBE.summarize_session(plain)

    assert result["client_wall_s"] == 2.2
    assert result["task_wall_s"] == 2.2
    assert result["turn_union_s"] == 2.2
    assert result["post_turn_s"] == 0.0
    assert result["model_wall_s"] == 1.69
    assert result["tool_union_s"] == 0.25
    assert result["tool_sum_s"] == 0.45
    assert result["tokens"] == {
        "fresh_input": 150,
        "cached_input": 2100,
        "context_input": 2250,
        "output": 30,
        "cache_reuse_pct": 93.333,
    }
    assert result["step_rows"][0]["time_to_first_stream_s"] == 0.29
    assert result["step_rows"][1]["stream_after_first_s"] == 0.4
    assert result["tool_calls"] == {"read": {"calls": 2, "wall_s": 0.45}}


def test_task_wall_excludes_events_after_the_final_turn(tmp_path):
    session = tmp_path / "task" / "session-example"
    session.mkdir(parents=True)
    plain = session / "session.jsonl"
    rows = [
        {"type": "session", "time": 1000},
        {"type": "turn/start", "time": 1100, "data": {"turn": 1}},
        {"type": "step/start", "time": 1110, "data": {"turn": 1, "step": 1}},
        {
            "type": "assistant/message",
            "time": 2110,
            "data": {
                "usage": {"inputTokens": 5, "cacheReadTokens": 0, "outputTokens": 2},
                "message": {"stream": [{"time": 1600, "chunk": {}}]},
            },
        },
        {"type": "step/end", "time": 2110, "data": {"turn": 1, "step": 1}},
        {"type": "turn/end", "time": 2200, "data": {"turn": 1}},
        {"type": "session/title", "time": 2600, "data": {}},
    ]
    plain.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    result = PROBE.summarize_session(plain)

    assert result["client_wall_s"] == 1.6
    assert result["task_wall_s"] == 1.1
    assert result["turn_union_s"] == 1.1
    assert result["post_turn_s"] == 0.4


def test_server_join_uses_exact_tokens_and_reports_hidden_request(tmp_path):
    session = tmp_path / "task" / "session-example"
    session.mkdir(parents=True)
    plain = session / "session.jsonl"
    rows = [
        {"type": "turn/start", "time": 1000, "data": {"turn": 1}},
        {"type": "step/start", "time": 1010, "data": {"turn": 1, "step": 1}},
        {
            "type": "assistant/message",
            "time": 2000,
            "data": {
                "usage": {"inputTokens": 100, "cacheReadTokens": 900, "outputTokens": 20},
                "message": {"stream": [{"time": 1500, "chunk": {}}]},
            },
        },
        {"type": "step/end", "time": 2000, "data": {"turn": 1, "step": 1}},
        {"type": "turn/end", "time": 2000, "data": {"turn": 1}},
    ]
    plain.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    requests = [
        {
            "id": "main",
            "time": 1.0101,
            "duration_s": 1.0,
            "prompt_tokens": 1000,
            "output_tokens": 20,
            "prompt_ms": 500,
            "decode_ms": 450,
            "finish": "stop",
        },
        {
            "id": "title",
            "time": 1.0102,
            "duration_s": 0.8,
            "prompt_tokens": 300,
            "output_tokens": 10,
            "prompt_ms": 200,
            "decode_ms": 500,
            "finish": "stop",
        },
    ]

    result = PROBE.summarize_session(plain, requests)

    assert result["server"]["matched_steps"] == 1
    assert result["server"]["prompt_ms_sum"] == 500
    assert result["server"]["decode_ms_sum"] == 450
    assert len(result["server"]["unmatched"]) == 1
    assert result["server"]["unmatched"][0]["prompt_tokens"] == 300


def test_cli_emits_only_metadata(tmp_path, capsys):
    session = tmp_path / "session.jsonl"
    session.write_text(
        "\n".join(
            [
                '{"type":"turn/start","time":1000,"data":{}}',
                '{"type":"step/start","time":1010,"data":{"step":1}}',
                '{"type":"assistant/message","time":1510,"data":{"usage":{"inputTokens":5,'
                '"cacheReadTokens":0,"outputTokens":2},"message":{"stream":[{"time":1100,"chunk":{}}]}}}',
                '{"type":"step/end","time":1510,"data":{"step":1}}',
                '{"type":"turn/end","time":1600,"data":{}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "report.json"

    assert PROBE.main([str(session), "--output", str(output)]) == 0

    printed = json.loads(capsys.readouterr().out)
    assert printed["summary"]["steps"] == 1
    assert printed["sessions"][0]["tokens"]["fresh_input"] == 5
    assert "prompt" not in printed["sessions"][0]
    assert output.is_file()


def test_cli_skips_a_failed_session_with_no_model_steps(tmp_path, capsys):
    session = tmp_path / "session.jsonl"
    session.write_text(
        "\n".join(
            [
                '{"type":"turn/start","time":1000,"data":{}}',
                '{"type":"turn/end","time":1100,"data":{}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert PROBE.main([str(session)]) == 0

    printed = json.loads(capsys.readouterr().out)
    assert printed["summary"]["sessions"] == 0
    assert printed["sessions"] == []
    assert printed["skipped_empty_sessions"][0]["client_wall_s"] == 0.1
