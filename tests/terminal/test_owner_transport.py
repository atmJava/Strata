"""Owner transport contract tests for the private Unix socket."""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strata_control import owner_transport as ot  # noqa: E402
from strata_control.private_files import restrict_to_current_user  # noqa: E402


class TestAddressing(unittest.TestCase):
    def test_unix_address_fits_the_sun_path_limit(self):
        """A long home directory must not produce an address the kernel rejects."""
        long_dir = Path("/Users/someone-with-a-very-long-account-name/Documents/deepseek-harness/state")
        addr = ot.address_for(long_dir, "default")
        self.assertLessEqual(len(addr.encode()), 90)
        self.assertTrue(addr.endswith(".sock"))

    def test_address_is_stable_for_the_same_inputs(self):
        d = Path("/tmp/strata-runtime")
        self.assertEqual(ot.address_for(d, "default"), ot.address_for(d, "default"))
        self.assertNotEqual(ot.address_for(d, "default"), ot.address_for(d, "other"))


class TestPosixListener(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="strata-transport-"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_round_trip_and_private_permissions(self):
        addr = ot.address_for(self.tmp, "default")
        with ot.Listener(addr, timeout=2) as listener:
            mode = stat.S_IMODE(os.stat(listener.address).st_mode)
            self.assertEqual(mode, 0o600, "the owner socket must not be reachable by other users")

            received: list[bytes] = []

            def server():
                conn = listener.accept()
                assert conn is not None, "the listener timed out"
                with conn:
                    received.append(conn.recv(4096))

            t = threading.Thread(target=server)
            t.start()
            with ot.connect(listener.address, timeout=2) as client:
                client.sendall(json.dumps({"hello": "owner"}).encode() + b"\n")
            t.join(timeout=5)
            self.assertEqual([json.loads(received[0])], [{"hello": "owner"}])

    def test_the_socket_path_is_removed_on_close(self):
        addr = ot.address_for(self.tmp, "default")
        listener = ot.Listener(addr)
        listener.bind()
        self.assertTrue(Path(listener.address).exists())
        listener.close()
        self.assertFalse(Path(listener.address).exists(),
                         "a stale socket path would block the next owner from binding")

    def test_accept_returns_none_on_timeout_rather_than_raising(self):
        """The owner polls, so a quiet channel must not look like an error."""
        addr = ot.address_for(self.tmp, "default")
        with ot.Listener(addr, timeout=0.2) as listener:
            self.assertIsNone(listener.accept())

    def test_connect_to_a_dead_address_raises_oserror(self):
        addr = ot.address_for(self.tmp, "nothing-here")
        with self.assertRaises(OSError):
            ot.connect(addr, timeout=1)


class TestStateFileValidation(unittest.TestCase):
    """A client must refuse a state file it cannot trust."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="strata-state-"))
        self.path = self.tmp / "default.json"
        self.path.write_text('{"token": "x"}')
        restrict_to_current_user(self.path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_private_regular_file_is_accepted(self):
        ot.check_state_file(self.path)

    def test_a_world_readable_state_file_is_refused(self):
        os.chmod(self.path, 0o644)
        with self.assertRaises(ot.TransportError):
            ot.check_state_file(self.path)

    def test_a_symlink_is_refused(self):
        link = self.tmp / "link.json"
        link.symlink_to(self.path)
        with self.assertRaises(ot.TransportError):
            ot.check_state_file(link)

    def test_a_directory_is_refused(self):
        with self.assertRaises(ot.TransportError):
            ot.check_state_file(self.tmp)

    def test_an_implausibly_large_state_file_is_refused(self):
        big = self.tmp / "big.json"
        big.write_bytes(b"x" * (17 * 1024))
        os.chmod(big, 0o600)
        with self.assertRaises(ot.TransportError):
            ot.check_state_file(big)

    def test_a_missing_file_raises_oserror(self):
        with self.assertRaises(OSError):
            ot.check_state_file(self.tmp / "nope.json")


if __name__ == "__main__":
    unittest.main()
