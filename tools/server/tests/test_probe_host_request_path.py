"""Unit tests for the no-engine DSH host request-path probe."""
from __future__ import annotations

import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
PROBE = ROOT / "tools/server/probe-host-request-path.py"


def load_probe():
    spec = importlib.util.spec_from_file_location("probe_host_request_path", PROBE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {PROBE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HostRequestPathProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.probe = load_probe()

    def test_percentile_handles_empty_single_and_interpolation(self):
        self.assertIsNone(self.probe.percentile([], 0.5))
        self.assertEqual(self.probe.percentile([7.0], 0.9), 7.0)
        self.assertEqual(self.probe.percentile([1.0, 2.0, 3.0], 0.5), 2.0)
        self.assertAlmostEqual(self.probe.percentile([1.0, 3.0], 0.5), 2.0)

    def test_timing_summary_keeps_bounded_sample_and_spread(self):
        result = self.probe.timing_summary_ms([1.23456, 2.0, 3.0])
        self.assertEqual(result["n"], 3)
        self.assertEqual(result["median_ms"], 2.0)
        self.assertEqual(result["min_ms"], 1.235)
        self.assertEqual(result["max_ms"], 3.0)
        self.assertEqual(result["samples_ms"], [1.235, 2.0, 3.0])

    def test_dsh_tool_surface_matches_current_names(self):
        tools = self.probe.dsh_tools()
        self.assertEqual(sorted(tool["name"] for tool in tools),
                         sorted(self.probe.DSH_MAIN_TOOLS))
        self.assertTrue(all(tool["type"] == "function" for tool in tools))

    def test_parse_contexts_rejects_tiny_or_empty_values(self):
        self.assertEqual(self.probe.parse_contexts("6000,94700"), [6000, 94700])
        with self.assertRaises(Exception):
            self.probe.parse_contexts("")
        with self.assertRaises(Exception):
            self.probe.parse_contexts("999")


if __name__ == "__main__":
    unittest.main()
