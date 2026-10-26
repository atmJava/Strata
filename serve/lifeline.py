"""Tie every server child to the server's lifetime on Linux.

``PR_SET_PDEATHSIG`` is installed in the child before ``exec``.  The signal is
attached to the thread that forks, so all launches pass through one long-lived
anchor thread; otherwise a request thread ending would kill its child.
"""

from __future__ import annotations

import atexit
import concurrent.futures
import ctypes
import os
import queue
import signal
import subprocess
import threading
import time

from strata_control import procs

_REAL_POPEN = subprocess.Popen
PR_SET_PDEATHSIG = 1

_libc = ctypes.CDLL(None, use_errno=True)
_libc.prctl.argtypes = (
    ctypes.c_int,
    ctypes.c_ulong,
    ctypes.c_ulong,
    ctypes.c_ulong,
    ctypes.c_ulong,
)


class _Anchor:
    """One process-lifetime thread that performs every ``Popen``."""

    def __init__(self) -> None:
        self._jobs: queue.Queue = queue.Queue()
        self._thread = threading.Thread(
            target=self._run,
            name="strata-lifeline-anchor",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        while True:
            fn, future = self._jobs.get()
            try:
                future.set_result(fn())
            except BaseException as exc:  # noqa: BLE001 - handed to the caller
                future.set_exception(exc)

    def call(self, fn):
        if threading.current_thread() is self._thread:
            return fn()
        future: concurrent.futures.Future = concurrent.futures.Future()
        self._jobs.put((fn, future))
        return future.result()


_anchor: _Anchor | None = None
_anchor_lock = threading.Lock()


def _the_anchor() -> _Anchor:
    global _anchor
    with _anchor_lock:
        if _anchor is None:
            _anchor = _Anchor()
        return _anchor


def _die_with_parent(parent: int, then=None):
    """Return the child-side setup that asks Linux to SIGKILL it with its parent."""

    def run() -> None:
        if _libc.prctl(PR_SET_PDEATHSIG, int(signal.SIGKILL), 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "prctl(PR_SET_PDEATHSIG) failed")
        if os.getppid() != parent:
            os.kill(os.getpid(), signal.SIGKILL)
        if then is not None:
            then()

    return run


def mechanism() -> str:
    return "pdeathsig"


_children: set[subprocess.Popen] = set()
_children_lock = threading.Lock()


def end_children(grace: float = 3.0) -> None:
    with _children_lock:
        alive = [child for child in _children if child.poll() is None]
    for child in alive:
        try:
            child.terminate()
        except OSError:
            pass
    deadline = time.monotonic() + grace
    for child in alive:
        try:
            child.wait(timeout=max(0.0, deadline - time.monotonic()))
        except (subprocess.TimeoutExpired, OSError):
            try:
                child.kill()
            except OSError:
                pass


atexit.register(end_children)


def _after_exit(child: subprocess.Popen, token: str | None) -> None:
    child.wait()
    with _children_lock:
        _children.discard(child)
    if token is not None:
        procs.drop_record(child.pid, token)


ISOLATED_ROLES = ("engine", "vision")


def _own_group(then=None):
    def run() -> None:
        os.setpgid(0, 0)
        if then is not None:
            then()

    return run


def popen(args, role: str = "engine", **kw) -> subprocess.Popen:
    """Start a child that cannot outlive this server."""
    isolate = role in ISOLATED_ROLES
    if isolate and "start_new_session" not in kw:
        kw["preexec_fn"] = _own_group(kw.pop("preexec_fn", None))
    parent, then = os.getpid(), kw.pop("preexec_fn", None)
    kw["preexec_fn"] = _die_with_parent(parent, then)
    child = _the_anchor().call(lambda: subprocess.Popen(args, **kw))
    if isinstance(child, _REAL_POPEN):
        with _children_lock:
            _children.add(child)
    try:
        token = procs.write_record(
            child.pid,
            role,
            profile=os.environ.get("STRATA_RUNTIME_PROFILE") or None,
        )
        threading.Thread(
            target=_after_exit,
            args=(child, token),
            name="strata-lifeline-reaper",
            daemon=True,
        ).start()
    except Exception:  # noqa: BLE001 - ownership records are best-effort
        pass
    return child


def install_stop_handlers(request_stop, finished: threading.Event | None = None) -> None:
    """Turn SIGTERM and SIGHUP into the server's clean stop path."""

    def in_main(reason: str):
        def handler(signum, frame):
            request_stop(reason)

        return handler

    for name, reason in (("SIGTERM", "sigterm"), ("SIGHUP", "sighup")):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            if name == "SIGHUP" and signal.getsignal(sig) == signal.SIG_IGN:
                continue
            signal.signal(sig, in_main(reason))
        except (ValueError, OSError):
            pass
