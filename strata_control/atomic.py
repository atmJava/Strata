"""Atomic file replacement for Linux records."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Union

PathLike = Union[str, "os.PathLike[str]"]


def replace(src: PathLike, dst: PathLike) -> None:
    os.replace(src, dst)


def write_text(path: PathLike, text: str, *, encoding: str = "utf-8") -> None:
    """Write `text` to `path` through a neighbouring temporary file, so a reader never sees half a record."""
    target = Path(path)
    tmp = target.with_suffix(target.suffix + ".tmp") if target.suffix else target.with_name(target.name + ".tmp")
    tmp.write_text(text, encoding=encoding)
    replace(tmp, target)
