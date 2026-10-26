"""Per-user Linux state locations."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from strata_control import paths, runtime


class StatePaths(unittest.TestCase):
    def test_xdg_state_home_is_used(self):
        self.assertEqual(paths.state_base({"XDG_STATE_HOME": "/x"}), Path("/x") / "strata")
        self.assertEqual(paths.state_base({}, home=Path("/home/me")),
                         Path("/home/me") / ".local" / "state" / "strata")

    def test_runtime_dir_default_lives_under_the_state_base(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"XDG_STATE_HOME": d}
            with mock.patch.dict(os.environ, env):
                os.environ.pop("STRATA_RUNTIME_DIR", None)
                self.assertEqual(runtime.runtime_dir(), paths.state_base() / "runtime")
                self.assertEqual(runtime.runtime_dir().parent.parent, Path(d))

if __name__ == "__main__":
    unittest.main()
