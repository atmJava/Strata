import importlib.util
import json
import pathlib
import sys


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "bench-agent-quality.py"
SPEC = importlib.util.spec_from_file_location("bench_agent_quality", SCRIPT)
BENCH = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = BENCH
SPEC.loader.exec_module(BENCH)


def test_every_fixture_starts_failing_and_protects_its_tests(tmp_path):
    for name, task in BENCH.TASKS.items():
        root = tmp_path / name
        hashes = BENCH.build_fixture(task, root)
        assert set(hashes) == set(task.protected_files)
        assert (root / ".git").is_dir()
        assert BENCH.subprocess.run(
            [BENCH.sys.executable, "-m", "pytest", "-q"],
            cwd=root,
            stdout=BENCH.subprocess.DEVNULL,
            stderr=BENCH.subprocess.DEVNULL,
            check=False,
        ).returncode != 0


def test_event_summary_counts_tools_without_exposing_bodies(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text(
        "\n".join(
            [
                '{"type":"item.completed","item":{"type":"command_execution","name":"exec_command"}}',
                '{"type":"item.completed","item":{"type":"file_change"}}',
                '{"type":"item.completed","item":{"type":"error","message":"Model metadata for `m` not found. Defaulting to fallback metadata; this can degrade performance and cause issues."}}',
                '{"type":"item.completed","item":{"type":"error","message":"Codex is ignoring 1 unrecognized configuration setting."}}',
                '{"type":"item.completed","item":{"type":"agent_message","text":"RESULT=PASS"}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    summary = BENCH.event_summary(events)
    assert summary["tool_calls"] == {"apply_patch": 1, "exec_command": 1}
    assert summary["final"] == "RESULT=PASS"
    assert set(summary) == {
        "events",
        "tool_calls",
        "tool_call_count",
        "tool_errors",
        "event_errors",
        "event_warnings",
        "final",
    }
    assert summary["event_errors"] == 0
    assert summary["tool_errors"] == 0
    assert summary["event_warnings"] == 2


def test_dsh_event_summary_counts_calls_errors_and_final(tmp_path):
    events = tmp_path / "dsh-events.jsonl"
    events.write_text(
        "\n".join(
            [
                '{"type":"tool_call","tool":"bash","input":{"command":"pytest"}}',
                '{"type":"tool_result","status":"completed","result":"ok"}',
                '{"type":"tool_result","status":"error","result":"recoverable"}',
                '{"type":"status","phase":"turn_end","reason":{"kind":"completed"}}',
                '{"type":"final","text":"RESULT=PASS"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    summary = BENCH.event_summary(events, "dsh")
    assert summary["tool_calls"] == {"bash": 1}
    assert summary["tool_call_count"] == 1
    assert summary["tool_errors"] == 1
    assert summary["event_errors"] == 0
    assert summary["final"] == "RESULT=PASS"


def test_every_task_has_a_machine_checkable_prompt():
    for task in BENCH.TASKS.values():
        assert "RESULT=PASS" in task.prompt
        assert "without changing tests" in task.prompt or "not the tests" in task.prompt
        assert task.expected_files
        assert task.protected_files
        assert "does not declare apply_patch" not in task.prompt
    assert BENCH.TASKS["apply-patch"].required_tool == "apply_patch"


def test_dsh_model_is_unset_unless_an_explicit_label_is_given(tmp_path, monkeypatch):
    output = tmp_path / "report.json"
    calls = []

    def fake_run_task(task, **options):
        calls.append((task, options))
        return {
            "task": task.name,
            "wall_s": 0.0,
            "returncode": 0,
            "timed_out": False,
            "tests_pass": True,
            "tests_unchanged": True,
            "final_pass": True,
            "final_sha256": "",
            "test_output_sha256": "",
            "event_errors": 0,
            "event_warnings": 0,
            "events": {},
            "tool_calls": {},
            "tool_call_count": 0,
            "tool_errors": 0,
            "quality_pass": True,
        }

    monkeypatch.setattr(BENCH, "run_task", fake_run_task)
    assert BENCH.main(
        [
            "--runner",
            "dsh",
            "--tasks",
            "single-file",
            "--work-root",
            str(tmp_path / "work"),
            "--output",
            str(output),
        ]
    ) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["model"] is None
    assert calls[0][1]["model"] == BENCH.DEFAULT_MODEL

    assert BENCH.main(
        [
            "--runner",
            "dsh",
            "--model-label",
            "DeepSeek via DSH",
            "--tasks",
            "single-file",
            "--work-root",
            str(tmp_path / "work"),
            "--output",
            str(output),
        ]
    ) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["model"] == "DeepSeek via DSH"
