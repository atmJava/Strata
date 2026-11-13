"""tools/test_sim_client.py - the parts of sim_client that decide what it measures, without a server.

A change is judged by a repeatable simulation (AGENTS.md), so the simulation has to be checkable: the prompts are the
same on every run, a request carries the history up to its own question, and the verdict separates a healthy run from
a collapse.

    python -m unittest tools.test_sim_client -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sim_client  # noqa: E402


class Args:
    min_reuse = 0.9
    max_read = 8000
    min_decode_ratio = 0.7
    sessions = 5


def row(turn, prompt, reused, read, tok_s, session=0):
    return {"session": session, "turn": turn, "prompt": prompt, "reused": reused, "read": read,
            "tok_s": tok_s, "prefill_s": 1.0, "decode_s": 1.0, "first_s": 1.0, "wall_s": 2.0, "chars": 10}


class PromptText(unittest.TestCase):
    def test_the_text_is_the_same_on_every_run(self):
        self.assertEqual(sim_client.filler(500, 7), sim_client.filler(500, 7))

    def test_a_different_seed_is_different_text(self):
        self.assertNotEqual(sim_client.filler(500, 7), sim_client.filler(500, 8))

    def test_more_words_is_more_text(self):
        self.assertGreater(len(sim_client.filler(100, 1)), len(sim_client.filler(50, 1)))


class Conversation(unittest.TestCase):
    def test_one_turn_per_request(self):
        msgs = sim_client.conversation(0, 4000, 3)
        self.assertEqual(len(msgs), 3 + 2 * 3)
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[1]["role"], "user")
        self.assertEqual(len([m for m in msgs if m["role"] == "user"]), 1 + 3)

    def test_the_session_number_decides_the_history(self):
        self.assertEqual(sim_client.conversation(0, 4000, 3), sim_client.conversation(0, 4000, 3))
        self.assertNotEqual(sim_client.conversation(0, 4000, 3), sim_client.conversation(1, 4000, 3))

    def test_a_larger_target_is_a_longer_history(self):
        small = sim_client.conversation(0, 400, 1)
        big = sim_client.conversation(0, 40000, 1)
        self.assertGreater(len(big[1]["content"]), len(small[1]["content"]))


class RequestBody(unittest.TestCase):
    def setUp(self):
        self.msgs = sim_client.conversation(0, 4000, 3)

    def test_the_first_turn_carries_the_history_and_its_question(self):
        first = sim_client.request_body("m", self.msgs, 0, 16)["messages"]
        self.assertEqual(len(first), 4)
        self.assertIs(first[-1], self.msgs[3])
        self.assertTrue(all(m["content"] != "(pending)" for m in first))

    def test_a_later_turn_carries_the_previous_answer(self):
        second = sim_client.request_body("m", self.msgs, 1, 16)["messages"]
        self.assertEqual(len(second), 6)
        self.assertIs(second[-1], self.msgs[5])

    def test_the_sampling_is_fixed(self):
        body = sim_client.request_body("m", self.msgs, 2, 16)
        self.assertEqual(body["model"], "m")
        self.assertEqual(body["temperature"], 0)
        self.assertIs(body["stream"], False)


class RowMatching(unittest.TestCase):
    def setUp(self):
        self.client = sim_client.Client("http://x", "k", 1.0)
        self.rows = [{"id": "a", "time": 10.0, "prompt_total": 100},
                     {"id": "b", "time": 11.0, "prompt_total": 100},
                     {"id": "c", "time": 9.0, "prompt_total": 100}]
        self.client._open = lambda path, body=None: {"requests": self.rows}

    def test_the_newest_matching_row(self):
        self.assertEqual(self.client.row(10.5, 100).get("id"), "b")

    def test_a_row_is_handed_out_once(self):
        self.client.row(10.5, 100)
        self.assertEqual(self.client.row(10.5, 100).get("id"), "a")

    def test_a_row_from_before_the_request_is_not_used(self):
        self.client.row(10.5, 100)
        self.client.row(10.5, 100)
        self.assertEqual(self.client.row(10.5, 100), {})       # c started before 10.5

    def test_another_prompt_length_matches_nothing(self):
        self.assertEqual(self.client.row(10.5, 999), {})


class Verdict(unittest.TestCase):
    def test_a_healthy_run_has_no_failures(self):
        good = [row(1, 100000, 90000, 10000, 20.0, 0), row(2, 100000, 96000, 4000, 20.0, 0),
                row(2, 60000, 58000, 2000, 18.0, 1)]
        passed, failed = sim_client.summary(good, [], Args)
        self.assertEqual(failed, [])
        self.assertTrue(any("reuse" in p for p in passed))

    def test_a_collapse_in_reuse_is_a_failure(self):
        bad = [row(1, 100000, 9000, 91000, 20.0, 0), row(2, 100000, 9000, 91000, 20.0, 0),
               row(2, 60000, 58000, 2000, 18.0, 1)]
        _, failed = sim_client.summary(bad, [], Args)
        self.assertTrue(any("reuse on late turns" in f for f in failed))

    def test_a_whole_conversation_read_is_a_failure(self):
        bad = [row(1, 100000, 90000, 12000, 20.0, 0), row(2, 100000, 96000, 4000, 20.0, 0)]
        _, failed = sim_client.summary(bad, [], Args)
        self.assertTrue(any("largest prompt read" in f for f in failed))

    def test_a_decode_collapse_is_a_failure(self):
        bad = [row(2, 100000, 96000, 4000, 20.0, 0), row(2, 60000, 58000, 2000, 5.0, 1)]
        _, failed = sim_client.summary(bad, [], Args)
        self.assertTrue(any("concurrent decode" in f for f in failed))

    def test_one_turn_cannot_show_reuse(self):
        _, failed = sim_client.summary([row(1, 100000, 90000, 10000, 20.0, 0)], [], Args)
        self.assertTrue(any("no turn 2" in f for f in failed))

    def test_a_client_error_is_a_failure(self):
        good = [row(1, 100000, 90000, 10000, 20.0, 0), row(2, 100000, 96000, 4000, 20.0, 0),
                row(2, 60000, 58000, 2000, 18.0, 1)]
        _, failed = sim_client.summary(good, ["session 1 turn 1: boom"], Args)
        self.assertTrue(any("boom" in f for f in failed))


if __name__ == "__main__":
    unittest.main()
