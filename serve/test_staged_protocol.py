"""Strict protocol tests for the batch / staged-read path (STRATA_MP_STAGED_READ).

`serve/test_staged_wedge.py` checks the S0c wedge: an engine that dies during BREAD must not leave `mp_staged`
set.  These cases go further: the wire fields of BREAD / BRDY / BADOPT / BCANCEL, the heartbeat type the
consumer of `generate_batched` accepts, the cleanup of every failure path (`mp_staged`, `wait_lens`, the next
admission), and the admission rules while a read is staged (one at a time, slot-wait timeout).

The engine behind the tests is a strict fake: it validates what the server sends and answers `ERR <what>` on
a bad field, so a request that succeeds proves the server's lines were well formed.  The fake also speaks the
engine's side of the protocol (`BRDY <n> <max_new> <cont>`, `BADM <slot> 1`, `BCANCELLED`) and can inject the
failure the case needs.

The staged-read feature ships as the mp-w3 server patch.  On a tree whose serve/server.py is the plain base it
is absent and the staged cases skip, as test_staged_wedge does.  The gate is stricter than `"BREAD " in
source`: all of the pieces the protocol needs must be there (BRDY parsing, mp_staged, BADOPT, BCANCEL drain).
The heartbeat cases do not depend on the staged feature at all (a PP progress line already reaches the
consumer as `None`), so they also run - and must pass - on the plain base.

Run: /tmp/strata-venv/bin/python -m pytest serve/test_staged_protocol.py -q
"""
import inspect
import os
import re
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from serve.frontend import ChatTemplate
from serve.server import ByteTokenizer, EngineDied, QueueWaitExpired, Service, StrataEngine
import serve.server as _server


def _engine_source() -> str:
    try:
        return inspect.getsource(_server.StrataEngine)
    except (OSError, TypeError):            # a frozen/zipped install: no feature detection possible -> skip
        return ""


# The staged read is more than the string "BREAD": the server also has to parse BRDY (and pass `n max_new cont`
# through), mark mp_staged, adopt with BADOPT, and cancel+drain BCANCELLED.  A tree with only one of those is
# half-patched; skip rather than fail there (the same rule as the other backend-gated tests).
_STAGED_PIECES = (
    'self._send(f"BREAD ',
    'line.startswith("BRDY ")',
    "self.mp_staged = True",
    'self._send(f"BADOPT ',
    'self._send("BCANCEL")',
    'self._drain_control("BCANCELLED"',
)
_HAS_STAGED_READ = all(piece in _engine_source() for piece in _STAGED_PIECES)


# A strict fake engine for the batch protocol (a trimmed copy of serve/test_parallel.py's FAKE_BATCH plus the
# staged lines).  Modes:
#   normal                 BREAD -> T + BRDY, BADOPT adopts, BCANCEL answers BCANCELLED; BGEN/GEN decode
#   die-on-bread           exit(1) the moment a well-formed BREAD arrives (the engine died during the read)
#   badopt-refuse          ERR BADOPT: slot busy and KEEP the staged read pending (the real engine's behaviour:
#                          its copy_to_slot failure path clears it, the `slot busy` guard does not); GEN/BGEN
#                          while a staged read is pending answer ERR, exactly as generate.cpp does
#   badopt-refuse-forgive  ERR BADOPT: slot busy but drop the staged read (so the server-side cleanup can be
#                          checked without the engine's own pending state in the way)
#   bcancel-silent         process BCANCEL (drop the staged read) but never print BCANCELLED
# A malformed BREAD/BADOPT/BCANCEL (field count, order, token list) is answered with `ERR <what>` and logged as
# PROTO_ERR; the cases assert no PROTO_ERR and no unexpected ERR.
FAKE = r'''import os, queue, sys, threading, time
args = sys.argv[1:]
def opt(name, default=None):
    return args[args.index(name) + 1] if name in args else default
slots = int(opt("--batch", "0"))
mode = opt("--mode", "normal")
log = open(opt("--log"), "a")
STEP = float(opt("--step", "0.02"))
PP = "--pp" in args          # print PP progress lines: the server turns each into a None heartbeat

def note(s):
    log.write(s + "\n")
    log.flush()

def proto_err(what):
    note("PROTO_ERR " + what)
    print("ERR " + what, flush=True)

lines, stop = queue.Queue(), threading.Event()
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

active = {}                    # slot -> [tokens left, max_new, produced]
slot_request = {}              # slot -> request_id echoed on its BDONE
stopped = set()                # slots the server BSTOPped
staged = {"on": False, "left": [], "max_new": 0, "request_id": ""}  # one staged read at a time
LONG = b"LONGREPLY"

def request_id_of(fields):
    return next((x.partition("=")[2] for x in fields if x.startswith("request_id=")), "")

def reply(ids):
    # a prompt with LONG streams a long reply (a fill request stays in its slot); every other prompt is short
    data = bytes(int(x) & 255 for x in ids)
    if LONG in data:
        return list(b"a long staged reply " * 20) + [257]
    return list(b"ok, done.") + [257]

def token_list(field):
    if not field:
        return None
    out = []
    for p in field.split(","):
        if not p.isdigit():
            return None
        out.append(int(p))
    return out or None

def window():
    for b in sorted(list(active)):
        rid = slot_request.get(b, "")
        rid_txt = f" request_id={rid}" if rid else ""
        if b in stopped:
            produced = active[b][2]
            note(f"BDONE_RAW BDONE {b} {produced} cancel 1.0{rid_txt}")
            print(f"BDONE {b} {produced} cancel 1.0{rid_txt}", flush=True)
            del active[b]
            slot_request.pop(b, None)
            stopped.discard(b)
            continue
        left, max_new, produced = active[b]
        t = left.pop(0) if left else 257
        produced += 1
        print(f"BT {b} {t}", flush=True)
        if t == 257 or produced >= max_new:
            note(f"BDONE_RAW BDONE {b} {produced} {'stop' if t == 257 else 'length'} 1.0{rid_txt}")
            print(f"BDONE {b} {produced} {'stop' if t == 257 else 'length'} 1.0{rid_txt}", flush=True)
            del active[b]
            slot_request.pop(b, None)
        else:
            active[b] = [left, max_new, produced]

while True:
    try:
        line = lines.get(timeout=STEP if active else None)
    except queue.Empty:
        line = ""
    if line is None or line == "QUIT":
        break
    if line == "STOP":
        continue
    if line.startswith("BSTOP "):
        note("BSTOP_RAW " + line)
        f = line.split()
        if len(f) != 2 or not f[1].isdigit():
            proto_err("BSTOP: expected exactly one slot")
            continue
        stopped.add(int(f[1]))
        continue
    if line.startswith("BREAD "):
        note("BREAD_RAW " + line)
        f = line.split()
        if len(f) < 3:
            proto_err("BREAD: fewer than three fields (max_new, keys, tokens)")
            continue
        if staged["on"]:
            proto_err("BREAD: staged read pending")
            continue
        if not f[1].isdigit() or int(f[1]) < 1:
            proto_err("BREAD: field 1 must be max_new as a positive integer")
            continue
        ids = token_list(f[-1])
        if ids is None:
            proto_err("BREAD: the last field must be a comma-separated token list")
            continue
        bad = [k for k in f[2:-1] if "=" not in k or k.startswith("=") or k.endswith("=")]
        if bad:
            proto_err("BREAD: the fields between max_new and the tokens must be key=value: " + repr(bad))
            continue
        mn = int(f[1])
        note(f"BREAD_OK max_new={mn} keys={len(f) - 3} prompt={len(ids)}")
        if mode == "die-on-bread":
            note("DIE_ON_BREAD")
            log.close()
            sys.exit(1)
        toks = reply(ids)
        cont = mn > 1 and len(toks) > 1 and toks[0] != 257
        print(f"T {toks[0]}", flush=True)
        staged.update(on=True, left=list(toks[1:]), max_new=mn, request_id=request_id_of(f[2:-1]))
        brdy = f"BRDY {len(ids)} {mn} {1 if cont else 0}"
        print(brdy, flush=True)
        note("BRDY_RAW " + brdy)
        note(f"BRDY_SENT prompt={len(ids)} max_new={mn} cont={1 if cont else 0}")
        continue
    if line.startswith("BADOPT "):
        note("BADOPT_RAW " + line)
        f = line.split()
        if len(f) != 2 or not f[1].isdigit():
            proto_err("BADOPT: expected exactly one integer slot")
            continue
        slot = int(f[1])
        if not (0 <= slot < slots):
            proto_err(f"BADOPT: slot {slot} out of range")
            continue
        if not staged["on"]:
            proto_err("BADOPT: nothing staged")
            continue
        if mode in ("badopt-refuse", "badopt-refuse-forgive"):
            note(f"BADOPT_REFUSED slot={slot}")
            print("ERR BADOPT: slot busy", flush=True)
            if mode == "badopt-refuse-forgive":
                staged["on"] = False
            continue
        if slot in active:
            note(f"BADOPT_REFUSED slot={slot}")
            print("ERR BADOPT: slot busy", flush=True)
            continue
        active[slot] = [list(staged["left"]), staged["max_new"], 1]
        slot_request[slot] = staged["request_id"]
        staged["on"] = False
        note(f"BADOPT_OK slot={slot}")
        print(f"BADM {slot} 1", flush=True)
        continue
    if line.startswith("BCANCEL"):
        note("BCANCEL_RAW " + line)
        if line != "BCANCEL":
            proto_err("BCANCEL: no arguments expected")
            continue
        was = staged["on"]
        staged["on"] = False
        note(f"BCANCEL_STATE was_staged={int(was)}")
        if mode == "bcancel-silent":
            note("BCANCEL_SILENT")
            continue
        print("BCANCELLED", flush=True)
        note("BCANCEL_ACK")
        continue
    if line.startswith("BGEN "):
        note("BGEN_RAW " + line)
        f = line.split()
        if len(f) < 4:
            proto_err("BGEN: too few fields")
            continue
        try:
            slot, mn = int(f[1]), int(f[2])
        except ValueError:
            proto_err("BGEN: slot and max_new must be integers")
            continue
        if not (0 <= slot < slots):
            proto_err(f"BGEN: slot {slot} out of range")
            continue
        ids = token_list(f[-1])
        if ids is None:
            proto_err("BGEN: the last field must be a comma-separated token list")
            continue
        if staged["on"] and mode == "badopt-refuse":
            proto_err("a staged read is pending; adopt or cancel it first")
            continue
        if slot in active:
            note("BGEN_BUSY")
            print("ERR BGEN: slot busy", flush=True)
            continue
        if PP:
            print(f"PP 1 {len(ids)} 0.2", flush=True)
            time.sleep(0.05)
            print(f"PP {len(ids)} {len(ids)} 0.2", flush=True)
        toks = reply(ids)
        print(f"T {toks[0]}", flush=True)
        active[slot] = [list(toks[1:]), mn, 1]
        slot_request[slot] = request_id_of(f[3:-1])
        print(f"BADM {slot} 1", flush=True)
        note(f"BGEN_OK slot={slot} max_new={mn} prompt={len(ids)}")
        continue
    if line.startswith("GEN "):
        note("GEN_RAW " + line)
        f = line.split()
        if len(f) < 3 or not f[1].isdigit():
            proto_err("GEN: field 1 must be max_new")
            continue
        ids = token_list(f[-1])
        if ids is None:
            proto_err("GEN: the last field must be a comma-separated token list")
            continue
        if staged["on"] and mode == "badopt-refuse":
            proto_err("a staged read is pending; adopt or cancel it first")
            continue
        if PP:
            print(f"PP 1 {len(ids)} 0.2", flush=True)
            time.sleep(0.05)
            print(f"PP {len(ids)} {len(ids)} 0.2", flush=True)
        stop.clear()
        toks = reply(ids)
        out = 0
        for t in toks:
            if stop.is_set():
                break
            print(f"T {t}", flush=True)
            out += 1
            time.sleep(STEP)
            if t == 257:
                break
        fin = "stop" if out and toks[out - 1] == 257 else ("cancel" if stop.is_set() else "length")
        rid = request_id_of(f[2:-1])
        rid_txt = f" request_id={rid}" if rid else ""
        print(f"DONE {out} 0 0 0 {fin} 0 0 0{rid_txt}", flush=True)
        note(f"GEN_DONE out={out} finish={fin}")
        continue
    if line:
        note("UNKNOWN " + line)
    else:
        window()                             # the engine's batch window: every active slot gets one token
    if not active:
        stop.clear()
'''


class FakeEngineCase(unittest.TestCase):
    """A StrataEngine + Service over the strict fake, with helpers for guarded requests and log assertions."""

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

    # ---- harness -------------------------------------------------------------------------------------------
    def engine(self, mode="normal", *extra: str) -> StrataEngine:
        e = StrataEngine(str(self.script), ["--batch", "2", "--mode", mode, "--log", str(self.log), *extra])
        self.addCleanup(e.close)
        return e

    def service(self, e: StrataEngine) -> Service:
        return Service(e, ByteTokenizer(), ChatTemplate(Path(__file__).parent / "chat_template.jinja"))

    def log_text(self) -> str:
        try:
            return self.log.read_text()
        except FileNotFoundError:
            return ""

    def log_count(self, marker: str) -> int:
        return sum(1 for line in self.log_text().splitlines() if marker in line)

    def wait_log(self, marker: str, timeout: float = 5.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if marker in self.log_text():
                return True
            time.sleep(0.02)
        return False

    def wait_until(self, pred, msg: str, timeout: float = 6.0, snapshot=None):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if pred():
                return True
            time.sleep(0.02)
        self.fail(msg + (f" (now: {snapshot()!r})" if snapshot is not None else ""))

    def start_run(self, svc: Service, ids, max_new: int = 200, sampling=None, request_id=None):
        box = {"result": None, "error": None}
        cancel = threading.Event()
        if request_id is not None:
            setattr(cancel, "_strata_request_id", request_id)
        done = threading.Event()

        def go():
            try:
                box["result"] = list(svc.run(list(ids), False, [], max_new, sampling or {}, cancel))
            except BaseException as exc:        # noqa: BLE001 - the case decides what is expected
                box["error"] = exc
            finally:
                done.set()
        t = threading.Thread(target=go, daemon=True)
        t.start()
        self.addCleanup(self._stop_thread, t, cancel)
        return {"thread": t, "cancel": cancel, "done": done, "box": box}

    @staticmethod
    def _stop_thread(t: threading.Thread, cancel: threading.Event):
        cancel.set()
        t.join(6.0)                              # daemon: a wedged one must not hold the whole run

    def run_with_timeout(self, svc: Service, ids, max_new: int = 8, timeout: float = 12.0):
        run = self.start_run(svc, ids, max_new)
        self.assertTrue(run["done"].wait(timeout), f"the request neither completed nor failed within {timeout} s")
        return run["box"]

    def fill_slots(self, svc: Service, n: int = 2):
        """Start n streaming requests and wait until every slot is busy (the staged path needs pick_slot None)."""
        threads, errors = [], []

        def stream(cancel, k):
            try:
                list(svc.run(list(f"LONGREPLY {k}".encode()), False, [], 400, {}, cancel))
            except BaseException as exc:        # noqa: BLE001 - the case decides what is expected
                errors.append(exc)

        for k in range(n):
            cancel = threading.Event()
            t = threading.Thread(target=stream, args=(cancel, k), daemon=True)
            threads.append((t, cancel))
            self.addCleanup(self._stop_thread, t, cancel)
            t.start()
        end = time.monotonic() + 8.0
        while time.monotonic() < end:
            if sum(1 for b in svc.engine.slot_busy if b) >= n:
                time.sleep(0.1)                 # still busy 100 ms later: a finished fill would free a slot
                if sum(1 for b in svc.engine.slot_busy if b) >= n:
                    self.assertFalse(errors, f"a fill request died: {errors[0] if errors else ''}")
                    return threads
            time.sleep(0.02)
        self.fail(f"only {sum(1 for b in svc.engine.slot_busy if b)} of {n} slots became busy")

    def assert_clean(self, e: StrataEngine, timeout: float = 6.0):
        """The failure-path contract: the staged mark, the admission queue and the control lines are free."""
        self.wait_until(lambda: e.mp_staged is None, "mp_staged must be cleared after a failed staged read",
                        timeout, snapshot=lambda: e.mp_staged)
        self.wait_until(lambda: list(e.wait_lens) == [], "wait_lens must be empty after a failed staged read",
                        timeout, snapshot=lambda: list(e.wait_lens))
        self.wait_until(lambda: e.waiting == 0, "the control-line wait count must return to 0", timeout,
                        snapshot=lambda: e.waiting)
        self.wait_until(lambda: not e.ctl.locked(), "the control lines must be given back", timeout)

    def assert_request_ok(self, box, label: str):
        self.assertIsNone(box.get("error"), f"{label} failed: {box.get('error')!r}")
        self.assertTrue(box.get("result"), f"{label} must stream something")
        self.assertEqual(box["result"][-1][0], "done", f"{label} must end with a done event: {box['result'][-1]!r}")

    def start_staged(self, svc: Service, ids, max_new: int = 200, sampling=None, request_id=None):
        """Start a request that must take the staged path (BREAD), and leave the low threshold before returning."""
        before = self.log_count("BREAD_RAW")
        with mock.patch.dict(os.environ, {"STRATA_MP_STAGED_READ_MIN_TOKENS": "1"}):
            run = self.start_run(svc, ids, max_new, sampling, request_id)
            self.wait_until(lambda: self.log_count("BREAD_RAW") > before or run["done"].is_set(),
                            "the request must take the staged path (send BREAD)", 10.0)
        return run


class HeartbeatTypes(FakeEngineCase):
    """The consumer of generate_batched accepts token ids and None only (never a tuple such as ("ping", None)).

    These do not need the staged feature: a PP progress line already reaches the consumer as None on the base
    tree, so this class runs (and must pass) on both the base and the mp-w3 tree."""

    def setUp(self):
        super().setUp()
        self.env = mock.patch.dict(os.environ, {"STRATA_MP_STAGED_READ": "0", "STRATA_BATCH_STAGED_READ": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_generate_batched_yields_only_tokens_and_none(self):
        e = self.engine("normal", "--pp")
        svc = self.service(e)
        fills = self.fill_slots(svc, 2)           # both slots busy: the request below must take the batch read
        # free one slot a moment later, so the request waits in the admission loop and then reads (BGEN + PP)
        threading.Timer(0.3, fills[0][1].set).start()
        cancel = threading.Event()
        items = list(e.generate(list(b"a heartbeat in the batch read"), 8, {}, cancel))
        self.assertTrue(items, "the request must produce something")
        bad = [x for x in items if x is not None and not (isinstance(x, int) and not isinstance(x, bool))]
        self.assertFalse(bad, "every element of generate_batched must be a token id (int) or None; got "
                              f"{[(type(x).__name__, x) for x in bad[:4]]!r}")
        self.assertTrue(any(x is None for x in items),
                        "the PP progress lines must reach the consumer as None heartbeats; got only tokens: "
                        f"{items[:12]!r}")
        self.assertTrue(any(isinstance(x, int) for x in items), "the request must still yield its tokens")

    def test_service_turns_the_heartbeat_into_ping_not_a_bad_token(self):
        e = self.engine("normal", "--pp")
        svc = self.service(e)
        fills = self.fill_slots(svc, 2)
        threading.Timer(0.3, fills[0][1].set).start()
        events = list(svc.run(list(b"a heartbeat through the service"), False, [], 8, {}, threading.Event()))
        self.assertTrue(events, "the request must produce events")
        self.assertTrue(all(isinstance(ev, tuple) and len(ev) == 2 for ev in events),
                        f"every service event is a (kind, value) pair, got {events[:4]!r}")
        kinds = [ev[0] for ev in events]
        self.assertIn("ping", kinds, "the heartbeat must surface as a ping event, not as a token")
        self.assertEqual(kinds[-1], "done", f"the request must end with done, got {events[-1]!r}")
        self.assertNotIn("error", kinds, "a heartbeat must never end the request as an error")


@unittest.skipUnless(_HAS_STAGED_READ, "serve/server.py has no complete staged read (BRDY/BADOPT/BCANCEL): "
                                      "apply the mp-w3 server patch")
class StagedProtocol(FakeEngineCase):
    def setUp(self):
        super().setUp()
        self.env = mock.patch.dict(os.environ, {"STRATA_MP_STAGED_READ": "1",
                                                "STRATA_BATCH_STAGED_READ": "1",
                                                "STRATA_MP_STAGED_READ_MIN_TOKENS": "4096"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def bread_lines(self):
        return [line[len("BREAD_RAW "):] for line in self.log_text().splitlines() if line.startswith("BREAD_RAW ")]

    def assert_no_protocol_error(self):
        self.assertNotIn("PROTO_ERR", self.log_text(),
                         "the strict fake refused a line the server sent: " +
                         "\n".join(x for x in self.log_text().splitlines() if "PROTO_ERR" in x))
        bad = [x for x in self.log_text().splitlines()
               if x.startswith("ERR ") or x == "ERR" or x.startswith("BAD ")]
        self.assertFalse(bad, f"the engine answered the server with an error: {bad[:4]!r}")

    # ---- 1. wire fields --------------------------------------------------------------------------------------
    def test_bread_fields_and_brdy_cont_adopt(self):
        """BREAD is `<max_new> <key=value...> <tokens...>`; BRDY is `<n> <max_new> <cont>`, cont=1 waits and adopts."""
        e = self.engine("normal")
        svc = self.service(e)
        fills = self.fill_slots(svc)
        ids = list(b"staged protocol fields")
        run = self.start_staged(svc, ids, max_new=8,
                                sampling={"temperature": 0.7, "top_p": 0.95}, request_id="stage123")
        # BRDY reached the server and was parsed as ("brdy", n, max_new, cont) while the read waits for a slot
        self.wait_until(lambda: self.log_count("BRDY_SENT") == 1, "the engine must answer BRDY", 10.0)
        self.wait_until(lambda: e._ctl_result is not None and e._ctl_result[0] == "brdy",
                        "the server must parse the BRDY line", 5.0)
        self.assertEqual(e._ctl_result, ("brdy", len(ids), 8, True),
                         "BRDY must be `<n> <max_new> <cont>` with cont=1 for a read that can still continue; "
                         f"the server parsed it as {e._ctl_result!r}")
        brdy = [line[len("BRDY_RAW "):] for line in self.log_text().splitlines()
                if line.startswith("BRDY_RAW ")]
        self.assertEqual(len(brdy), 1, f"exactly one BRDY expected, got {brdy!r}")
        parts = brdy[0].split()
        self.assertEqual(len(parts), 4, f"BRDY is exactly `BRDY <n> <max_new> <cont>`: {brdy[0]!r}")
        self.assertEqual([int(x) for x in parts[1:]], [len(ids), 8, 1],
                         f"BRDY's fields are the read length, max_new and cont=1: {brdy[0]!r}")
        # what the server sent, checked independently of the fake's own validator
        bread = self.bread_lines()
        self.assertEqual(len(bread), 1, f"exactly one BREAD expected, got {bread!r}")
        f = bread[0].split()
        self.assertEqual(f[0], "BREAD", f"the line must start with BREAD: {bread[0]!r}")
        self.assertEqual(f[1], "8", f"BREAD's second field is max_new: {bread[0]!r}")
        self.assertEqual([int(x) for x in f[-1].split(",")], ids,
                         f"BREAD's last field is the prompt tokens, in order: {bread[0]!r}")
        keys = f[2:-1]
        self.assertTrue(keys, f"the request's sampling must travel as key=value fields: {bread[0]!r}")
        self.assertTrue(all(re.fullmatch(r"[a-z_]+=[^=\s]+", k) for k in keys),
                        f"every field between max_new and the tokens is key=value: {keys!r}")
        self.assertIn("temperature=0.7", keys, f"the request's temperature is sent: {keys!r}")
        self.assertIn("top_p=0.95", keys, f"the request's top_p is sent: {keys!r}")
        self.assertIn("request_id=stage123", keys, f"the request's correlation ID is sent: {keys!r}")
        # free the slots: the staged read must be adopted with BADOPT <slot>, not read again
        for _, cancel in fills:
            cancel.set()
        self.wait_until(lambda: self.log_count("BADOPT_OK") == 1,
                        "with a slot free the staged read must be adopted (BADOPT)", 15.0)
        self.assertTrue(run["done"].wait(15.0), "the adopted request must finish")
        self.assert_request_ok(run["box"], "the staged request")
        self.assert_no_protocol_error()
        self.assertTrue(any(line.startswith("BDONE_RAW ") and line.endswith("request_id=stage123")
                            for line in self.log_text().splitlines()),
                        "the adopted slot's BDONE must echo the staged request's ID")
        badopt = [line for line in self.log_text().splitlines() if line.startswith("BADOPT_RAW ")]
        self.assertEqual(len(badopt), 1, f"exactly one BADOPT expected, got {badopt!r}")
        m = re.fullmatch(r"BADOPT_RAW BADOPT (\d+)", badopt[0])
        self.assertIsNotNone(m, f"BADOPT must be `BADOPT <slot>`: {badopt[0]!r}")
        self.assertTrue(0 <= int(m.group(1)) < e.batch, f"the adopted slot must be a real slot: {badopt[0]!r}")
        self.assertNotIn("BCANCEL_RAW", self.log_text(),
                         "cont=1 means the read is adopted: it must not be cancelled")
        self.assert_clean(e)

    def test_brdy_cont_zero_cancels_and_waits_for_the_answer(self):
        """cont=0 (max_new 1) means there is nothing to adopt: the server sends BCANCEL and consumes BCANCELLED."""
        e = self.engine("normal")
        svc = self.service(e)
        ids = list(b"one token only")
        started = time.monotonic()
        run = self.start_staged(svc, ids, max_new=1)
        self.assertTrue(run["done"].wait(10.0), "a max_new=1 staged read must finish")
        elapsed = time.monotonic() - started
        self.assert_request_ok(run["box"], "the one-token staged request")
        self.assertEqual(run["box"]["result"][-1][1].get("completion_tokens"), 1,
                         f"max_new=1 must produce one token: {run['box']['result'][-1]!r}")
        self.assertEqual(e._ctl_result, ("brdy", len(ids), 1, False),
                         "BRDY's cont field must be 0 when there is nothing to adopt; "
                         f"the server parsed it as {e._ctl_result!r}")
        brdy = [line[len("BRDY_RAW "):] for line in self.log_text().splitlines()
                if line.startswith("BRDY_RAW ")]
        self.assertEqual(brdy, [f"BRDY {len(ids)} 1 0"],
                         f"BRDY is `<n> <max_new> 0` for a read that cannot continue: {brdy!r}")
        self.assertIn("BCANCEL_RAW BCANCEL", self.log_text(),
                      "cont=0 must send BCANCEL with no arguments")
        self.assertIn("BCANCEL_ACK", self.log_text(), "the fake must have answered BCANCELLED")
        self.assert_no_protocol_error()
        self.assertLess(elapsed, 8.0,
                        "the server must consume BCANCELLED instead of waiting for a 300 s drain")
        self.assert_clean(e)

    # ---- 2. heartbeat on the staged wait ---------------------------------------------------------------------
    def test_staged_wait_heartbeat_is_none(self):
        """While a read is staged and waits for a slot, generate_batched yields None (never ("ping", None))."""
        e = self.engine("normal")
        svc = self.service(e)
        fills = self.fill_slots(svc)
        ids = list(b"staged wait heartbeat")
        before = self.log_count("BREAD_RAW")
        box, items = {"error": None}, []
        cancel = threading.Event()

        def go():
            try:
                items.extend(e.generate(ids, 200, {}, cancel))
            except BaseException as exc:        # noqa: BLE001 - reported below
                box["error"] = exc
        with mock.patch.dict(os.environ, {"STRATA_MP_STAGED_READ_MIN_TOKENS": "1"}):
            t = threading.Thread(target=go, daemon=True)
            t.start()
            self.addCleanup(self._stop_thread, t, cancel)
            self.wait_until(lambda: self.log_count("BREAD_RAW") > before,
                            "the request must take the staged path", 10.0)
        self.wait_until(lambda: self.log_count("BRDY_SENT") == 1 and e.mp_staged is True,
                        "the read must be staged and waiting for a slot", 10.0)
        time.sleep(1.3)                          # at least one turn of the staged-wait loop yields a heartbeat
        for _, c in fills:
            c.set()
        t.join(15.0)
        self.assertFalse(t.is_alive(), "the staged request must finish once a slot frees")
        self.assertIsNone(box["error"], f"the staged request failed: {box['error']!r}")
        bad = [x for x in items if x is not None and not (isinstance(x, int) and not isinstance(x, bool))]
        self.assertFalse(bad, "the consumer only accepts token ids and None; got "
                              f"{[(type(x).__name__, x) for x in bad[:4]]!r}")
        self.assertTrue(any(x is None for x in items),
                        f"the slot wait must produce None heartbeats; got {items!r}")
        self.assert_clean(e)

    # ---- 3. failure-path cleanup -----------------------------------------------------------------------------
    def test_engine_dying_during_bread_clears_state_and_next_request_is_admitted(self):
        e = self.engine("die-on-bread")
        svc = self.service(e)
        self.fill_slots(svc)
        run = self.start_staged(svc, list(b"this read dies in the engine"), max_new=64)
        self.assertTrue(run["done"].wait(10.0), "the request must end when the engine dies during BREAD")
        self.assertIsInstance(run["box"]["error"], EngineDied,
                              f"a dead engine must surface as EngineDied, got {run['box']['error']!r}")
        self.assert_clean(e)
        # the acceptance: after the restart the next request is admitted normally (no wedge)
        box = self.run_with_timeout(svc, list(b"next request after the death"), max_new=8)
        self.assert_request_ok(box, "the request after the restart")
        self.assertIsNone(e.mp_staged, "the restarted engine must not inherit a staged mark")

    def test_badopt_refused_clears_state_and_next_request_is_admitted(self):
        """The engine refuses the adoption (`ERR BADOPT: slot busy`): the server cancels the staged read, retries the
        request and the caller gets its answer - a momentarily busy slot is not the conversation's fault (#1994)."""
        e = self.engine("badopt-refuse-forgive")
        svc = self.service(e)
        fills = self.fill_slots(svc)
        run = self.start_staged(svc, list(b"a read whose slot is already busy"), max_new=64)
        self.wait_until(lambda: self.log_count("BRDY_SENT") == 1 and e.mp_staged is True,
                        "the read must be staged and waiting for a slot", 10.0)
        fills[0][1].set()                        # free a slot: the server adopts, the engine refuses once
        self.wait_until(lambda: self.log_count("BADOPT_REFUSED") == 1,
                        "the server must try to adopt the staged read", 15.0)
        self.assertTrue(self.wait_log("BCANCEL_RAW", timeout=5.0),
                        "after ERR BADOPT: slot busy the server must cancel the staged read")
        self.assertTrue(run["done"].wait(15.0), "a refused adoption must not leave the request hanging")
        err = run["box"]["error"]
        self.assertIsNone(err, f"a refused adoption is retried, not failed: got {err!r}")
        self.assert_clean(e)
        for _, c in fills:
            c.set()
        self.wait_until(lambda: not any(e.slot_busy), "the slots must free after the fills are stopped", 10.0)
        box = self.run_with_timeout(svc, list(b"next request after the refusal"), max_new=8)
        self.assert_request_ok(box, "the request after a refused adoption")

    def test_badopt_refusal_must_bcancel_the_engines_staged_read(self):
        """`ERR BADOPT: slot busy` leaves the engine's staged read pending, so the server must cancel it
        (serve/server.py, the BADOPT refusal branch); otherwise every later read is refused with
        "a staged read is pending"."""
        e = self.engine("badopt-refuse")
        svc = self.service(e)
        fills = self.fill_slots(svc)
        run = self.start_staged(svc, list(b"a read refused by its slot"), max_new=64)
        self.wait_until(lambda: self.log_count("BRDY_SENT") == 1, "the read must be staged", 10.0)
        fills[0][1].set()
        self.wait_until(lambda: self.log_count("BADOPT_REFUSED") == 1,
                        "the server must try to adopt the staged read", 15.0)
        self.assertTrue(self.wait_log("BCANCEL_RAW", timeout=5.0),
                        "after ERR BADOPT: slot busy the server must send BCANCEL")
        self.assertTrue(run["done"].wait(15.0), "the retried request must finish")
        self.assert_clean(e)

    def test_after_badopt_refusal_the_next_request_is_admitted(self):
        """The same fix seen from the next request: with the staged read cancelled, its GEN/BGEN is not met with
        `ERR a staged read is pending; adopt or cancel it first`."""
        e = self.engine("badopt-refuse")
        svc = self.service(e)
        fills = self.fill_slots(svc)
        run = self.start_staged(svc, list(b"a read refused by its slot"), max_new=64)
        self.wait_until(lambda: self.log_count("BRDY_SENT") == 1, "the read must be staged", 10.0)
        fills[0][1].set()
        self.wait_until(lambda: self.log_count("BADOPT_REFUSED") == 1,
                        "the server must try to adopt the staged read", 15.0)
        self.assertTrue(run["done"].wait(10.0), "a refused adoption must end the request")
        self.assert_clean(e)
        for _, c in fills:
            c.set()
        self.wait_until(lambda: not any(e.slot_busy), "the slots must free after the fills are stopped", 10.0)
        box = self.run_with_timeout(svc, list(b"the next request must be admitted"), max_new=8)
        self.assert_request_ok(box, "the request after a refused adoption (server gap: engine read left pending)")

    def test_bcancel_without_an_answer_still_clears_state(self):
        """BCANCEL that is never acknowledged (the engine processed it, the answer was lost) must not wedge."""
        e = self.engine("bcancel-silent")
        svc = self.service(e)
        # the drain's 300 s default would make this case five minutes: shorten only that wait, not the logic
        drain = e._drain_control

        def fast_drain(until, timeout=300.0, born=None, cancel=None):
            return drain(until, timeout=1.0, born=born, cancel=cancel)
        e._drain_control = fast_drain
        run = self.start_staged(svc, list(b"a read whose cancel is not answered"), max_new=1)
        self.assertTrue(run["done"].wait(10.0), "the request must end although BCANCELLED never came")
        self.assert_request_ok(run["box"], "the request with an unanswered BCANCEL")
        self.assertIn("BCANCEL_RAW BCANCEL", self.log_text(), "the server must send BCANCEL")
        self.assertIn("BCANCEL_SILENT", self.log_text(), "the engine must have processed (and not answered) it")
        self.assertNotIn("BCANCEL_ACK", self.log_text(), "this mode never answers BCANCELLED")
        self.assert_clean(e)
        box = self.run_with_timeout(svc, list(b"next request after the unanswered cancel"), max_new=8)
        self.assert_request_ok(box, "the request after an unanswered BCANCEL")

    # ---- 4. concurrency and the slot-wait timeout ------------------------------------------------------------
    def test_third_request_stages_and_two_staged_reads_never_coexist(self):
        e = self.engine("normal")
        svc = self.service(e)
        fills = self.fill_slots(svc)
        a = self.start_staged(svc, list(b"the third request stages"), max_new=200)
        self.wait_until(lambda: self.log_count("BRDY_SENT") == 1 and e.mp_staged is True,
                        "the third request must be staged while both slots are busy", 10.0)
        # a fourth request while the read is staged: it must wait for that one, not stage a second read
        with mock.patch.dict(os.environ, {"STRATA_MP_STAGED_READ_MIN_TOKENS": "1"}):
            b = self.start_run(svc, list(b"a second request that must not stage"), max_new=200)
            time.sleep(0.6)
            self.assertEqual(self.log_count("BREAD_RAW"), 1,
                             "two staged reads must never coexist: the second request sent BREAD while "
                             f"{e.mp_staged!r} was set (lines: {self.bread_lines()!r})")
            self.assertFalse(b["done"].is_set(), "the second request must wait while the first read is staged")
            b["cancel"].set()
        self.assertTrue(b["done"].wait(10.0), "the waiting request must end when cancelled")
        a["cancel"].set()                        # cancel the staged read: the server BCANCELs it
        self.assertTrue(a["done"].wait(10.0), "the cancelled staged request must end")
        self.assertIn("BCANCEL_RAW", self.log_text(),
                      "a staged read cancelled while waiting for a slot must be cancelled in the engine")
        self.assert_clean(e)
        for _, cancel in fills:
            cancel.set()
        self.wait_until(lambda: not any(e.slot_busy), "the slots must free after the fills are stopped", 10.0)
        box = self.run_with_timeout(svc, list(b"next request after the staged cancellation"), max_new=8)
        self.assert_request_ok(box, "the request after the staged cancellation")

    def test_slot_wait_timeout_clears_the_staged_read(self):
        e = self.engine("normal")
        svc = self.service(e)
        fills = self.fill_slots(svc)
        svc.queue_timeout_s = 1.2                # the admission deadline: the staged read waits for a slot
        try:
            run = self.start_staged(svc, list(b"a staged read that waits too long"), max_new=200)
            self.assertTrue(run["done"].wait(10.0), "the staged request must end when its slot wait expires")
        finally:
            svc.queue_timeout_s = None
        err = run["box"]["error"]
        self.assertIsInstance(err, QueueWaitExpired,
                              f"a slot wait past the deadline must be QueueWaitExpired, got {err!r}")
        self.assert_clean(e)
        self.assertIn("BCANCEL_RAW", self.log_text(),
                      "the timed-out staged read must be cancelled in the engine")
        for _, cancel in fills:
            cancel.set()
        self.wait_until(lambda: not any(e.slot_busy), "the slots must free after the fills are stopped", 10.0)
        box = self.run_with_timeout(svc, list(b"next request after the wait timeout"), max_new=8)
        self.assert_request_ok(box, "the request after the slot-wait timeout")


if __name__ == "__main__":
    unittest.main()
