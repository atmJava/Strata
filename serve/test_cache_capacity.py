"""serve/test_cache_capacity.py - the conversation cache, measured in conversations rather than in gigabytes.

The engine reports what one token of a conversation costs in the running KV format (INFO kv_bytes_per_token), so
capacity is budget / that, and the number this deployment asks of it is how many conversations peak near the
compaction point (AGENTS.md: capacity comes from the working set).

    python -m unittest serve.test_cache_capacity -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import serve.server as server  # noqa: E402


def view(**info):
    return server.conversation_cache_view(info, [], {}, {})


class Capacity(unittest.TestCase):
    def test_the_working_set_is_eighty_percent_of_a_200k_window(self):
        self.assertEqual(server.COMPACTION_TOKENS, 163_840)

    def test_the_budget_becomes_tokens_and_conversations(self):
        v = view(conversation_cache_mib=11960, conversation_cache_slots=12, kv_bytes_per_token=14_220)
        self.assertTrue(v["enabled"])
        self.assertEqual(v["kv_bytes_per_token"], 14_220)
        self.assertEqual(v["capacity_tokens"], 11_960 * 1024 * 1024 // 14_220)
        self.assertAlmostEqual(v["capacity_conversations"], round((11_960 * 1024 * 1024 // 14_220) / 163_840, 1))
        self.assertGreaterEqual(v["capacity_conversations"], 5.0)      # the working set at its widest

    def test_a_four_bit_kv_format_holds_more(self):
        int8 = view(conversation_cache_mib=11960, kv_bytes_per_token=14_220)["capacity_conversations"]
        q4 = view(conversation_cache_mib=11960, kv_bytes_per_token=8_460)["capacity_conversations"]
        self.assertGreater(q4, int8)

    def test_without_the_engine_s_number_nothing_is_claimed(self):
        v = view(conversation_cache_mib=11960)
        self.assertIsNone(v["kv_bytes_per_token"])
        self.assertIsNone(v["capacity_tokens"])
        self.assertIsNone(v["capacity_conversations"])

    def test_a_zero_or_absent_budget_is_a_disabled_cache(self):
        for info in ({}, {"conversation_cache_mib": 0}, {"kv_bytes_per_token": 14_220}):
            with self.subTest(info=info):
                v = view(**info)
                self.assertFalse(v["enabled"])
                self.assertIsNone(v["capacity_tokens"])

    def test_a_nonsense_token_size_is_not_divided_by(self):
        for bad in (0, -1):
            with self.subTest(per_token=bad):
                v = view(conversation_cache_mib=11960, kv_bytes_per_token=bad)
                self.assertIsNone(v["capacity_tokens"])

    def test_the_numbers_the_rest_of_the_card_reports_are_kept(self):
        hist = [{"reused": 44073, "prompt_tokens": 60000}]
        v = server.conversation_cache_view({"conversation_cache_mib": 11960, "conversation_cache_slots": 12,
                                            "kv_bytes_per_token": 14_220}, hist,
                                           {"reused": 44073, "prompt_tokens": 60000}, {"parked": 5})
        self.assertEqual(v["slots"], 12)
        self.assertEqual(v["parked"], 5)
        self.assertEqual(v["requests"], 1)
        self.assertEqual(v["requests_reused"], 1)
        self.assertEqual(v["reused_tokens"], 44073)
        self.assertEqual(v["last_reused"], 44073)


if __name__ == "__main__":
    unittest.main()
