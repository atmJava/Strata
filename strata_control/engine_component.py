"""Resolve a locally built or copied CUDA sm_75 engine.

The server distribution has no installer, updater, signature feed or cross-vendor component registry.
A run config normally names an absolute executable. For older configs, ``component:strata-engine``
selects a local ``strata`` executable under ``STRATA_COMPONENTS_DIR`` (or the user's data folder).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

MANIFEST = "engine-component.json"
COMPONENT_ID = "strata-engine"
REFERENCE_PREFIX = "component:"
ENV_DIR = "STRATA_COMPONENTS_DIR"


class EngineMissing(ValueError):
    """No local engine was found, so nothing can be started."""

    code = "engine_component_missing"
    exit_code = 5

    def __init__(self, reason: str, root: Path | None = None):
        self.reason = reason
        self.root = root
        where = f" under {root}" if root else ""
        super().__init__(
            f"No local CUDA sm_75 engine was found{where}: {reason}. "
            "Build the engine with CMake or point the run config's exe at an existing local binary."
        )


class EngineIntegrityError(EngineMissing):
    code = "engine_component_integrity"


def components_dir(environ: dict[str, str] | None = None, *, home: Path | None = None) -> Path:
    env = os.environ if environ is None else environ
    if env.get(ENV_DIR):
        return Path(env[ENV_DIR]).expanduser()
    base = Path(env.get("XDG_DATA_HOME") or str((home or Path.home()) / ".local" / "share"))
    return base / "strata" / "components"


def engine_dir(root: Path | None = None) -> Path:
    return (root or components_dir()) / "engine"


def package_lib_dirs(folder: str | Path) -> list[str]:
    """The engine folder plus bounded relative library folders named by BUILD.json."""
    folder = Path(folder)
    result = [str(folder)]
    try:
        data = json.loads((folder / "BUILD.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, AttributeError):
        return result
    names = data.get("lib_dirs") if isinstance(data, dict) else None
    for name in names if isinstance(names, list) else []:
        if not isinstance(name, str) or not name or Path(name).is_absolute() or ".." in Path(name).parts:
            continue
        child = folder / name
        if child.is_dir() and str(child) not in result:
            result.append(str(child))
    return result


def is_reference(value: object) -> bool:
    return isinstance(value, str) and value.startswith(REFERENCE_PREFIX)


def _entrypoint(folder: Path) -> Path | None:
    try:
        manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, AttributeError):
        manifest = {}
    name = manifest.get("entrypoint") if isinstance(manifest, dict) else None
    if not isinstance(name, str) or not name or Path(name).is_absolute() or ".." in Path(name).parts:
        name = "strata"
    return folder / name


def _version(folder: Path) -> tuple:
    try:
        value = json.loads((folder / "BUILD.json").read_text(encoding="utf-8-sig")).get("version", "")
    except (OSError, ValueError, AttributeError):
        value = ""
    return tuple(int(part) if part.isdigit() else 0 for part in str(value).split("."))


def _verify_manifest(folder: Path, exe: Path) -> None:
    """When a manifest is present, refuse changed packaged files before starting them."""
    path = folder / MANIFEST
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        files = data["files"]
        if data.get("platform") != "linux-x86_64" or data.get("backend") != "cuda":
            raise ValueError("manifest is not for Linux CUDA")
        entries = {str(item["path"]): item for item in files}
        entrypoint = str(data.get("entrypoint") or "strata")
        if entrypoint not in entries:
            raise ValueError("manifest does not contain its entrypoint")
        for name, item in entries.items():
            target = folder / name
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts or not target.is_file():
                raise ValueError(f"missing or unsafe file {name}")
            body = target.read_bytes()
            if len(body) != int(item["bytes"]) or hashlib.sha256(body).hexdigest() != item["sha256"]:
                raise ValueError(f"changed file {name}")
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise EngineIntegrityError(f"the engine manifest failed verification: {exc}", folder) from None
    if not exe.is_file():
        raise EngineMissing("the manifest's entrypoint is missing", folder)


def resolve(root: Path | None = None, *, full: bool = False) -> Path:
    base = root or engine_dir()
    folders = [base]
    try:
        folders.extend(sorted(path for path in base.iterdir() if path.is_dir()))
    except OSError:
        pass
    found: list[tuple[tuple, Path, Path]] = []
    problems: list[str] = []
    broken: list[str] = []
    for folder in folders:
        exe = _entrypoint(folder)
        if exe is None or not exe.is_file():
            continue
        if not os.access(exe, os.X_OK):
            problems.append(f"{exe} is not executable")
            continue
        if full:
            try:
                _verify_manifest(folder, exe)
            except EngineMissing as exc:
                broken.append(str(exc))
                continue
        found.append((_version(folder), folder, exe))
    if not found:
        if broken:
            raise EngineIntegrityError("; ".join(broken), base)
        detail = "; ".join(problems) if problems else f"no executable strata binary under {base}"
        raise EngineMissing(detail, base)
    _, folder, exe = max(found, key=lambda item: (item[0], item[1].name))
    return exe.resolve()


def expand(value: str, cfg: dict | None = None, *, root: Path | None = None, full: bool = True) -> str:
    if not is_reference(value):
        return value
    name = value[len(REFERENCE_PREFIX):]
    if name not in ("", COMPONENT_ID):
        raise EngineMissing(f"unknown local component {name!r}", root or engine_dir())
    backend = (cfg or {}).get("backend")
    if backend not in (None, "cuda"):
        raise EngineMissing(f"this distribution has no {backend!r} engine", root or engine_dir())
    return str(resolve(root, full=full))


def cuda_archs_present() -> list[str]:
    """The target is fixed at sm_75; BUILD.json cannot widen this build."""
    return ["sm_75"]
