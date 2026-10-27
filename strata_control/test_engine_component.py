from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from strata_control import engine_component


class LocalEngine(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="strata-engine-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "0.1.51-linux-sm75-cuda"
        self.folder.mkdir()
        self.exe = self.folder / "strata"
        self.exe.write_bytes(b"engine")
        self.exe.chmod(0o700)
        (self.folder / "BUILD.json").write_text(
            json.dumps({"version": "0.1.51", "archs": ["sm_75"]}),
            encoding="utf-8",
        )

    def test_component_reference_resolves_the_local_sm75_binary(self):
        self.assertEqual(
            engine_component.expand("component:strata-engine", {"backend": "cuda"}, root=self.root),
            str(self.exe.resolve()),
        )
        self.assertEqual(engine_component.cuda_archs_present(), ["sm_75"])

    def test_absolute_paths_pass_through_and_other_backends_are_refused(self):
        self.assertEqual(engine_component.expand("/opt/ai/strata", root=self.root), "/opt/ai/strata")
        with self.assertRaisesRegex(engine_component.EngineMissing, "hip"):
            engine_component.expand(
                "component:strata-engine", {"backend": "hip"}, root=self.root,
            )

    def test_manifest_hashes_are_checked_when_present(self):
        body = self.exe.read_bytes()
        manifest = {
            "platform": "linux-x86_64",
            "backend": "cuda",
            "entrypoint": "strata",
            "files": [{
                "path": "strata",
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            }],
        }
        (self.folder / engine_component.MANIFEST).write_text(
            json.dumps(manifest), encoding="utf-8",
        )
        self.assertEqual(
            Path(engine_component.expand("component:strata-engine", root=self.root)),
            self.exe.resolve(),
        )
        self.exe.write_bytes(b"changed")
        self.exe.chmod(0o700)
        with self.assertRaises(engine_component.EngineIntegrityError):
            engine_component.expand("component:strata-engine", root=self.root)


if __name__ == "__main__":
    unittest.main()
