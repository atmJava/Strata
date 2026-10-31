import importlib.util
import pathlib
import unittest


MODULE_PATH = pathlib.Path(__file__).parents[1] / "probe-decode-concurrency.py"
SPEC = importlib.util.spec_from_file_location("probe_decode_concurrency", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class WaveSummaryTests(unittest.TestCase):
    def test_aggregate_uses_overlapping_decode_time(self):
        summary = MODULE.wave_summary([
            {"decode_ms": 1000, "completion_tokens": 64, "wall_s": 1.1, "finish": "length", "output_sha256": "a",
             "started_monotonic": 10.0, "finished_monotonic": 11.1},
            {"decode_ms": 1000, "completion_tokens": 64, "wall_s": 1.2, "finish": "length", "output_sha256": "a",
             "started_monotonic": 10.0, "finished_monotonic": 11.2},
        ])
        self.assertEqual(summary["decode_aggregate_tok_s"], 128)
        self.assertEqual(summary["decode_wall_s"], 1.0)
        self.assertAlmostEqual(summary["completion_span_s"], 1.2)
        self.assertAlmostEqual(summary["completion_aggregate_tok_s"], 128 / 1.2)
        self.assertEqual(summary["wall_s_max"], 1.2)
        self.assertEqual(summary["output_tokens"], [64])
        self.assertEqual(summary["output_hashes"], ["a"])

    def test_errors_are_retained_without_poisoning_medians(self):
        summary = MODULE.wave_summary([
            {"decode_ms": 1000, "completion_tokens": 32, "wall_s": 1.0, "finish": "length", "output_sha256": "a"},
            {"error": "timeout", "wall_s": 2.0},
        ])
        self.assertEqual(summary["requests"], 2)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["decode_aggregate_tok_s"], 32)
        self.assertEqual(summary["wall_s_median"], 1.0)

    def test_missing_timings_do_not_invent_a_measurement(self):
        summary = MODULE.wave_summary([
            {"wall_s": 1.0, "completion_tokens": 8, "finish": "stop", "output_sha256": "a"},
        ])
        self.assertIsNone(summary["decode_aggregate_tok_s"])
        self.assertIsNone(summary["completion_span_s"])
        self.assertIsNone(summary["completion_aggregate_tok_s"])

    def test_serial_completion_span_counts_waiting_time(self):
        summary = MODULE.wave_summary([
            {"decode_ms": 5000, "completion_tokens": 512, "wall_s": 5.0, "finish": "length", "output_sha256": "a",
             "started_monotonic": 20.0, "finished_monotonic": 25.0},
            {"decode_ms": 5000, "completion_tokens": 512, "wall_s": 10.0, "finish": "length", "output_sha256": "a",
             "started_monotonic": 20.01, "finished_monotonic": 30.0},
        ])
        self.assertAlmostEqual(summary["decode_aggregate_tok_s"], 204.8)
        self.assertAlmostEqual(summary["completion_span_s"], 10.0)
        self.assertAlmostEqual(summary["completion_aggregate_tok_s"], 102.4)


if __name__ == "__main__":
    unittest.main()
