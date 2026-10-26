"""Atomic file replacement for Linux records."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Union

PathLike = Union[str, "os.PathLike[str]"]


def replace(src: PathLike, dst: PathLike) -> None:
    os.replace(src, dst)


def write_bytes(path: PathLike, data: bytes) -> None:
    """Durably replace `path` without exposing a partial record or a temporary file on failure."""
    target = Path(path)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{target.stem}-", suffix=".tmp", dir=target.parent)
    open_descriptor = descriptor
    try:
        stream = os.fdopen(open_descriptor, "wb")
        open_descriptor = -1
        with stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        replace(temp_name, target)
        try:
            parent_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        except OSError:
            # The replacement already succeeded; do not report a failed save after changing the record.
            pass
    finally:
        if open_descriptor >= 0:
            os.close(open_descriptor)
        Path(temp_name).unlink(missing_ok=True)


def write_text(path: PathLike, text: str, *, encoding: str = "utf-8") -> None:
    """Write `text` to `path` through a neighbouring temporary file, so a reader never sees half a record."""
    write_bytes(path, text.encode(encoding))
