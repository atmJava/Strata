"""serve/test_admit_order.py - ADMIT_SHORT_FIRST: new requests with less work left take the control lines first
(StrataEngine._take_control / _admit_next; no process, no GPU).

    python -m unittest serve.test_admit_order -v
"""
from __future__ import annotations

import ast
import inspect
import sys
import textwrap
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import serve.server as server  # noqa: E402
from serve.server import StrataEngine  # noqa: E402


def engine_without_process(n=2):
    """A StrataEngine with the admission state of a started one and no process: alive() is `e.up`."""
    e = StrataEngine("missing-executable", [], lazy=True)
    e.batch = n
    e.slot_busy = [False] * n
    e.slot_held = [[] for _ in range(n)]
    e.slot_claims, e.slot_releasing, e._orphan_seen = set(), set(), {}
    e.slot_cv = threading.Condition()
    e.waiting, e.admitting, e.wait_lens, e.ctl_epoch = 0, 0, [], 0
    e.ctl = threading.Lock()
    e.gen = 1
    e.up = True
    e.alive = lambda: e.up
    return e


class AdmitOrderTest(unittest.TestCase):
    def setUp(self):
        self.e = engine_without_process(16)
        self.order, self.cancels = [], []
        self.order_lock = threading.Lock()

    def tearDown(self):
        for c in self.cancels:
            c.set()

    def take(self, name, prompt_or_len, **kw):
        """A request waiting for the control lines; once it has them it notes its name and gives them back."""
        if isinstance(prompt_or_len, list):
            prompt, plen = prompt_or_len, len(prompt_or_len)
            kw.setdefault("reused", self.e._known_prompt_reuse(prompt))
        else:
            plen = prompt_or_len
        c = threading.Event()
        self.cancels.append(c)

        def go():
            gen = self.e._take_control(c, plen, **kw)
            try:
                while True:
                    next(gen)
            except StopIteration as s:
                if s.value:
                    with self.order_lock:
                        self.order.append(name)
                    time.sleep(0.05)                # its read
                    self.e.ctl.release()
        threading.Thread(target=go, daemon=True).start()

    def until(self, cond, s=5.0):
        end = time.time() + s
        while time.time() < end and not cond():
            time.sleep(0.01)
        self.assertTrue(cond())

    def queue(self, *reqs):
        """While a long read holds the control lines, the requests arrive in this order; then the read ends."""
        self.assertTrue(self.e.ctl.acquire(timeout=1))
        for name, plen in reqs:
            self.take(name, plen, new=True)
            self.until(lambda n=len(self.order) + sum(1 for _ in self.e.wait_lens): self.e.waiting >= 1)
            time.sleep(0.02)                        # (distinct arrival times)
        self.until(lambda: self.e.waiting == len(reqs))
        self.e.ctl.release()
        self.until(lambda: len(self.order) == len(reqs), s=15.0)
        return self.order

    def test_short_first_puts_the_chat_before_the_agent_reads(self):
        with mock.patch.object(server, "ADMIT_SHORT_FIRST", 8192), mock.patch.object(server, "ADMIT_MAX_WAIT_S", 60.0):
            order = self.queue(("agent1", 120000), ("agent2", 190000), ("chat", 500), ("agent3", 150000))
        self.assertEqual(order[0], "chat")
        self.assertEqual(order[1:], ["agent1", "agent2", "agent3"])      # arrival order among the long ones

    def test_cached_history_ranks_by_the_tokens_left_to_read(self):
        """A long prompt that has nearly all of its history cached does little work and goes before a fresh read."""
        held = list(range(1, 120_001))
        self.e.slot_held[0] = held
        chat = held + [500_001, 500_002]
        agent = list(range(600_000, 612_000))
        with mock.patch.object(server, "ADMIT_SHORT_FIRST", 8192), mock.patch.object(server, "ADMIT_MAX_WAIT_S", 60.0):
            order = self.queue(("agent", agent), ("chat", chat))
        self.assertEqual(order, ["chat", "agent"])

    def test_yielding_compares_the_waiters_tokens_left_to_read(self):
        self.e.wait_lens = [[120_000, time.monotonic(), True, 1_000]]
        self.assertTrue(self.e._shorter_waiting(5_000))
        self.assertFalse(self.e._shorter_waiting(1_999))

    def test_work_never_falls_below_zero_or_exceeds_the_prompt(self):
        self.assertEqual(self.e._tokens_left_to_read(10_000, 7_500), 2_500)
        self.assertEqual(self.e._tokens_left_to_read(10_000, 10_000), 0)
        self.assertEqual(self.e._tokens_left_to_read(10_000, 12_000), 0)
        self.assertEqual(self.e._tokens_left_to_read(10_000, -1), 10_000)

    def test_known_prompt_work_follows_the_slots(self):
        """A re-admission asks again: a slot that now holds more of the prompt lowers the work."""
        prompt = list(range(10_000))
        self.e.slot_held[0] = prompt[:9_000]
        self.assertEqual(self.e._known_prompt_work(prompt), (9_000, 1_000))
        self.e.slot_held[0] = prompt[:9_500]
        self.assertEqual(self.e._known_prompt_work(prompt), (9_500, 500))

    def test_a_completed_staged_read_has_no_work_left(self):
        """BRDY holds the read in the engine, not in a slot; waiting to adopt it must rank as zero work."""
        prompt = list(range(10_000))
        self.e.slot_held[0] = prompt[:1_000]
        self.assertEqual(self.e._known_prompt_work(prompt), (1_000, 9_000))
        self.assertEqual(self.e._known_prompt_work(prompt, read_complete=True), (10_000, 0))

    def test_ranking_uses_reuse_a_free_slot_can_provide_now(self):
        """A busy slot's prefix cannot make a request short while an empty slot is available."""
        prompt = list(range(10_000))
        self.e.slot_held[0] = prompt[:9_000]
        self.e.slot_busy[0] = True
        self.e.slot_held[1] = []
        self.assertEqual(self.e._known_prompt_work(prompt), (0, 10_000))
        self.e.slot_busy[0] = False
        self.assertEqual(self.e._known_prompt_work(prompt), (9_000, 1_000))

    def test_every_prompt_control_wait_reports_known_reuse(self):
        """Solo promotion, staged wait and BYIELD re-entry must not fall back to the full prompt length."""
        tree = ast.parse(textwrap.dedent(inspect.getsource(StrataEngine.generate_batched)))
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_take_control"
        ]
        self.assertTrue(calls)
        missing = [ast.unparse(call) for call in calls
                   if not any(keyword.arg == "reused" for keyword in call.keywords)]
        self.assertEqual(missing, [], "every prompt-control wait must report its known slot reuse")

    def test_the_short_work_boundary_is_inclusive(self):
        now = time.monotonic()
        at_boundary = [5_000, now, True, server.ADMIT_SHORT_FIRST]
        over_boundary = [5_000, now - 1.0, True, server.ADMIT_SHORT_FIRST + 1]
        self.e.wait_lens = [over_boundary, at_boundary]
        with self.e.slot_cv:
            self.assertIs(self.e._admit_next(), at_boundary)

    def test_a_long_request_that_waited_too_long_goes_first(self):
        with mock.patch.object(server, "ADMIT_SHORT_FIRST", 8192), mock.patch.object(server, "ADMIT_MAX_WAIT_S", 0.2):
            self.assertTrue(self.e.ctl.acquire(timeout=1))
            self.take("agent", 120000, new=True)
            self.until(lambda: self.e.waiting == 1)
            time.sleep(0.4)                         # past STRATA_ADMIT_MAX_WAIT_S
            self.take("chat", 500, new=True)
            self.until(lambda: self.e.waiting == 2)
            self.e.ctl.release()
            self.until(lambda: len(self.order) == 2, s=15.0)
        self.assertEqual(self.order, ["agent", "chat"])

    def test_the_deployment_default(self):
        """2048 tokens, as a constant: the order is what this box is for, not an opt-in (AGENTS.md)."""
        self.assertEqual(server.ADMIT_SHORT_FIRST, 2048)

    def test_without_the_order_both_get_their_turn(self):
        with mock.patch.object(server, "ADMIT_SHORT_FIRST", 0):
            order = self.queue(("agent1", 120000), ("chat", 500))
        self.assertEqual(sorted(order), ["agent1", "chat"])            # both get their turn, in any order

    def test_a_continuation_is_not_held_back(self):
        """A request already admitted once (new=False: promoted from the solo path) is not part of the order."""
        with mock.patch.object(server, "ADMIT_SHORT_FIRST", 8192), mock.patch.object(server, "ADMIT_MAX_WAIT_S", 60.0):
            self.assertTrue(self.e.ctl.acquire(timeout=1))
            self.take("chat", 500, new=True)
            self.take("continuation", 190000)
            self.until(lambda: self.e.waiting == 2)
            self.e.ctl.release()
            self.until(lambda: len(self.order) == 2, s=15.0)
        self.assertEqual(sorted(self.order), ["chat", "continuation"])


if __name__ == "__main__":
    unittest.main()
