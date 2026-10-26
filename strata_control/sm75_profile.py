"""Read-only checks for the dedicated 2 x RTX 2080 Ti server profile.

This module encodes only values that are present in the repository's server documentation and
``tools/server/production-env-2080ti.json``. It does not inspect or change a running host.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


EXPECTED = {
    "backend": "cuda",
    "arch": "qwen4exp",
    "parallel": 6,
    "max_context": 262144,
    "batch_groups": 2,
    "kv": "int8",
    "kv_resident": 32768,
}

ENV_FILE = Path(__file__).resolve().parents[1] / "tools" / "server" / "production-env-2080ti.json"
LEGACY_VISION_READERS = ("strata-vision-hip", "strata-vision-sycl", "strata-vision-metal")


def _flag(args: list[str], name: str) -> str | None:
    value = None
    for index, item in enumerate(args[:-1]):
        if item == name:
            value = str(args[index + 1])
    return value


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _reader_name(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return Path(value).name.lower().removesuffix(".exe")


def expected_env(path: str | Path = ENV_FILE) -> dict[str, str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    env = data.get("env") if isinstance(data, dict) else None
    if not isinstance(env, dict) or not env:
        raise ValueError(f"{path} has no env object")
    return {str(key): str(value) for key, value in env.items()}


def check_config(cfg: dict, *, process_env: dict[str, str] | None = None,
                 env_path: str | Path = ENV_FILE) -> dict:
    """Compare a run config and process environment with the documented production shape.

    Return ``{"ok": bool, "errors": [...], "warnings": [...], "observed": {...}}``.
    The result is static evidence only; it makes no claim that an engine loaded on a GPU.
    """
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    args = cfg.get("args") if isinstance(cfg.get("args"), list) else []
    args = [str(item) for item in args]

    def error(code: str, message: str) -> None:
        errors.append({"code": code, "message": message})

    def warn(code: str, message: str) -> None:
        warnings.append({"code": code, "message": message})

    for key in ("backend", "arch"):
        actual = cfg.get(key)
        if actual != EXPECTED[key]:
            error(f"{key}_mismatch",
                  f"{key} must be {EXPECTED[key]!r} for this server profile; found {actual!r}")

    parallel = _integer(cfg.get("parallel"))
    if parallel is None:
        parallel = _integer(_flag(args, "--parallel"))
    if parallel != EXPECTED["parallel"]:
        error("parallel_mismatch",
              f"parallel must be {EXPECTED['parallel']}; found {parallel!r}")

    batch = _integer(_flag(args, "--batch"))
    if batch is None:
        batch = parallel
    if batch != EXPECTED["parallel"]:
        error("batch_mismatch",
              f"effective --batch must be {EXPECTED['parallel']}; found {batch!r}")

    groups = _integer(cfg.get("batch_groups"))
    if groups is None:
        groups = _integer(_flag(args, "--batch-groups"))
    if groups != EXPECTED["batch_groups"]:
        error("batch_groups_mismatch",
              f"batch_groups must be {EXPECTED['batch_groups']}; found {groups!r}")

    max_context = _integer(cfg.get("max_context"))
    if max_context is None:
        max_context = _integer(_flag(args, "--max-context"))
    if max_context != EXPECTED["max_context"]:
        error("max_context_mismatch",
              f"max_context must be {EXPECTED['max_context']}; found {max_context!r}")

    kv = _flag(args, "--kv") or cfg.get("kv")
    if str(kv).strip().lower() != EXPECTED["kv"]:
        error("kv_mismatch", f"KV cache must be {EXPECTED['kv']!r}; found {kv!r}")

    resident = _integer(_flag(args, "--kv-resident"))
    if resident is None:
        resident = _integer(cfg.get("kv_resident"))
    if resident != EXPECTED["kv_resident"]:
        error("kv_resident_mismatch",
              f"kv_resident must be {EXPECTED['kv_resident']}; found {resident!r}")

    drafting = str(cfg.get("drafting") or "auto").strip().lower()
    if drafting not in ("auto", "mtp", "dflash2", "off"):
        error("drafting_unsupported",
              f"drafting must be auto, mtp, dflash2 or off in this build; found {drafting!r}")

    images = cfg.get("images")
    image_backend = None
    if images is not None:
        if not isinstance(images, dict):
            error("images_invalid", "images must be an object when present")
        else:
            image_backend = str(images.get("backend") or "").strip().lower()
            if image_backend != "cuda":
                error("images_backend_mismatch",
                      f"images.backend must be 'cuda' in this server build; found {image_backend!r}")

    vision = cfg.get("vision")
    vision_gpu = None
    if vision is not None:
        if not isinstance(vision, dict):
            error("vision_invalid", "vision must be an object when present")
        else:
            vision_gpu = vision.get("gpu") is True
            for key in ("exe", "cpu_exe"):
                reader = _reader_name(vision.get(key))
                if reader in LEGACY_VISION_READERS:
                    error("vision_backend_mismatch",
                          f"vision.{key} names the removed {reader} reader; use the CUDA-capable "
                          f"strata-vision program")

    gpu = cfg.get("gpu")
    if isinstance(gpu, int) and not isinstance(gpu, bool):
        gpu = [gpu]
    if gpu is not None:
        if not isinstance(gpu, list) or len(gpu) != 2 or len(set(gpu)) != 2:
            error("gpu_shape", f"gpu must name two distinct CUDA devices; found {gpu!r}")
    else:
        warn("gpu_not_pinned",
             "the config does not pin gpu=[0,1]; the server will use the CUDA runtime's visible devices")

    cfg_env = cfg.get("env") if isinstance(cfg.get("env"), dict) else {}
    seen_env = process_env if process_env is not None else os.environ
    expected = expected_env(env_path)
    env_sources: dict[str, str] = {}
    for key, wanted in expected.items():
        config_value = cfg_env.get(key)
        process_value = seen_env.get(key)
        if key == "STRATA_ADMIT_SHORT_FIRST":
            # The Python HTTP server reads this switch, so a run-config env entry reaches only the engine child
            # and cannot replace the service environment.
            actual = str(process_value) if process_value is not None else None
            if actual is not None:
                env_sources[key] = "process"
            if config_value is not None:
                warn("env_server_switch_in_config",
                     "STRATA_ADMIT_SHORT_FIRST in the run config reaches only the engine child; "
                     "the systemd service must also provide it")
        elif config_value is not None:
            env_sources[key] = "run_config"
            actual = str(config_value)
        elif process_value is not None:
            env_sources[key] = "process"
            actual = str(process_value)
        else:
            actual = None
        if actual is None:
            owner = "the systemd service environment" if key == "STRATA_ADMIT_SHORT_FIRST" else "the run config"
            error("env_missing", f"{key} must be supplied by {owner}; it is absent")
        elif actual != wanted:
            error("env_mismatch", f"{key} must be {wanted!r}; found {actual!r}")

    model_name = cfg.get("model_name")
    if not isinstance(model_name, str) or "qwen3.8-flash-next" not in model_name.lower():
        warn("model_name",
             f"model_name does not identify Qwen3.8-Flash-Next: {model_name!r}")

    observed = {
        "backend": cfg.get("backend"),
        "arch": cfg.get("arch"),
        "parallel": parallel,
        "batch": batch,
        "batch_groups": groups,
        "max_context": max_context,
        "kv": kv,
        "kv_resident": resident,
        "drafting": drafting,
        "images_backend": image_backend,
        "vision_gpu": vision_gpu,
        "gpu": gpu,
        "model_name": model_name,
        "env_sources": env_sources,
    }
    return {"ok": not errors, "errors": errors, "warnings": warnings, "observed": observed}


def check_path(path: str | Path, *, process_env: dict[str, str] | None = None,
               env_path: str | Path = ENV_FILE) -> dict:
    cfg = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(cfg, dict):
        raise ValueError(f"{path} is not a JSON object")
    result = check_config(cfg, process_env=process_env, env_path=env_path)
    result["config"] = str(Path(path).expanduser().resolve())
    return result
