"""T-probe: the version stamp must hash the files the processes actually run."""
from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "tools" / "server" / "probe-version-stamp.py"
spec = importlib.util.spec_from_file_location("probe_version_stamp", SCRIPT)
probe = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(probe)


def _stamp(engine: str, server: str) -> dict:
    return {
        "files": {
            "engine": {"path": engine},
            "server": {"path": server},
            "launcher": {"path": "/launcher"},
            "config": {"path": "/config"},
        },
        "running": {
            "engine": {"binary": engine},
            "server": {"script": server, "config": "/config"},
        },
        "health": {"loaded": True},
    }


def test_check_accepts_the_running_paths():
    assert probe.check(_stamp("/run/strata", "/run/serve/server.py")) == []


def test_check_rejects_a_server_hash_from_another_tree():
    stamp = _stamp("/run/strata", "/run/serve/server.py")
    stamp["files"]["server"]["path"] = "/opt/ai/src/old/serve/server.py"
    problems = probe.check(stamp)
    assert any("running server script" in problem for problem in problems)


def test_check_rejects_an_unidentified_server_script():
    stamp = _stamp("/run/strata", "/run/serve/server.py")
    stamp["running"]["server"]["script"] = None
    problems = probe.check(stamp)
    assert any("cannot identify" in problem for problem in problems)


def test_check_rejects_a_config_hash_from_another_file():
    stamp = _stamp("/run/strata", "/run/serve/server.py")
    stamp["files"]["config"]["path"] = "/run/old/strata-live.json"
    problems = probe.check(stamp)
    assert any("running server config" in problem for problem in problems)


def test_check_rejects_an_unidentified_running_config():
    stamp = _stamp("/run/strata", "/run/serve/server.py")
    stamp["running"]["server"]["config"] = None
    problems = probe.check(stamp)
    assert any("cannot identify its --config" in problem for problem in problems)


def test_probe_discovers_engine_independent_of_build_directory():
    assert probe.ENGINE_PROCESS_PATTERN == "[s]trata --serve"
    assert "build/strata --serve" not in probe.REMOTE_PROGRAM
    assert "[s]trata --serve" in probe.REMOTE_PROGRAM


def test_expected_env_accepts_exact_running_switches():
    stamp = _stamp("/run/strata", "/run/serve/server.py")
    stamp["running"]["engine"]["env"] = ["STRATA_HC_Q8=1", "STRATA_EXP_MODE=8"]
    assert probe.check_expected_env(stamp, {"STRATA_HC_Q8": "1", "STRATA_EXP_MODE": "8"}) == []


def test_expected_env_rejects_a_dropped_switch():
    stamp = _stamp("/run/strata", "/run/serve/server.py")
    stamp["running"]["engine"]["env"] = ["STRATA_EXP_MODE=8"]
    problems = probe.check_expected_env(stamp, {"STRATA_HC_Q8": "1"})
    assert problems == ["running engine env: missing STRATA_HC_Q8=1"]


def test_expected_env_rejects_a_different_value():
    stamp = _stamp("/run/strata", "/run/serve/server.py")
    stamp["running"]["engine"]["env"] = ["STRATA_HC_Q8=0"]
    problems = probe.check_expected_env(stamp, {"STRATA_HC_Q8": "1"})
    assert problems == ["running engine env: STRATA_HC_Q8=0 != expected 1"]
