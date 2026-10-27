"""Owner-only files on Linux."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from strata_control import private_files


class PrivateFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_file_mode_is_0600_and_stays_readable(self):
        path = self.root / "key.json"
        path.write_text('{"apiKey": "x"}')
        private_files.restrict_to_current_user(path)
        self.assertTrue(private_files.is_private(path))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(path.read_text(), '{"apiKey": "x"}')

    def test_directory_mode_is_0700(self):
        folder = self.root / "state"
        folder.mkdir()
        private_files.restrict_to_current_user(folder)
        self.assertTrue(private_files.is_private(folder))
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)

    def test_private_mkdir_creates_owner_only_directories(self):
        made = private_files.private_mkdir(self.root / "a" / "b", parents=True)
        self.assertEqual(stat.S_IMODE(made.stat().st_mode), 0o700)

    def test_group_or_world_access_is_not_private(self):
        path = self.root / "shared.json"
        path.write_text("{}")
        os.chmod(path, 0o644)
        self.assertFalse(private_files.is_private(path))


if __name__ == "__main__":
    unittest.main()
