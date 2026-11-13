"""serve/test_admit_order.py - ADMIT_SHORT_FIRST: new requests with less work left take the control lines first
(StrataEngine._take_control / _admit_next; no process, no GPU).

    python -m unittest serve.test_admit_order -v
"""
from __future__ import annotations

import ast
import inspect
import queue
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
    e.slot_order, e.slot_group = list(range(n)), [0] * n
    e.slot_groups, e.slot_gs, e.slot_used = 1, 0, [0.0] * n
    e.ctl = threading.Lock()
    e.gen = 1
    e.up = True
    e.alive = lambda: e.up
    return e


class CountingCondition(threading.Condition):
    def __init__(self):
        super().__init__()
        self.notifications = 0

    def notify_all(self):
        self.notifications += 1
        super().notify_all()


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
            prompt = prompt_or_len
        else:
            prompt = [0] * prompt_or_len
        c = threading.Event()
        self.cancels.append(c)

        def go():
            gen = self.e._take_control(c, prompt, **kw)
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
        prompt = [1] * 120_000
        self.e.slot_held[0] = prompt[:119_000]
        self.e.wait_lens = [[len(prompt), time.monotonic(), True, 1_000, prompt, False]]
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

    def test_stable_slot_ownership_reuses_waiting_estimates(self):
        """A waiter keeps its work estimate until a slot is taken, released or a test rewrites the busy list."""
        now = time.monotonic()
        self.e.wait_lens = [
            [10_000, now, True, 10_000, list(range(10_000)), False],
            [20_000, now + 0.01, True, 20_000, list(range(20_000)), False],
        ]
        with mock.patch.object(self.e, "_known_prompt_work_locked",
                               wraps=self.e._known_prompt_work_locked) as work:
            with self.e.slot_cv:
                self.e._refresh_waiting_work_locked()
                self.e._refresh_waiting_work_locked()
            self.assertEqual(work.call_count, 2)

            with self.e.slot_cv:
                self.e._set_slot_busy_locked(0, True)
                self.e._refresh_waiting_work_locked()
            self.assertEqual(work.call_count, 4)

            self.e.slot_busy[1] = True
            with self.e.slot_cv:
                self.e._refresh_waiting_work_locked()
            self.assertEqual(work.call_count, 6)

            self.e._replace_slot_held(0, [1, 2, 3])
            with self.e.slot_cv:
                self.e._refresh_waiting_work_locked()
            self.assertEqual(work.call_count, 8)

    def test_a_pre_restart_release_cannot_touch_the_new_slot_list(self):
        """A delayed BDONE or park callback owns the list it started with, not the restarted engine's slots."""
        old = self.e.slot_busy
        old[0] = True
        self.e.slot_busy = [False] * self.e.batch
        epoch = self.e._slot_epoch
        with self.e.slot_cv:
            self.e._set_slot_busy_locked(0, False, old)
        self.assertEqual(old, [False] * self.e.batch)
        self.assertEqual(self.e.slot_busy, [False] * self.e.batch)
        self.assertEqual(self.e._slot_epoch, epoch)

    def test_prefix_sampling_keeps_exact_reuse_semantics(self):
        """Sampled rejection is only an early exit; every sampled match still verifies the whole prefix."""
        prompt = list(range(10_000))
        held = prompt[:9_000]
        self.e.slot_held[0] = held
        self.assertEqual(self.e._known_prompt_work(prompt), (9_000, 1_000))

        changed = held.copy()
        changed[-1] += 1
        self.e.slot_held[0] = changed
        self.assertEqual(self.e._known_prompt_work(prompt), (0, 10_000))

        changed = held.copy()
        changed[-2] += 1
        self.e.slot_held[0] = changed
        self.assertEqual(self.e._known_prompt_work(prompt), (0, 10_000))

    def test_turn_checkpoint_cache_tracks_replaced_slots(self):
        """Reusing a slot must not reuse the checkpoint position of the conversation it replaced."""
        prompt = list(range(10_000))
        first = prompt[:9_000]
        first[-1] = self.e.TURN_ID
        second = prompt[:2_000]
        second[1_000] = self.e.TURN_ID
        self.e.slot_held[0] = first

        self.assertEqual(self.e._held_prefix(0, prompt + [20_000]), len(first) - 1)
        self.e.slot_held[0] = second
        self.assertEqual(self.e._held_prefix(0, prompt + [20_000]), 1_000)
        self.assertEqual(self.e._held_turn(0, second), 1_000)

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

    def test_ranking_uses_the_prefix_pick_slot_can_actually_take(self):
        """Group mode skips a short prefix when choosing a slot; admission must not count that prefix as saved work."""
        prompt = list(range(2_300))
        self.e.slot_groups = 2
        self.e.slot_held[0] = prompt[:self.e.SLOT_PREFIX_MIN - 1]
        self.assertEqual(self.e.pick_slot(prompt), 1)
        self.assertEqual(self.e._known_prompt_work(prompt), (0, len(prompt)))

        self.e.slot_held[0] = prompt[:self.e.SLOT_PREFIX_MIN]
        self.assertEqual(self.e.pick_slot(prompt), 0)
        self.assertEqual(self.e._known_prompt_work(prompt),
                         (self.e.SLOT_PREFIX_MIN, len(prompt) - self.e.SLOT_PREFIX_MIN))

    def test_all_busy_uses_the_best_parked_prefix(self):
        """When no slot is free, the eventual release can still preserve the best parked conversation prefix."""
        prompt = list(range(10_000))
        self.e.slot_held[0] = prompt[:9_000]
        self.e.slot_busy[:] = [True] * self.e.batch
        self.assertEqual(self.e._known_prompt_work(prompt), (9_000, 1_000))

    def test_admission_refreshes_work_after_a_free_slot_is_taken(self):
        """A waiter that looked short through a free cached slot must not keep that rank after it is taken."""
        cached, fresh = [7] * 10_000, [8] * 1_500
        self.e.slot_held[0] = cached[:9_000]
        with mock.patch.object(server, "ADMIT_SHORT_FIRST", 2048), mock.patch.object(server, "ADMIT_MAX_WAIT_S", 60.0):
            self.assertTrue(self.e.ctl.acquire(timeout=1))
            self.take("cached", cached, new=True)
            self.until(lambda: self.e.waiting == 1)
            with self.e.slot_cv:
                self.assertEqual(self.e.wait_lens[0][3], 1_000)
                self.assertIs(self.e.wait_lens[0][4], cached)
                self.e.slot_busy[0] = True
            self.take("fresh", fresh, new=True)
            self.until(lambda: self.e.waiting == 2)
            self.e.ctl.release()
            self.until(lambda: len(self.order) == 2, s=15.0)
        self.assertEqual(self.order, ["fresh", "cached"])

    def test_yielding_refreshes_work_after_a_free_slot_is_taken(self):
        """BYIELD must compare fresh work too, or it yields control for a cached prefix no free slot can provide."""
        cached = [7] * 10_000
        self.e.slot_held[0] = cached[:9_000]
        self.e.wait_lens = [[len(cached), time.monotonic(), True, 1_000, cached, False]]
        self.assertTrue(self.e._shorter_waiting(5_000))
        with self.e.slot_cv:
            self.e.slot_busy[0] = True
        self.assertFalse(self.e._shorter_waiting(5_000))

    def test_a_completed_staged_read_stays_zero_work_while_the_queue_changes(self):
        """A staged read lives in the engine, so taking a slot must not restore its prompt-read work."""
        prompt = [9] * 10_000
        self.e.slot_held[0] = prompt[:9_000]
        staged_wait = [len(prompt), time.monotonic(), True, 0, prompt, True]
        fresh_wait = [1_500, time.monotonic() + 0.01, True, 1_500, [8] * 1_500, False]
        self.e.wait_lens = [staged_wait, fresh_wait]
        with self.e.slot_cv:
            self.e.slot_busy[0] = True
            self.assertIs(self.e._admit_next(), staged_wait)

    def test_every_prompt_control_wait_passes_the_prompt(self):
        """Every prompt wait passes its prompt so admission can refresh work while it waits."""
        tree = ast.parse(textwrap.dedent(inspect.getsource(StrataEngine.generate_batched)))
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_take_control"
        ]
        self.assertTrue(calls)
        missing = [ast.unparse(call) for call in calls
                   if not any(keyword.arg == "prompt" for keyword in call.keywords)]
        self.assertEqual(missing, [], "every prompt-control wait must let admission refresh its slot reuse")

    def test_cancelled_control_wait_wakes_the_next_request(self):
        """Removing a cancelled waiter changes who may go next, so it must notify the condition immediately."""
        e = engine_without_process()
        e.slot_cv = CountingCondition()
        cancel = threading.Event()
        cancel.set()
        self.assertTrue(e.ctl.acquire(timeout=1))
        try:
            wait = e._take_control(cancel, [1] * 100, new=True)
            with self.assertRaises(StopIteration) as stopped:
                next(wait)
            self.assertFalse(stopped.exception.value)
        finally:
            e.ctl.release()
        self.assertGreater(e.slot_cv.notifications, 0)

    def test_a_request_cancelled_before_an_idle_control_line_is_not_admitted(self):
        """Cancellation wins even when the control line becomes available without a wait: the caller must not
        send a prompt read after its client has gone."""
        e = engine_without_process()
        cancel = threading.Event()
        cancel.set()
        wait = e._take_control(cancel, [1] * 100, new=True)
        with self.assertRaises(StopIteration) as stopped:
            next(wait)
        self.assertFalse(stopped.exception.value)
        self.assertTrue(e.ctl.acquire(blocking=False), "a cancelled admission must not retain the control lines")
        e.ctl.release()

    def test_a_request_cancelled_while_claiming_a_slot_does_not_send_bgen(self):
        """A client can leave after it gets the control lines and while its slot is claimed. The claim must be
        released without reading the prompt into the engine or changing the untouched slot's LRU position."""
        e = engine_without_process(2)
        e.slot_busy[0], e.slot_claims = True, {0}
        e.slot_q = [queue.Queue(), queue.Queue()]
        e.slot_live, e.slot_releasing = [None, None], set()
        e.slot_used = [10.0, 20.0]
        e.mp_staged, e._yielded = None, None
        e.progress, e.progress_ms, e.reused = None, 0, 0
        cancel = threading.Event()
        original_pick = e.pick_slot

        def pick_then_cancel(prompt):
            slot = original_pick(prompt)
            cancel.set()
            return slot
        e.pick_slot = pick_then_cancel
        sends = []

        def send(line):
            sends.append(line)
            raise AssertionError(f"cancelled request sent {line!r}")
        e._send = send

        self.assertEqual(list(e.generate_batched([1] * 100, 8, {}, cancel)), [])
        self.assertEqual(sends, [])
        self.assertEqual(e.slot_busy, [True, False])
        self.assertEqual(e.slot_claims, {0})
        self.assertEqual(e.slot_used, [10.0, 20.0], "an untouched slot must keep its LRU position")
        self.assertTrue(e.ctl.acquire(blocking=False), "a cancelled admission must release the control lines")
        e.ctl.release()

    def test_the_short_work_boundary_is_inclusive(self):
        now = time.monotonic()
        prompt_at, prompt_over = [1] * 5_000, [2] * 5_000
        self.e.slot_held[0] = prompt_at[:len(prompt_at) - server.ADMIT_SHORT_FIRST]
        self.e.slot_held[1] = prompt_over[:len(prompt_over) - server.ADMIT_SHORT_FIRST - 1]
        at_boundary = [5_000, now, True, server.ADMIT_SHORT_FIRST, prompt_at, False]
        over_boundary = [5_000, now - 1.0, True, server.ADMIT_SHORT_FIRST + 1, prompt_over, False]
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
