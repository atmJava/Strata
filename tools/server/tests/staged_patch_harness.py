"""Build the staged-read server (patch applied) and a StrataEngine shaped for unit tests.

The base file is the **0.2.2** `serve/server.py` (the text production runs), kept under
`tools/server/patches/base-0.2.2/`.  It is copied to a temp directory and patched there, so the
patch itself is what these tests exercise.  Patching the repository's `serve/server.py` instead made
both tests fail with "Reversed (or previously applied) patch detected" once `681fd256` committed the
staged read: the patch then had nothing left to add (see `tools/server/patches/README.md`).
"""
import hashlib
import importlib.util
import queue
import shutil
import subprocess
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PATCH = ROOT / "tools" / "server" / "patches" / "mp-w3-server-0.2.2.patch"
BASE = ROOT / "tools" / "server" / "patches" / "base-0.2.2" / "serve" / "server.py"
# The tag `v0.2.2` text: the deployed server's md5 (aec97a89...), so a silent base swap fails loudly.
BASE_MD5 = "aec97a89386f5f90cf179932bbec4d4c"


class DeadPipe:
    def write(self, _data):
        raise BrokenPipeError("the engine is gone")

    def flush(self):
        pass


class DeadProc:
    """A process the server has not noticed yet: poll() still None (alive() true), the pipe broken."""

    stdin = DeadPipe()

    def poll(self):
        return None

    def wait(self, timeout=None):  # noqa: ARG002 - EngineDied asks for the exit code
        return 1


class TrackedLock:
    """A Lock that remembers whether its owner holds it, for "was this sent under the control lines?"."""

    def __init__(self):
        self._lock = threading.Lock()
        self.held = False

    def acquire(self, timeout=-1):
        ok = self._lock.acquire(timeout=timeout) if timeout != -1 else self._lock.acquire()
        if ok:
            self.held = True
        return ok

    def release(self):
        self.held = False
        self._lock.release()


def patched_module(tmp_path: Path):
    base = BASE.read_bytes()
    digest = hashlib.md5(base).hexdigest()
    assert digest == BASE_MD5, f"{BASE} is not the 0.2.2 base (md5 {digest}); see patches/README.md"
    target = tmp_path / "server.py"
    target.write_bytes(base)
    patch = PATCH.read_bytes()
    done = subprocess.run(["patch", str(target)], input=patch, capture_output=True, cwd=ROOT)
    assert done.returncode == 0, done.stdout.decode() + done.stderr.decode()
    spec = importlib.util.spec_from_file_location("staged_server", target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def new_engine(module, slots=4):
    """A StrataEngine with the state `generate_batched` reads, and control lines it can take at once."""
    engine = module.StrataEngine.__new__(module.StrataEngine)
    engine.slot_cv = threading.Condition()
    engine.slot_busy = [True] + [False] * (slots - 1)       # not alone: the staged path is the one to take
    engine.waiting = 0
    engine.gen = 1
    engine.ended = False
    engine.proc = DeadProc()
    engine.wlock = threading.Lock()
    engine.ctl = TrackedLock()
    engine.mp_staged = None
    engine.slot_claims = set()
    engine.slot_releasing = set()
    engine.batch = slots
    engine.slot_order = list(range(slots))
    engine.slot_group = [0] * slots
    engine.slot_groups = 1
    engine.slot_gs = 0
    engine.slot_q = [queue.Queue() for _ in range(slots)]
    engine.slot_held = [[] for _ in range(slots)]
    engine.slot_used = [0.0] * slots
    engine.slot_live = [None] * slots
    engine.admitting = 0
    engine.wait_lens = []
    engine.ctl_epoch = 0
    engine._ctl_erred = False
    engine.last = None
    engine.progress = engine.progress_ms = engine.reused = None
    engine._take_control = _take_control.__get__(engine, module.StrataEngine)
    engine._reap_orphan_slots = lambda: None
    return engine


def _take_control(self, _cancel, _plen, **_kw):
    self.ctl.acquire()
    self.ctl_epoch += 1
    if False:            # a generator: `yield from` expects one
        yield
    return True
