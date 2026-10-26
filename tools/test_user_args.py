"""The options a person adds to a run config survive generated arguments and later saves.

``strata_control/user_args.py`` owns the merge/capture rules; ``serve.server.engine_args`` and
``serve.runconfig.apply`` are the remaining consumers.  Every external process is mocked.

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

CVEC = ["--control-vector-scaled", "C:/vectors/mine.gguf:1.0",
        "--control-vector-layer-range", "4", "44", "--cvec-mode", "project",
        "--cvec-dir", "per-layer"]


def value(args, flag):
    return args[args.index(flag) + 1] if flag in args[:-1] else None


class Rule(unittest.TestCase):
    def test_groups_and_merge(self):
        self.assertEqual(user_args.groups(["--a", "1", "--b", "--c", "x", "y"]),
                         [["--a", "1"], ["--b"], ["--c", "x", "y"]])
        self.assertEqual(user_args.merge(["--a", "1", "--b", "2", "--a", "3"],
                                         ["--a", "9", "--z"]),
                         ["--b", "2", "--a", "9", "--z"])
        self.assertEqual(user_args.merge(["--a", "1"], []), ["--a", "1"])

    def test_capture_tells_hand_edits_from_generated_arguments(self):
        written = ["--max-context", "32768", "--vram-reserve-mib", "700", "--spec", "4"]
        cfg = {"args": written + CVEC, "args_written": written}
        self.assertEqual(user_args.capture(cfg), CVEC)
        cfg = {"args": ["--max-context", "32768", "--vram-reserve-mib", "0", "--spec", "4"],
               "args_written": written}
        self.assertEqual(user_args.capture(cfg), ["--vram-reserve-mib", "0"])
        self.assertEqual(user_args.capture({**cfg, "args": written}), [])
        self.assertEqual(user_args.capture({
            "args": ["--max-context", "1"],
            "args_written": ["--max-context", "2"],
        }, frozenset({"--max-context"})), [])
        self.assertEqual(user_args.capture({
            "args": written[:4],
            "args_written": written,
            "user_args": ["--spec", "2"],
        }), [])
        self.assertEqual(user_args.capture({
            "args": written,
            "args_written": written,
            "user_args": ["--spec", "2"],
        }), ["--spec", "2"])
        self.assertEqual(user_args.capture({"args": CVEC, "user_args": ["--a", "1"]}),
                         ["--a", "1"])

    def test_apply_stamp_and_set_flag_keep_one_value_per_flag(self):
        cfg = {
            "args": ["--pack", "/p", "--vram-reserve-mib", "1536", "--spec", "4"],
            "user_args": ["--vram-reserve-mib", "0"] + CVEC,
        }
        self.assertEqual(user_args.apply(cfg), [
            "--vram-reserve-mib", "--control-vector-scaled",
            "--control-vector-layer-range", "--cvec-mode", "--cvec-dir",
        ])
        self.assertEqual(value(cfg["args"], "--vram-reserve-mib"), "0")
        self.assertEqual(cfg["args"].count("--vram-reserve-mib"), 1)
        user_args.stamp(cfg, user_args.set_flag(cfg["user_args"],
                                                "--vram-reserve-mib", "256"))
        self.assertEqual(value(cfg["user_args"], "--vram-reserve-mib"), "256")
        self.assertEqual(cfg["args_written"], cfg["args"])

    def test_dropped_by_setup_keeps_only_unknown_person_options(self):
        old = ["--max-context", "32768", "--vram-reserve-mib", "0", *CVEC]
        new = ["--max-context", "131072", "--pack", "/p"]
        setup_flags = frozenset({"--max-context", "--pack"})
        self.assertEqual(user_args.dropped_by_setup(old, new, setup_flags),
                         ["--vram-reserve-mib", "0", *CVEC])


class Start(unittest.TestCase):
    def test_every_start_puts_the_persons_options_over_the_generated_ones(self):
        cfg = {"args": ["--pack", "/p", "--vram-reserve-mib", "1536", "--spec", "4"],
               "user_args": ["--vram-reserve-mib", "0"] + CVEC}
        with contextlib.redirect_stdout(io.StringIO()):
            args = engine_args(cfg)
        self.assertEqual(value(args, "--vram-reserve-mib"), "0")
        self.assertEqual(args.count("--vram-reserve-mib"), 1)
        self.assertEqual(args[args.index("--control-vector-scaled"):][:9], CVEC)
        self.assertEqual(cfg["args"][3], "1536")

    def test_the_settings_page_records_the_reserve_as_the_persons(self):
        cfg = {"args": ["--pack", "/p", "--vram-reserve-mib", "1536"],
               "args_written": ["--pack", "/p", "--vram-reserve-mib", "1536"]}
        new, changed = runconfig.apply(cfg, {"vram_reserve_mib": 0})
        self.assertEqual(changed, ["vram_reserve_mib"])
        self.assertEqual(new["user_args"], ["--vram-reserve-mib", "0"])
        self.assertEqual(new["args_written"], new["args"])
        back, _ = runconfig.apply(new, {"vram_reserve_mib": None})
        self.assertNotIn("user_args", back)
        self.assertNotIn("--vram-reserve-mib", back["args"])


if __name__ == "__main__":
    unittest.main()
