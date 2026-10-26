"""Atomic file replacement for Linux records."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Union

PathLike = Union[str, "os.PathLike[str]"]


def replace(src: PathLike, dst: PathLike) -> None:
    os.replace(src, dst)


def _sync_parent(path: Path) -> None:
    try:
        parent_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    except OSError:
        # The record already reached its committed name; a parent sync failure must not report the save as failed.
        pass


def fdopen_owned(descriptor: int, mode: str, **kwargs):
    """Wrap a descriptor and close it if ownership transfer fails."""
    try:
        return os.fdopen(descriptor, mode, **kwargs)
    except BaseException:
        os.close(descriptor)
        raise


def _write_owned_descriptor(descriptor: int, data: bytes) -> None:
    stream = fdopen_owned(descriptor, "wb")
    with stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def write_bytes(path: PathLike, data: bytes) -> None:
    """Durably replace `path` without exposing a partial record or a temporary file on failure."""
    target = Path(path)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{target.stem}-", suffix=".tmp", dir=target.parent)
    try:
        _write_owned_descriptor(descriptor, data)
        replace(temp_name, target)
        _sync_parent(target)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def write_new(path: PathLike, data: bytes) -> None:
    """Create a new owner-only record; on any failure, no path or descriptor is left behind."""
    target = Path(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(target, flags, 0o600)
    try:
        _write_owned_descriptor(descriptor, data)
        _sync_parent(target)
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def write_text(path: PathLike, text: str, *, encoding: str = "utf-8") -> None:
    """Write `text` to `path` through a neighbouring temporary file, so a reader never sees half a record."""
    write_bytes(path, text.encode(encoding))
