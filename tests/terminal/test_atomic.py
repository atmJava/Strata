"""Atomic record replacement closes temporary descriptors on every failure path."""

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from strata_control import atomic


class AtomicWriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="strata-atomic-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / "record.json"
        self.target.write_bytes(b"old")

    @staticmethod
    def _capture_mkstemp(created):
        real_mkstemp = tempfile.mkstemp

        def create(*args, **kwargs):
            descriptor, name = real_mkstemp(*args, **kwargs)
            created.append((descriptor, Path(name)))
            return descriptor, name

        return create

    def test_replaces_the_record_and_leaves_no_temporary_file(self):
        atomic.write_bytes(self.target, b'{"new":true}')
        self.assertEqual(self.target.read_bytes(), b'{"new":true}')
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)
        self.assertEqual(list(self.root.glob(".*.tmp")), [])

    def test_exclusive_create_sets_owner_only_mode_and_never_overwrites(self):
        target = self.root / "new.json"
        atomic.write_new(target, b"new")
        self.assertEqual(target.read_bytes(), b"new")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        with self.assertRaises(FileExistsError):
            atomic.write_new(target, b"replacement")
        self.assertEqual(target.read_bytes(), b"new")

    def test_exclusive_create_fdopen_failure_leaves_no_file_or_descriptor(self):
        target = self.root / "new.json"
        descriptors = []
        real_open = os.open

        def record_open(*args, **kwargs):
            descriptor = real_open(*args, **kwargs)
            descriptors.append(descriptor)
            return descriptor

        with mock.patch.object(atomic.os, "open", side_effect=record_open), \
                mock.patch.object(atomic.os, "fdopen", side_effect=OSError("fdopen failed")):
            with self.assertRaisesRegex(OSError, "fdopen failed"):
                atomic.write_new(target, b"new")
        with self.assertRaises(OSError):
            os.fstat(descriptors[-1])
        self.assertFalse(target.exists())

    def test_fdopen_failure_closes_the_descriptor_and_removes_the_temp(self):
        created = []
        with mock.patch.object(atomic.tempfile, "mkstemp", side_effect=self._capture_mkstemp(created)), \
                mock.patch.object(atomic.os, "fdopen", side_effect=OSError("fdopen failed")):
            with self.assertRaisesRegex(OSError, "fdopen failed"):
                atomic.write_bytes(self.target, b"new")
        descriptor, path = created[-1]
        with self.assertRaises(OSError):
            os.fstat(descriptor)
        self.assertFalse(path.exists())
        self.assertEqual(self.target.read_bytes(), b"old")

    def test_replace_failure_closes_the_descriptor_and_removes_the_temp(self):
        created = []
        with mock.patch.object(atomic.tempfile, "mkstemp", side_effect=self._capture_mkstemp(created)), \
                mock.patch.object(atomic, "replace", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(OSError, "replace failed"):
                atomic.write_bytes(self.target, b"new")
        descriptor, path = created[-1]
        with self.assertRaises(OSError):
            os.fstat(descriptor)
        self.assertFalse(path.exists())
        self.assertEqual(self.target.read_bytes(), b"old")

    def test_parent_fsync_failure_does_not_report_a_completed_replace_as_failed(self):
        real_fsync = os.fsync
        calls = 0

        def fail_parent(descriptor):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("directory fsync failed")
            return real_fsync(descriptor)

        with mock.patch.object(atomic.os, "fsync", side_effect=fail_parent):
            atomic.write_bytes(self.target, b"new")
        self.assertEqual(self.target.read_bytes(), b"new")


if __name__ == "__main__":
    unittest.main()
