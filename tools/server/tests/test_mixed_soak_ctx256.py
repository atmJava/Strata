"""Tests for the CTX256 mixed-soak workload profile."""

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("mixed_soak", ROOT / "tools" / "server" / "mixed-soak.py")
MIXED_SOAK = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MIXED_SOAK)


class Ctx256ProfileTest(unittest.TestCase):
    """Checks that the long-run profile includes a near-256K cold read."""

    def test_process_patterns_do_not_depend_on_the_build_directory(self) -> None:
        self.assertEqual(MIXED_SOAK.ENGINE_PROCESS_PATTERN, "[s]trata --serve")
        self.assertEqual(MIXED_SOAK.SERVER_PROCESS_PATTERN, "serve/[s]erver.py --engine")

    def test_ctx256_profile_keeps_mixed_waves_and_adds_240k_read(self) -> None:
        self.assertEqual(MIXED_SOAK.CTX256_SHAPES[:-1], MIXED_SOAK.WAVE_SHAPES)
        self.assertEqual(
            MIXED_SOAK.CTX256_SHAPES[-1],
            ("short_json", "medium_code", "agent_tool", "long_240k"),
        )
        task = MIXED_SOAK.make_task("long_240k", 0, 0)
        self.assertEqual(task["target_context"], 245_760)
        self.assertEqual(task["kind"], "long_240k")
        self.assertTrue(task["expected_answer"].startswith("P2-"))


if __name__ == "__main__":
    unittest.main()
