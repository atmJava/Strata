"""The engine options a person set by hand survive every start (#3216 control vectors, #3215 and #3270 --vram-reserve-mib).
strata_control/user_args.py is the rule; serve/server.py engine_args and serve/runconfig.py use it.  Fake configs in
memory - nothing is installed, started or written to disk.

    python -m unittest tools.test_user_args
"""
from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from serve import runconfig  # noqa: E402
from serve.server import engine_args  # noqa: E402
from strata_control import user_args  # noqa: E402

CVEC = ["--control-vector-scaled", "C:/vectors/mine.gguf:1.0", "--control-vector-layer-range", "4", "44",
        "--cvec-mode", "project", "--cvec-dir", "per-layer"]


def value(args, flag):
    return args[args.index(flag) + 1] if flag in args[:-1] else None


class Rule(unittest.TestCase):
    def test_groups_and_merge(self):
        self.assertEqual(user_args.groups(["--a", "1", "--b", "--c", "x", "y"]), [["--a", "1"], ["--b"], ["--c", "x", "y"]])
        self.assertEqual(user_args.merge(["--a", "1", "--b", "2", "--a", "3"], ["--a", "9", "--z"]), ["--b", "2", "--a", "9", "--z"])
        self.assertEqual(user_args.merge(["--a", "1"], []), ["--a", "1"])

    def test_capture_tells_hand_edits_from_what_was_written(self):
        written = ["--max-context", "32768", "--vram-reserve-mib", "700", "--spec", "4"]
        cfg = {"args": written + CVEC, "args_written": written}
        self.assertEqual(user_args.capture(cfg), CVEC)
        cfg = {"args": ["--max-context", "32768", "--vram-reserve-mib", "0", "--spec", "4"], "args_written": written}
        self.assertEqual(user_args.capture(cfg), ["--vram-reserve-mib", "0"])
        self.assertEqual(user_args.capture({**cfg, "args": written}), [])                  # nothing changed
        # an option the caller chooses itself (the context size, say): a hand edit of it is not taken as an override
        self.assertEqual(user_args.capture({"args": ["--max-context", "1"], "args_written": ["--max-context", "2"]},
                                           {"--max-context"}), [])
        # an option taken out of "args" by hand is the person's no more
        self.assertEqual(user_args.capture({"args": written[:4], "args_written": written, "user_args": ["--spec", "2"]}), [])
        self.assertEqual(user_args.capture({"args": written, "args_written": written, "user_args": ["--spec", "2"]}), ["--spec", "2"])   # user_args stands
        # no copy of what was written: only user_args is known
        self.assertEqual(user_args.capture({"args": CVEC, "user_args": ["--a", "1"]}), ["--a", "1"])


class Start(unittest.TestCase):
    def test_every_start_puts_the_persons_options_over_the_generated_ones(self):
        cfg = {"args": ["--pack", "/p", "--vram-reserve-mib", "1536", "--spec", "4"],
               "user_args": ["--vram-reserve-mib", "0"] + CVEC}
        with contextlib.redirect_stdout(io.StringIO()):
            args = engine_args(cfg)
        self.assertEqual(value(args, "--vram-reserve-mib"), "0")
        self.assertEqual(args.count("--vram-reserve-mib"), 1)
        self.assertEqual(args[args.index("--control-vector-scaled"):][:9], CVEC)
        self.assertEqual(cfg["args"][3], "1536")                                           # the config in memory is untouched

    def test_the_settings_page_records_the_reserve_as_the_persons(self):
        cfg = {"args": ["--pack", "/p", "--vram-reserve-mib", "1536"], "args_written": ["--pack", "/p", "--vram-reserve-mib", "1536"]}
        new, changed = runconfig.apply(cfg, {"vram_reserve_mib": 0})
        self.assertEqual(changed, ["vram_reserve_mib"])
        self.assertEqual(new["user_args"], ["--vram-reserve-mib", "0"])
        self.assertEqual(new["args_written"], new["args"])
        back, _ = runconfig.apply(new, {"vram_reserve_mib": None})
        self.assertNotIn("user_args", back)
        self.assertNotIn("--vram-reserve-mib", back["args"])


if __name__ == "__main__":
    unittest.main()
