"""固定一次 managed runtime 启动所依赖的本地制品身份。

这里的身份用于回答两个问题：当前磁盘上的 engine 和 Python runtime 是否仍是
上次验证通过的那一套，以及在不能证明回滚目标未变化时是否必须拒绝 drain。
模型分片和动态库的安装级清单仍由后续制品存储层负责，本模块不伪造那部分证据。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path

from strata_control import engine_component


RUNTIME_ROOTS = ("serve", "strata_control")
IGNORED_RUNTIME_PARTS = frozenset({"fixtures", "tests", "__pycache__"})
MAX_RUNTIME_FILES = 8192
READ_CHUNK = 1024 * 1024
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
ARTIFACT_KEYS = frozenset({
    "artifact_id",
    "engine_path",
    "engine_sha256",
    "engine_size",
    "runtime_code_files",
    "runtime_code_sha256",
})


class ArtifactError(Exception):
    """A local launch artifact is missing, unsafe, or cannot be read."""


def _digest_file(path: Path) -> tuple[int, str]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ArtifactError(f"cannot open {path}: {exc.strerror or exc}") from None
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise ArtifactError(f"{path} is not a regular file")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, READ_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
        return details.st_size, digest.hexdigest()
    finally:
        os.close(descriptor)


def _engine_path(cfg: dict, root: Path) -> Path:
    raw = cfg.get("exe")
    if not isinstance(raw, str) or not raw:
        raise ArtifactError("run config has no engine executable")
    try:
        if engine_component.is_reference(raw):
            return Path(engine_component.expand(raw, cfg)).resolve(strict=True)
        path = Path(raw)
        if not path.is_absolute():
            cwd = Path(cfg.get("cwd") or root)
            if not cwd.is_absolute():
                cwd = root / cwd
            path = cwd / path
        return path.resolve(strict=True)
    except (OSError, engine_component.EngineMissing) as exc:
        raise ArtifactError(f"cannot resolve the engine executable: {exc}") from None


def _runtime_files(root: Path) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    for item in RUNTIME_ROOTS:
        base = root / item
        if not base.exists():
            continue
        if base.is_symlink() or not base.is_dir():
            raise ArtifactError(f"runtime code root {base} is unsafe")
        for path in base.rglob("*.py"):
            relative = path.relative_to(root)
            if path.name.startswith("test_") or any(part in IGNORED_RUNTIME_PARTS
                                                    for part in relative.parts):
                continue
            if not path.is_file():
                continue
            if path.is_symlink():
                raise ArtifactError(f"runtime code file {path} is a symlink")
            files.append((relative.as_posix(), path))
    if not files:
        raise ArtifactError("runtime Python code is unavailable")
    if len(files) > MAX_RUNTIME_FILES:
        raise ArtifactError("runtime Python code has too many files to identify")
    return sorted(files, key=lambda item: item[0])


def _runtime_digest(root: Path) -> tuple[int, str]:
    files = _runtime_files(root)
    digest = hashlib.sha256(b"strata-runtime-code-v1\0")
    for name, path in files:
        encoded = name.encode("utf-8", "surrogateescape")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        size, file_digest = _digest_file(path)
        digest.update(size.to_bytes(8, "big"))
        digest.update(bytes.fromhex(file_digest))
    return len(files), digest.hexdigest()


def _artifact_id(body: dict) -> str:
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def capture(source_bytes: bytes, *, root: Path) -> dict:
    """Return the exact engine and Python runtime identity seen before launch."""
    try:
        cfg = json.loads(source_bytes.decode("utf-8-sig"))
    except (UnicodeError, ValueError):
        raise ArtifactError("run config is not valid UTF-8 JSON") from None
    if not isinstance(cfg, dict):
        raise ArtifactError("run config must be an object")
    engine = _engine_path(cfg, root)
    engine_size, engine_sha256 = _digest_file(engine)
    runtime_files, runtime_sha256 = _runtime_digest(root)
    body = {
        "engine_path": str(engine),
        "engine_sha256": engine_sha256,
        "engine_size": engine_size,
        "runtime_code_files": runtime_files,
        "runtime_code_sha256": runtime_sha256,
    }
    return {**body, "artifact_id": _artifact_id(body)}


def validate(value: object) -> dict:
    """Validate a persisted artifact object without trusting its JSON shape."""
    if not isinstance(value, dict) or set(value) != ARTIFACT_KEYS:
        raise ArtifactError("artifact identity has an unsupported shape")
    path = value.get("engine_path")
    if not isinstance(path, str) or not path or "\0" in path or not Path(path).is_absolute():
        raise ArtifactError("artifact engine path is invalid")
    for key in ("engine_sha256", "runtime_code_sha256", "artifact_id"):
        if not isinstance(value.get(key), str) or not HEX64.fullmatch(value[key]):
            raise ArtifactError(f"artifact {key} is invalid")
    for key in ("engine_size", "runtime_code_files"):
        if type(value.get(key)) is not int or value[key] <= 0:
            raise ArtifactError(f"artifact {key} is invalid")
    body = {key: value[key] for key in ARTIFACT_KEYS if key != "artifact_id"}
    if value["artifact_id"] != _artifact_id(body):
        raise ArtifactError("artifact ID does not match its fields")
    return value
