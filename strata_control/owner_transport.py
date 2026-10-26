"""Owner transport for the Linux server runtime.

The owner and its clients share an owner-only ``AF_UNIX`` stream socket in the
private runtime directory.  The directory is mode 0700 and the socket is mode
0600, so the filesystem is the access-control boundary.
"""

from __future__ import annotations

import errno
import hashlib
import os
import socket
import stat
from pathlib import Path

MAX_MESSAGE = 64 * 1024


class TransportError(OSError):
    """The owner channel cannot be trusted or reached."""


def address_for(runtime_dir: Path, name: str) -> str:
    """Return a stable Unix socket path that fits Linux's ``sun_path`` limit."""
    digest = hashlib.sha256(f"{runtime_dir}\0{name}".encode()).hexdigest()[:16]
    candidate = runtime_dir / f"{digest}.sock"
    if len(os.fsencode(candidate)) <= 90:
        return str(candidate)
    return f"/tmp/strata-{os.getuid()}-{digest}.sock"


class Listener:
    """An accepting endpoint for one owner's private Unix socket."""

    def __init__(self, address: str, *, timeout: float = 1.0) -> None:
        self.requested = address
        self.address = address
        self._sock: socket.socket | None = None
        self._timeout = timeout
        self._bound = False

    def bind(self) -> str:
        path = Path(self.requested)
        if path.exists():
            # The caller established that this is a stale owner socket before binding.
            path.unlink()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(str(path))
            os.chmod(path, 0o600)
            sock.listen(5)
            sock.settimeout(self._timeout)
        except BaseException:
            sock.close()
            raise
        self._sock = sock
        self._bound = True
        return self.address

    def accept(self) -> socket.socket | None:
        if self._sock is None:
            raise TransportError("the listener is not bound")
        try:
            conn, _ = self._sock.accept()
        except socket.timeout:
            return None
        except OSError as exc:
            if exc.errno in (errno.EINTR, errno.EAGAIN):
                return None
            raise
        return conn

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
        if self._bound:
            try:
                Path(self.requested).unlink()
            except OSError:
                pass
        self._bound = False

    def __enter__(self) -> "Listener":
        self.bind()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def connect(address: str, *, timeout: float = 5.0) -> socket.socket:
    """Connect to an owner's private Unix socket."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(address)
    except OSError:
        sock.close()
        raise
    return sock


def check_state_file(path: Path) -> None:
    """Refuse a state file that another account can replace or read."""
    details = path.lstat()
    if stat.S_ISLNK(details.st_mode):
        raise TransportError("the runtime state file is a symlink")
    if not stat.S_ISREG(details.st_mode):
        raise TransportError("the runtime state file is not a regular file")
    if details.st_size > 16 * 1024:
        raise TransportError("the runtime state file is implausibly large")
    if details.st_uid != os.getuid():
        raise TransportError("the runtime state file is owned by another user")
    if details.st_mode & 0o077:
        raise TransportError("the runtime state file is readable by other users")
