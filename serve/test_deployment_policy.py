"""serve/test_deployment_policy.py - the defaults this deployment promises, asserted where a reader will look.

AGENTS.md says the default path is the adapted path: a behaviour this box always wants is code, not a setting. That
promise is only kept if a change that turns one of them back into a switch fails a test, and it should fail one test,
not three files away. This is that one file.

    python -m unittest serve.test_deployment_policy -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import serve.server as server  # noqa: E402

HERE = Path(__file__).resolve().parent


def source_reads(name):
    """The files under serve/ that name this environment variable (tests excluded)."""
    hits = []
    for path in sorted(HERE.glob("*.py")):
        if path.name.startswith("test_"):
            continue
        if name in path.read_text(encoding="utf-8"):
            hits.append(path.name)
    return hits


class Admission(unittest.TestCase):
    def test_short_first_is_a_constant(self):
        self.assertEqual(server.ADMIT_SHORT_FIRST, 2048)

    def test_no_switch_can_change_it(self):
        self.assertEqual(source_reads("STRATA_ADMIT_SHORT_FIRST"), [])


class TheFourDshBehaviours(unittest.TestCase):
    """Each is a default with a shape check of its own, never an opt-in."""

    def setUp(self):
        from serve.test_server import ByteTokenizer, ChatTemplate, MockEngine
        tok = ByteTokenizer()
        self.svc = server.Service(MockEngine(tok, "ok", max_context=1 << 20), tok,
                                  ChatTemplate(ROOT / "serve" / "chat_template.jinja"))

    def test_they_are_on(self):
        self.assertIs(self.svc.defer_title_requests, True)
        self.assertIs(self.svc.pin_dsh_tool_prefix, True)
        self.assertIs(self.svc.ptc_tool_rewrite, True)
        self.assertIs(self.svc.keep_dsh_compaction_tools, True)

    def test_no_switch_can_change_them(self):
        for name in ("STRATA_DEFER_TITLE_REQUESTS", "STRATA_PIN_DSH_TOOL_PREFIX",
                     "STRATA_DSH_PTC_TOOL_REWRITE", "STRATA_KEEP_DSH_COMPACTION_TOOLS"):
            with self.subTest(name=name):
                self.assertEqual(source_reads(name), [])


class Capacity(unittest.TestCase):
    def test_the_compaction_point_is_eighty_percent_of_200k(self):
        self.assertEqual(server.COMPACTION_TOKENS, 163_840)

    def test_the_run_config_carries_no_engine_switch(self):
        sys.path.insert(0, str(ROOT / "strata_control"))
        import sm75_profile
        self.assertEqual(sm75_profile.expected_env(), {})


if __name__ == "__main__":
    unittest.main()
