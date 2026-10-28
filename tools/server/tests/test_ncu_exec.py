"""Tests for the captured-argv launcher used to profile the engine under Nsight Compute."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "ncu-exec.py"


def load_ncu_exec():
    spec = importlib.util.spec_from_file_location("ncu_exec", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_load_runtime_rejects_missing_argv(tmp_path: Path):
    ncu_exec = load_ncu_exec()
    path = tmp_path / "runtime.json"
    path.write_text('{"argv": [], "env": {}}', encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(ValueError, match="argv"):
        ncu_exec.load_runtime(path)


def test_load_runtime_keeps_argv_and_environment(tmp_path: Path):
    ncu_exec = load_ncu_exec()
    path = tmp_path / "runtime.json"
    path.write_text(
        '{"argv": ["/bin/true"], "env": {"A": "1"}}',
        encoding="utf-8",
    )
    path.chmod(0o600)

    argv, env = ncu_exec.load_runtime(path)

    assert argv == ["/bin/true"]
    assert env == {"A": "1"}


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_load_runtime_rejects_shared_file_permissions(tmp_path: Path):
    ncu_exec = load_ncu_exec()
    path = tmp_path / "runtime.json"
    path.write_text(
        '{"argv": ["/bin/true"], "env": {"A": "1"}}',
        encoding="utf-8",
    )
    path.chmod(0o644)

    with pytest.raises(ValueError, match="permissions"):
        ncu_exec.load_runtime(path)
