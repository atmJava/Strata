"""Owner-only files for the Linux server runtime."""

from __future__ import annotations

import os
from pathlib import Path


def restrict_to_current_user(path: str | os.PathLike, *, directory: bool | None = None) -> None:
    """Set a file to 0600 or a directory to 0700."""
    target = os.fspath(path)
    if directory is None:
        directory = os.path.isdir(target)
    os.chmod(target, 0o700 if directory else 0o600)


def is_private(path: str | os.PathLike) -> bool:
    """Return whether group and other users have no access to ``path``."""
    details = os.lstat(path)
    return details.st_uid == os.getuid() and not details.st_mode & 0o077


def private_mkdir(path: str | os.PathLike, *, parents: bool = False, exist_ok: bool = True) -> Path:
    """Create a directory with mode 0700."""
    target = Path(path)
    target.mkdir(mode=0o700, parents=parents, exist_ok=exist_ok)
    return target
