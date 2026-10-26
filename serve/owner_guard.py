"""Parent-loss guard for a server launched by the local runtime host.

The inherited pipe has one writer, held by the host.  EOF means the host exited,
including after SIGKILL.  The server then signals its dedicated process group,
which also reaches the engine and other children.
"""

from __future__ import annotations

import os
import signal
import stat
import threading
import time


def start(fd: int, on_loss=None, grace: float = 60.0) -> None:
    if fd < 0 or os.getpgrp() != os.getpid():
        raise ValueError("--owner-fd requires a dedicated POSIX server process group")
    if not stat.S_ISFIFO(os.fstat(fd).st_mode):
        raise ValueError("--owner-fd must be an inherited pipe")

    def watch() -> None:
        try:
            while os.read(fd, 1):
                pass
        except OSError:
            pass                       # a broken guard must never authorize another process to manage us
        finally:
            os.close(fd)
        # This is an emergency path.  Signal the whole dedicated group so an engine already spawned during model
        # loading cannot be orphaned.  SIGTERM is the clean stop (the server drains and closes its engine); if the
        # group is still there after `grace` seconds, SIGKILL ends whatever is left.
        pgid = os.getpgrp()
        try:
            os.killpg(pgid, signal.SIGTERM)
            deadline = time.monotonic() + grace
            while time.monotonic() < deadline:
                time.sleep(0.5)
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass

    threading.Thread(target=watch, name="strata-owner-guard", daemon=True).start()
