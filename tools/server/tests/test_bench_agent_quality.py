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
        "session_id",
    }
    assert summary["event_errors"] == 0
    assert summary["tool_errors"] == 0
    assert summary["event_warnings"] == 2
    assert summary["session_id"] is None


def test_dsh_event_summary_counts_calls_errors_and_final(tmp_path):
    events = tmp_path / "dsh-events.jsonl"
    events.write_text(
        "\n".join(
            [
                '{"type":"session","sessionId":"session-example"}',
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
    assert summary["session_id"] == "session-example"


def test_every_task_has_a_machine_checkable_prompt():
    for task in BENCH.TASKS.values():
        assert "RESULT=PASS" in task.prompt
        assert BENCH.FINAL_RULE in task.prompt
        assert "no summary, explanation, blank line, or extra punctuation" in task.prompt
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
                "wall_scope": "runner_process; includes client-side post-turn auxiliary work",
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
    assert report["runner_profile"] == "dsh-headless"
    assert report["dsh_patch"] is None
    assert report["wall_scope"].startswith("runner_process")
    assert report["rows"][0]["wall_scope"].startswith("runner_process")
    assert calls[0][1]["model"] == BENCH.DEFAULT_MODEL
    assert calls[0][1]["dsh_patch"] is None

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


def test_dsh_patch_is_an_explicit_launcher_overlay_not_a_profile_edit():
    command = BENCH.dsh_command("dsh", BENCH.TASKS["single-file"], pathlib.Path("overlay.yml"))

    assert command[:5] == [
        "dsh",
        "--profile",
        "headless",
        "--patch",
        str(pathlib.Path("overlay.yml").resolve()),
    ]
    assert command[-2:] == ["--json", BENCH.TASKS["single-file"].prompt]


def test_prompt_suffix_is_appended_without_rewriting_the_fixture():
    task = BENCH.TASKS["transaction-rollback"]

    assert BENCH.task_prompt(task) == task.prompt
    assert BENCH.task_prompt(task, "FINAL RULE").endswith("\n\nFINAL RULE")


def test_missing_dsh_patch_is_rejected_before_any_task(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        BENCH,
        "run_task",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("task must not run")),
    )

    try:
        BENCH.main([
            "--runner", "dsh",
            "--dsh-patch", str(tmp_path / "missing.yml"),
            "--tasks", "single-file",
        ])
    except SystemExit as error:
        assert error.code == 2
    else:
        raise AssertionError("missing overlay should stop the benchmark")
    assert "does not exist" in capsys.readouterr().err


def test_quality_report_records_comparison_identity(tmp_path, monkeypatch):
    output = tmp_path / "report.json"
    catalog = tmp_path / "catalog.json"
    catalog.write_text('{"models":[]}\n', encoding="utf-8")

    def fake_run_task(task, **options):
        return {
            "task": task.name,
            "wall_s": 0.0,
            "wall_scope": "runner_process; includes client-side post-turn auxiliary work",
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
            "tool_calls": {"apply_patch": 1},
            "tool_call_count": 1,
            "tool_errors": 0,
            "quality_pass": True,
        }

    monkeypatch.setattr(BENCH, "run_task", fake_run_task)
    monkeypatch.setenv("STRATA_API_KEY", "test-only")
    assert BENCH.main(
        [
            "--tasks",
            "apply-patch",
            "--model-catalog",
            str(catalog),
            "--work-root",
            str(tmp_path / "work"),
            "--output",
            str(output),
        ]
    ) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema"] == 5
    assert report["provider_url"] == "http://127.0.0.1:18080/v1"
    assert report["model_catalog"] == str(catalog.resolve())
    assert report["model_catalog_sha256"] == BENCH.sha256(catalog.read_bytes())
    assert report["fixture_suite_sha256"] == BENCH.fixture_suite_sha256(["apply-patch"])
    assert report["prompt_suffix_sha256"] is None


def _quality_report(tasks, *, model="model"):
    rows = []
    for task, passed in tasks.items():
        rows.append(
            {
                "task": task,
                "quality_pass": passed,
                "tool_calls": {"apply_patch": 1} if passed else {},
                "event_warnings": 0,
                "event_errors": 0,
            }
        )
    return {
        "runner": "codex",
        "runner_profile": "codex-exec",
        "model": model,
        "reasoning_effort": "high",
        "provider_url": "http://127.0.0.1:18080/v1",
        "model_catalog_sha256": "catalog",
        "fixture_suite_sha256": "fixture",
        "prompt_suffix_sha256": None,
        "tasks": sorted(tasks),
        "rows": rows,
    }


def test_quality_compare_rejects_identity_mismatch():
    baseline = _quality_report({"single-file": True})
    candidate = _quality_report({"single-file": True}, model="other-model")
    comparison = BENCH.compare_reports(baseline, candidate)
    assert comparison["verdict"] == "reject_incomparable"
    assert comparison["identity_mismatches"]["model"] == {
        "baseline": "model",
        "candidate": "other-model",
    }


def test_quality_compare_rejects_candidate_regression():
    baseline = _quality_report({"single-file": True, "cross-file": False})
    candidate = _quality_report({"single-file": False, "cross-file": True})
    comparison = BENCH.compare_reports(baseline, candidate)
    assert comparison["verdict"] == "reject_candidate_regression"
    assert comparison["candidate_regressions"] == [
        {"task": "single-file", "reason": "quality pass regressed"}
    ]
    assert comparison["candidate_recoveries"] == ["cross-file"]


def test_quality_compare_blocks_shared_failures():
    report = _quality_report({"single-file": True, "cross-file": False})
    comparison = BENCH.compare_reports(report, report)
    assert comparison["verdict"] == "blocked_shared_failures"
    assert comparison["shared_failures"] == ["cross-file"]


def test_quality_compare_passes_only_all_candidate_rows():
    report = _quality_report({"single-file": True, "cross-file": True})
    comparison = BENCH.compare_reports(report, report)
    assert comparison["verdict"] == "pass"
    assert comparison["candidate_regressions"] == []
    assert comparison["shared_failures"] == []
