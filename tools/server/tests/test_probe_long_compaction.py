import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).parents[1] / "probe-long-compaction.py"
SPEC = importlib.util.spec_from_file_location("probe_long_compaction", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class LongCompactionProbeTests(unittest.TestCase):
    def test_compaction_keeps_the_turn_prefix_and_drops_request_tools(self):
        turn = MODULE.turn_body("m", "history", "session", "thread")
        compaction = MODULE.compaction_body(
            "m", turn, [{"type": "message", "role": "assistant", "content": []}], "session", "thread"
        )
        self.assertEqual(compaction["input"][0], turn["input"][0])
        self.assertEqual(compaction["input"][-1]["content"][0]["text"], MODULE.COMPACT_PROMPT)
        self.assertEqual(compaction["tools"], [])
        self.assertIn('"request_kind":"compaction"',
                      compaction["client_metadata"]["x-codex-turn-metadata"])

    def test_response_row_uses_responses_usage_names(self):
        row = MODULE.response_row({
            "status": "completed",
            "usage": {
                "input_tokens": 210_000,
                "input_tokens_details": {"cached_tokens": 209_900},
                "output_tokens": 20,
            },
        }, 2.5)
        self.assertEqual(row["input"], 210_000)
        self.assertEqual(row["cached"], 209_900)
        self.assertEqual(row["output"], 20)
        self.assertEqual(row["wall_s"], 2.5)

    def test_output_limits_are_explicit(self):
        turn = MODULE.turn_body("m", "history", "session", "thread", output_tokens=8)
        compaction = MODULE.compaction_body(
            "m", turn, [], "session", "thread", output_tokens=1024
        )
        self.assertEqual(turn["max_output_tokens"], 8)
        self.assertEqual(compaction["max_output_tokens"], 1024)

    def test_history_text_rejects_an_empty_history(self):
        with self.assertRaises(ValueError):
            MODULE.history_text(0, "x")


if __name__ == "__main__":
    unittest.main()
