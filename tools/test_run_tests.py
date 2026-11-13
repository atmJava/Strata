"""tests for tools/run_tests.py - what the runner reports and what it refuses to report.

    python -m unittest tools.test_run_tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import run_tests  # noqa: E402


class EngineTests(unittest.TestCase):
    def test_every_engine_test_has_its_source(self):
        for name, extra in run_tests.ENGINE_TESTS.items():
            with self.subTest(name=name):
                self.assertTrue((ROOT / "src" / "core" / (name + ".cpp")).exists())
                for item in extra:
                    self.assertTrue((ROOT / item).exists(), item)

    def test_the_engine_tests_are_the_conversation_ones(self):
        self.assertEqual(sorted(run_tests.ENGINE_TESTS),
                         ["conversation_cache_test", "conversation_file_test", "conversation_memory_test",
                          "conversation_split_failure_test", "session_save_reclaim_test"])


class Output(unittest.TestCase):
    def test_tail_keeps_the_last_lines(self):
        self.assertEqual(run_tests.tail("a\nb\nc\nd", 2), "c\nd")

    def test_tail_of_nothing_says_so(self):
        self.assertEqual(run_tests.tail(""), "(no output)")

    def test_a_timeout_is_124(self):
        code, output = run_tests.run([sys.executable, "-c", "import time; time.sleep(5)"], ROOT, 0.2)
        self.assertEqual(code, 124)
        self.assertIn("timed out", output)

    def test_a_missing_command_is_127(self):
        code, output = run_tests.run(["definitely-not-a-command-strata"], ROOT, 5)
        self.assertEqual(code, 127)


if __name__ == "__main__":
    unittest.main()
