"""Tests for the config-level Nsys/NCU engine wrapper."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "ncu-engine-wrapper.py"


def load_wrapper():
    spec = importlib.util.spec_from_file_location("ncu_engine_wrapper", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def executable(tmp_path: Path) -> Path:
    path = tmp_path / "strata"
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_nsys_wraps_the_engine_directly(tmp_path: Path):
    wrapper = load_wrapper()
    engine = executable(tmp_path)
    env = {
        "STRATA_PROFILE_MODE": "nsys",
        "STRATA_PROFILE_REAL_ENGINE": str(engine),
        "STRATA_PROFILE_OUTPUT": str(tmp_path / "trace"),
    }

    argv = wrapper.engine_command(env, ["--serve", "--batch", "6"])

    assert argv[:2] == ["/usr/local/bin/nsys", "profile"]
    assert argv[-4:] == [str(engine), "--serve", "--batch", "6"]
    assert argv.count("--trace=cuda,nvtx,osrt") == 1


def test_ncu_requires_a_bounded_named_run(tmp_path: Path):
    wrapper = load_wrapper()
    engine = executable(tmp_path)
    env = {
        "STRATA_PROFILE_MODE": "ncu",
        "STRATA_PROFILE_REAL_ENGINE": str(engine),
        "STRATA_PROFILE_OUTPUT": str(tmp_path / "profile"),
        "STRATA_NCU_KERNEL_REGEX": "qsa_prompt_attn",
        "STRATA_NCU_LAUNCH_SKIP": "2",
        "STRATA_NCU_LAUNCH_COUNT": "1",
    }

    argv = wrapper.engine_command(env, ["--serve"])

    assert argv[0] == "/usr/local/cuda-13.0/bin/ncu"
    assert argv[argv.index("--kernel-name") + 1] == "regex:qsa_prompt_attn"
    assert argv[argv.index("--launch-skip") + 1] == "2"
    assert argv[argv.index("--launch-count") + 1] == "1"
    assert argv[argv.index("--export") + 1] == str(tmp_path / "profile")
    assert argv[-2:] == [str(engine), "--serve"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"STRATA_PROFILE_MODE": "other"}, "MODE"),
        ({"STRATA_PROFILE_REAL_ENGINE": "strata"}, "absolute path"),
        ({"STRATA_PROFILE_OUTPUT": "relative"}, "absolute path"),
        ({"STRATA_NCU_LAUNCH_COUNT": "0"}, "count"),
    ],
)
def test_rejects_unsafe_arguments(tmp_path: Path, change: dict[str, str], message: str):
    wrapper = load_wrapper()
    engine = executable(tmp_path)
    env = {
        "STRATA_PROFILE_MODE": "ncu",
        "STRATA_PROFILE_REAL_ENGINE": str(engine),
        "STRATA_PROFILE_OUTPUT": str(tmp_path / "profile"),
    }
    env.update(change)

    with pytest.raises(ValueError, match=message):
        wrapper.engine_command(env, ["--serve"])
