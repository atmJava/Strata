"""不可变的本地 release 存储。

release 把一次已验证启动所需的 engine、Python 服务代码、launch config 和启动包装器
固化到以 release ID 命名的私有目录。release ID 同时覆盖代码制品、精确启动配置和
启动包装器，避免相同代码在不同参数下错误复用旧配置。运行中的 owner 可以从这里
重新启动旧制品，而不是依赖可能已被覆盖的构建路径。
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path

from strata_control import artifacts, atomic
from strata_control.private_files import private_mkdir


MANIFEST = "release.json"
CONFIG = "run.json"
LAUNCHER = "launch_server.py"
MAX_MANIFEST = 64 * 1024
MAX_CONFIG = 1024 * 1024
READ_CHUNK = 1024 * 1024
ID = re.compile(r"[0-9a-f]{64}\Z")
RELEASE_KEYS = frozenset({
    "schema_version",
    "release_id",
    "artifact_id",
    "source_sha256",
    "engine",
    "config",
    "launcher",
    "config_sha256",
    "config_size",
    "engine_sha256",
    "engine_size",
    "runtime_file_count",
    "runtime_sha256",
    "launcher_sha256",
    "launcher_size",
})
LAUNCHER_BYTES = (
    "import runpy\n"
    "\n"
    "if __name__ == \"__main__\":\n"
    "    runpy.run_module(\"serve.server\", run_name=\"__main__\", alter_sys=True)\n"
).encode("utf-8")


class ReleaseError(Exception):
    """A release is missing, unsafe, changed, or cannot be materialized."""


def _release_id(artifact_id: str, source_sha256: str) -> str:
    """Bind exact service code, source-config bytes, and launcher bytes."""
    raw = (
        b"strata-release-v1\0" +
        artifact_id.encode("ascii") + b"\0" +
        bytes.fromhex(source_sha256) +
        hashlib.sha256(LAUNCHER_BYTES).digest()
    )
    return hashlib.sha256(raw).hexdigest()


@contextmanager
def _release_lock(directory: Path):
    path = directory / ".lock"
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or not _private(details):
            raise ReleaseError("release lock is not private")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _private(details: os.stat_result) -> bool:
    return details.st_uid == os.getuid() and not details.st_mode & 0o077


def _private_file(path: Path, *, maximum: int, executable: bool = False) -> os.stat_result:
    try:
        details = path.lstat()
    except OSError as exc:
        raise ReleaseError(f"cannot read release file {path}: {exc.strerror or exc}") from None
    if not stat.S_ISREG(details.st_mode) or not _private(details) or details.st_size > maximum:
        raise ReleaseError(f"release file is not private or is invalid: {path}")
    if executable and not details.st_mode & stat.S_IXUSR:
        raise ReleaseError(f"release engine is not executable: {path}")
    return details


def _private_directory(path: Path) -> os.stat_result:
    try:
        details = path.lstat()
    except OSError as exc:
        raise ReleaseError(f"cannot read release directory {path}: {exc.strerror or exc}") from None
    if not stat.S_ISDIR(details.st_mode) or not _private(details):
        raise ReleaseError(f"release directory is not private: {path}")
    return details


def _digest(path: Path) -> tuple[int, str]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReleaseError(f"cannot open release source {path}: {exc.strerror or exc}") from None
    try:
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, READ_CHUNK)
            if not chunk:
                return size, digest.hexdigest()
            size += len(chunk)
            digest.update(chunk)
    finally:
        os.close(descriptor)


def _copy(source: Path, target: Path, *, executable: bool = False) -> None:
    source_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    target_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        source_fd = os.open(source, source_flags)
    except OSError as exc:
        raise ReleaseError(f"cannot open release source {source}: {exc.strerror or exc}") from None
    try:
        details = os.fstat(source_fd)
        if not stat.S_ISREG(details.st_mode):
            raise ReleaseError(f"release source is not a regular file: {source}")
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target_fd = os.open(target, target_flags, 0o700 if executable else 0o600)
        try:
            while True:
                chunk = os.read(source_fd, READ_CHUNK)
                if not chunk:
                    break
                view = memoryview(chunk)
                while view:
                    written = os.write(target_fd, view)
                    view = view[written:]
            os.fsync(target_fd)
        finally:
            os.close(target_fd)
        os.chmod(target, 0o700 if executable else 0o600)
    finally:
        os.close(source_fd)


def _safe_relative(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value or "\0" in value:
        raise ReleaseError(f"release {label} path is invalid")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path.name in ("", ".", ".."):
        raise ReleaseError(f"release {label} path is unsafe")
    return path


def _config_bytes(source_bytes: bytes, engine: Path) -> bytes:
    try:
        config = json.loads(source_bytes.decode("utf-8-sig"))
    except (UnicodeError, ValueError):
        raise ReleaseError("release source config is not valid UTF-8 JSON") from None
    if not isinstance(config, dict):
        raise ReleaseError("release source config must be an object")
    config["exe"] = str(engine)
    data = (
        json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if len(data) > MAX_CONFIG:
        raise ReleaseError("release config is too large")
    return data


def _manifest_bytes(record: dict) -> bytes:
    return (
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _sync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sync_tree(path: Path) -> None:
    directories = [path, *(item for item in path.rglob("*") if item.is_dir())]
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        _sync_directory(directory)


def _remove_staging(path: Path, releases_dir: Path) -> None:
    try:
        if path.parent == releases_dir and path.name.startswith(".") and path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
    except OSError:
        pass


def create(source_bytes: bytes, artifact: dict, *, root: Path, releases_dir: Path) -> dict:
    """Materialize one exact launch release, reusing only an identical valid copy."""
    try:
        artifact = artifacts.validate(artifact)
    except artifacts.ArtifactError as exc:
        raise ReleaseError(str(exc)) from None
    artifact_id = artifact["artifact_id"]
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    release_id = _release_id(artifact_id, source_sha256)
    releases_dir = Path(releases_dir)
    try:
        releases_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        _private_directory(releases_dir)
    except OSError as exc:
        raise ReleaseError(f"cannot create release store: {exc.strerror or exc}") from None
    target = releases_dir / release_id
    with _release_lock(releases_dir):
        if target.exists():
            return load(releases_dir, release_id)
        staging = private_mkdir(releases_dir / f".{release_id}-{uuid.uuid4().hex}.tmp")
        try:
            engine_source = Path(artifact["engine_path"])
            engine_relative = Path("engine") / (engine_source.name or "strata-engine")
            engine_target = staging / engine_relative
            _copy(engine_source, engine_target, executable=True)
            for relative, source in artifacts.runtime_files(root):
                _copy(source, staging / relative)
            captured = artifacts.capture(_config_bytes(source_bytes, engine_target), root=staging)
            if captured["artifact_id"] != artifact_id:
                raise ReleaseError("materialized release content does not match its artifact")
            config_bytes = _config_bytes(source_bytes, target / engine_relative)
            atomic.write_bytes(staging / CONFIG, config_bytes)
            atomic.write_bytes(staging / LAUNCHER, LAUNCHER_BYTES)
            manifest = {
                "schema_version": 1,
                "release_id": release_id,
                "artifact_id": artifact_id,
                "source_sha256": source_sha256,
                "engine": engine_relative.as_posix(),
                "config": CONFIG,
                "launcher": LAUNCHER,
                "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
                "config_size": len(config_bytes),
                "engine_sha256": captured["engine_sha256"],
                "engine_size": captured["engine_size"],
                "runtime_file_count": captured["runtime_file_count"],
                "runtime_sha256": captured["runtime_sha256"],
                "launcher_sha256": hashlib.sha256(LAUNCHER_BYTES).hexdigest(),
                "launcher_size": len(LAUNCHER_BYTES),
            }
            atomic.write_bytes(staging / MANIFEST, _manifest_bytes(manifest))
            _sync_tree(staging)
            try:
                os.rename(staging, target)
            except FileExistsError:
                _remove_staging(staging, releases_dir)
            _sync_directory(releases_dir)
            return load(releases_dir, release_id)
        except BaseException:
            _remove_staging(staging, releases_dir)
            raise


def validate(releases_dir: Path, release_id: str) -> dict:
    """Read and verify one immutable release without trusting manifest paths or hashes."""
    releases_dir = Path(releases_dir)
    if not isinstance(release_id, str) or not ID.fullmatch(release_id):
        raise ReleaseError("release ID is invalid")
    _private_directory(releases_dir)
    root = releases_dir / release_id
    _private_directory(root)
    manifest_path = root / MANIFEST
    details = _private_file(manifest_path, maximum=MAX_MANIFEST)
    if details.st_size <= 0:
        raise ReleaseError("release manifest is empty")
    try:
        record = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise ReleaseError("release manifest is unreadable") from None
    if not isinstance(record, dict) or set(record) != RELEASE_KEYS or \
            record.get("schema_version") != 1 or record.get("release_id") != release_id:
        raise ReleaseError("release manifest has an unsupported shape")
    artifact_id = record.get("artifact_id")
    source_sha256 = record.get("source_sha256")
    if not isinstance(artifact_id, str) or not ID.fullmatch(artifact_id) or \
            not isinstance(source_sha256, str) or not ID.fullmatch(source_sha256) or \
            release_id != _release_id(artifact_id, source_sha256):
        raise ReleaseError("release identity does not match its manifest")
    engine_relative = _safe_relative(record.get("engine"), "engine")
    config_relative = _safe_relative(record.get("config"), "config")
    launcher_relative = _safe_relative(record.get("launcher"), "launcher")
    if config_relative.as_posix() != CONFIG or launcher_relative.as_posix() != LAUNCHER:
        raise ReleaseError("release launch paths are invalid")
    config_path = root / config_relative
    config_details = _private_file(config_path, maximum=MAX_CONFIG)
    if config_details.st_size != record.get("config_size") or \
            not isinstance(record.get("config_sha256"), str) or \
            _digest(config_path) != (config_details.st_size, record["config_sha256"]):
        raise ReleaseError("release config does not match its manifest")
    config_bytes = config_path.read_bytes()
    try:
        captured = artifacts.capture(config_bytes, root=root)
    except (OSError, artifacts.ArtifactError) as exc:
        raise ReleaseError(f"release artifact cannot be verified: {exc}") from None
    expected = {
        "artifact_id": captured["artifact_id"],
        "engine_sha256": captured["engine_sha256"],
        "engine_size": captured["engine_size"],
        "runtime_file_count": captured["runtime_file_count"],
        "runtime_sha256": captured["runtime_sha256"],
    }
    if any(record.get(key) != value for key, value in expected.items()):
        raise ReleaseError("release content does not match its manifest")
    engine_path = root / engine_relative
    if Path(captured["engine_path"]) != engine_path.resolve(strict=True):
        raise ReleaseError("release engine path does not match its config")
    _private_file(engine_path, maximum=record["engine_size"], executable=True)
    launcher_path = root / launcher_relative
    launcher_details = _private_file(launcher_path, maximum=len(LAUNCHER_BYTES) + 1)
    if launcher_details.st_size != record.get("launcher_size") or \
            launcher_details.st_size != len(LAUNCHER_BYTES) or \
            record.get("launcher_sha256") != hashlib.sha256(LAUNCHER_BYTES).hexdigest() or \
            _digest(launcher_path) != (len(LAUNCHER_BYTES), record["launcher_sha256"]):
        raise ReleaseError("release launcher does not match")
    return {
        "release_id": release_id,
        "artifact_id": artifact_id,
        "source_sha256": source_sha256,
        "root": str(root),
        "manifest_path": str(manifest_path),
        "config_path": str(config_path),
        "config_bytes": config_bytes,
        "engine_path": str(engine_path),
        "launcher_path": str(launcher_path),
    }


def load(releases_dir: Path, release_id: str) -> dict:
    """Load and verify an existing immutable release."""
    return validate(Path(releases_dir), release_id)
