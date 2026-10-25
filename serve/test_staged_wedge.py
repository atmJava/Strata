"""S0c: an engine that dies during a staged read (BREAD) must not leave mp_staged set.

The staged-read admission (STRATA_MP_STAGED_READ) marks the engine "staged" while a long prompt is read
without a slot (BREAD -> BRDY -> BADOPT).  Every later admission whose request is not the staged one waits
while `mp_staged` is set.  If the engine dies while a read is staged and the mark survives, every later
admission waits forever - the WORK_QUEUE S0c wedge ("kill the engine before/during BREAD; after the
restart the next request must be admitted normally").  These tests drive the real StrataEngine/Service
against a fake engine speaking the batch protocol plus the staged lines (BREAD/BRDY/BADOPT/BCANCEL).
"""
import inspect
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from serve.frontend import ChatTemplate
from serve.server import ByteTokenizer, Service, StrataEngine
import serve.server as _server

# the staged read (BREAD -> BRDY -> BADOPT) ships as the mp-w3 server patch: on a tree whose serve/server.py is the
# plain base the feature is absent and these cases skip instead of failing (same rule as the other backend-gated tests)
_HAS_STAGED_READ = 'f"BREAD ' in inspect.getsource(_server.StrataEngine.generate_batched)

# A trimmed FAKE_BATCH (serve/test_parallel.py) plus the staged lines.  Modes:
#   die-on-bread  exit the process the moment BREAD arrives (the engine died mid-read)
#   brdy          answer BRDY (read done, no slot), adopt on BADOPT, answer BCANCELLED to BCANCEL
FAKE = r'''import os, queue, sys, threading, time
args = sys.argv[1:]
slots = int(args[args.index("--batch") + 1]) if "--batch" in args else 0
mode = args[args.index("--mode") + 1] if "--mode" in args else "brdy"
log = open(args[args.index("--log") + 1], "a")
STEP = 0.02
lines = queue.Queue()
stop = threading.Event()
def reader():
    for l in sys.stdin:
        l = l.strip()
        if l == "STOP":
            stop.set()
        lines.put(l)
    lines.put(None)
threading.Thread(target=reader, daemon=True).start()
print(f"INFO engine=0.1.49 batch_slots={slots}", flush=True)
print("READY 4096 stop", flush=True)
active = {}          # slot -> [tokens left, max_new, produced]
staged = None        # (tokens left, max_new) of a BREAD-read prompt
def reply(ids):
    # "LONG" prompts stream a long reply (no EOS for 40 tokens), so a second request arrives while the
    # first is still solo and the server hands it to a slot; every other prompt answers and stops at once
    loud = b"LONG" in bytes(int(x) & 255 for x in ids)
    return list(b"a much longer reply for the slot hand-off test") + [257] if loud else list(b"ok, done.") + [257]
def window():
    for b in sorted(list(active)):
        left, max_new, produced = active[b]
        t = left.pop(0) if left else 257
        produced += 1
        print(f"BT {b} {t}", flush=True)
        if t == 257 or produced >= max_new:
            print(f"BDONE {b} {produced} stop 1.0", flush=True)
            del active[b]
        else:
            active[b] = [left, max_new, produced]
while True:
    try:
        line = lines.get(timeout=STEP if active else None)
    except queue.Empty:
        line = ""
    if line is None or line == "QUIT":
        break
    if line.startswith("BREAD "):
        f = line.split()
        max_new = int(f[1])
        ids = [int(x) for x in f[-1].split(",")]
        log.write("BREAD\n"); log.flush()
        if mode == "die-on-bread":
            sys.exit(1)                       # the engine died while the read was staged
        toks = reply(ids)                     # the read is done; the first token was already produced
        print(f"T {toks[0]}", flush=True)
        staged = (toks[1:], max_new)
        print(f"BRDY {len(ids)} {max_new} {1 if max_new > 1 else 0}", flush=True)
        continue
    if line.startswith("BADOPT "):
        b = int(line.split()[1])
        log.write("BADOPT\n"); log.flush()
        left, max_new = staged
        staged = None
        active[b] = [list(left), max_new, 0]
        print(f"BADM {b} 1", flush=True)
        continue
    if line == "BCANCEL":
        staged = None
        print("BCANCELLED", flush=True)
        continue
    if line.startswith(("GEN ", "BGEN ")):
        f = line.split()
        slot = int(f[1]) if f[0] == "BGEN" else None
        max_new = int(f[2] if f[0] == "BGEN" else f[1])
        ids = f[-1].split(",")
        toks = reply(ids)
        if slot is not None:
            log.write("BGEN\n"); log.flush()
            active[slot] = [toks[1:], max_new, 1]
            print(f"T {toks[0]}", flush=True)
            print(f"BADM {slot} 1", flush=True)
        else:
            stop.clear()
            out = 0
            for t in toks:                    # solo: stream to max_new, EOS, or a STOP (the slot hand-off)
                if stop.is_set():
                    break
                print(f"T {t}", flush=True)
                out += 1
                if t == 257:
                    break
                time.sleep(STEP)
            fin = "stop" if out and toks[out - 1] == 257 else ("cancel" if stop.is_set() else "length")
            print(f"DONE {out} 0 0 0 {fin} 0 0 0", flush=True)
        continue
    if line:
        continue
    window()
'''


@unittest.skipUnless(_HAS_STAGED_READ, "serve/server.py has no staged read (BREAD): apply the mp-w3 server patch")
class StagedWedge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        py = self.root / "native.py"
        if os.name == "nt":
            py.write_text(FAKE)
            self.script = self.root / "native.cmd"
            self.script.write_text(f'@"{sys.executable}" "{py}" %*\r\n')
        else:
            self.script = py
            py.write_text("#!" + sys.executable + "\n" + FAKE)
            py.chmod(0o700)
        self.log = self.root / "engine.log"
        # staging on, but only for prompts over 4096 tokens by default: the requests that fill the slots must not
        # take the staged path (only the tests' long request does, via stage_long_prompts())
        self.env = mock.patch.dict(os.environ, {"STRATA_MP_STAGED_READ": "1",
                                                "STRATA_BATCH_STAGED_READ": "1",
                                                "STRATA_MP_STAGED_READ_MIN_TOKENS": "4096"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def stage_long_prompts(self):
        """Let prompts of any length stage (the threshold is read per request, when the request starts)."""
        p = mock.patch.dict(os.environ, {"STRATA_MP_STAGED_READ_MIN_TOKENS": "1"})
        p.start()
        self.addCleanup(p.stop)

    def engine(self, mode):
        e = StrataEngine(str(self.script), ["--batch", "2", "--mode", mode, "--log", str(self.log)])
        self.addCleanup(e.close)
        return e

    def service(self, e):
        return Service(e, ByteTokenizer(), ChatTemplate(Path(__file__).parent / "chat_template.jinja"))

    def wait_log(self, marker, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                if marker in self.log.read_text():
                    return True
            except FileNotFoundError:
                pass
            time.sleep(0.02)
        return False

    def fill_slots(self, svc, n=2):
        """Start n streaming requests and wait until every slot is busy (the staged path needs pick_slot None)."""
        threads = []

        died = []

        def stream(cancel, k):
            try:
                list(svc.run(list(f"LONGREPLY {k}".encode()), True, [], 400, {}, cancel))
            except _server.EngineDied as exc:      # the engine may die under a fill request; the test decides below
                died.append(exc)

        for k in range(n):
            cancel = threading.Event()
            t = threading.Thread(target=stream, args=(cancel, k), daemon=True)
            threads.append((t, cancel))
            t.start()
        # both slots busy, and still busy 100 ms later: a fill request that has already finished would hand the
        # staged request an empty slot, so the test would silently stop exercising the staged path
        end = time.monotonic() + 5.0
        while time.monotonic() < end:
            if sum(1 for b in self.engine_of(svc).slot_busy if b) >= n:
                time.sleep(0.1)
                if sum(1 for b in self.engine_of(svc).slot_busy if b) >= n:
                    self.assertFalse(died, f"a fill request died: {died[0] if died else ''}")
                    return threads
            time.sleep(0.02)
        self.assertTrue(self.wait_log("BGEN"), "the arriving requests must move the solo one into a slot")
        self.fail(f"only {sum(1 for b in self.engine_of(svc).slot_busy if b)} of {n} slots became busy")
        return threads

    @staticmethod
    def engine_of(svc):
        return svc.engine

    def run_with_timeout(self, svc, ids, timeout=8.0):
        """svc.run in a guarded thread: a wedged admission waits forever, a healthy one answers."""
        box = {}
        cancel = threading.Event()

        def go():
            try:
                box["result"] = list(svc.run(ids, False, [], 8, {}, cancel))
            except BaseException as e:      # noqa: BLE001 - the caller decides what is expected
                box["error"] = e
        t = threading.Thread(target=go, daemon=True)
        t.start()
        t.join(timeout)
        self.assertFalse(t.is_alive(), "the request neither completed nor failed within "
                                       f"{timeout} s - the admission is wedged")
        return box

    def test_engine_dying_during_bread_clears_mp_staged_and_next_request_is_admitted(self):
        e = self.engine("die-on-bread")
        svc = self.service(e)
        for t, cancel in self.fill_slots(svc):
            pass
        # a third request with every slot busy takes the staged path: it sends BREAD, the fake dies on it
        self.stage_long_prompts()
        box = self.run_with_timeout(svc, list(b"long prompt that must stage"))
        self.assertIsInstance(box.get("error"), Exception, "the staged request must report the dead engine")
        self.assertIsNone(e.mp_staged, "S0c: a dead engine must not leave mp_staged set")
        # the acceptance: after the restart the next request is admitted normally (no wedge)
        box = self.run_with_timeout(svc, list(b"next request"))
        self.assertNotIn("error", box, f"the request after the restart failed: {box.get('error')!r}")
        self.assertTrue(box.get("result"), "the request after the restart must stream something")

    def test_request_after_a_dead_engine_is_admitted(self):
        # a dead engine is restarted by the next request instead of wedging it (the staged-state cleanup itself is
        # covered by test_engine_dying_during_bread_..., which is the only one that can stage before the death)
        e = self.engine("brdy")
        e.close()
        svc = self.service(e)
        box = self.run_with_timeout(svc, list(b"anything"))
        self.assertNotIn("error", box, f"the request that restarts the engine failed: {box.get('error')!r}")
        self.assertTrue(box.get("result"), "the restarting request must stream something")
        self.assertIsNone(e.mp_staged, "nothing staged happened, and nothing may be left behind")

    def test_staged_read_completes_and_adopts_when_a_slot_frees(self):
        e = self.engine("brdy")
        svc = self.service(e)
        threads = self.fill_slots(svc)
        self.stage_long_prompts()
        # with every slot busy the long request reads into the engine first (BREAD) and waits there
        box = {}
        done = threading.Event()

        def long_request():
            try:
                box["result"] = list(svc.run(list(b"long staged prompt"), False, [], 8, {}, threading.Event()))
            except BaseException as err:      # noqa: BLE001 - reported below
                box["error"] = err
            done.set()
        threading.Thread(target=long_request, daemon=True).start()
        self.assertTrue(self.wait_log("BREAD", timeout=10.0), "the long request must have sent BREAD")
        self.assertIsNotNone(e.mp_staged, "while the read is staged the mark must be set")
        # free the slots: the streaming requests end, then the staged read must be adopted (BADOPT)
        for t, cancel in threads:
            cancel.set()
        self.assertTrue(self.wait_log("BADOPT", timeout=15.0),
                        "after a slot frees the staged read must be adopted, not wait forever")
        self.assertTrue(done.wait(15.0), "the adopted request must finish")
        self.assertIsNone(e.mp_staged, "an adopted read must clear mp_staged")
        self.assertNotIn("error", box, f"the adopted request failed: {box.get('error')!r}")


if __name__ == "__main__":
    unittest.main()
