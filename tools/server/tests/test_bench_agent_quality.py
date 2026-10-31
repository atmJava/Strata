import importlib.util
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
                '{"type":"item.completed","item":{"type":"error","message":"Model metadata for `m` not found. Defaulting to fallback metadata; this can degrade performance and cause issues."}}',
                '{"type":"item.completed","item":{"type":"error","message":"Codex is ignoring 1 unrecognized configuration setting."}}',
                '{"type":"item.completed","item":{"type":"agent_message","text":"RESULT=PASS"}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    summary = BENCH.event_summary(events)
    assert summary["tool_calls"] == {"exec_command": 1}
    assert summary["final"] == "RESULT=PASS"
    assert set(summary) == {
        "events",
        "tool_calls",
        "tool_call_count",
        "event_errors",
        "event_warnings",
        "final",
    }
    assert summary["event_errors"] == 0
    assert summary["event_warnings"] == 2


def test_every_task_has_a_machine_checkable_prompt():
    for task in BENCH.TASKS.values():
        assert "RESULT=PASS" in task.prompt
        assert "without changing tests" in task.prompt or "not the tests" in task.prompt
        assert task.expected_files
        assert task.protected_files
