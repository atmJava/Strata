"""The Linux CUDA picture-reader choice and the server's CUDA-to-CPU retry.

    python -m unittest tools.test_vision_reader
"""
from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import serve.server as server
from strata_control import vision_reader as VR


def folder(*names: str) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="vr-"))
    for name in names:
        (directory / name).write_bytes(b"x")
    return directory


class Choose(unittest.TestCase):
    def test_server_defaults_to_cuda_and_keeps_explicit_choices(self):
        self.assertEqual(VR.default_mode(22), "gpu")
        self.assertEqual(VR.default_mode(None), "gpu")
        self.assertEqual(VR.wanted(None, 22), "gpu")
        self.assertEqual(VR.wanted("yes", 22), "gpu")
        self.assertEqual(VR.wanted("gpu", 4), "gpu")
        self.assertEqual(VR.wanted("cpu", 22), "cpu")
        for choice in ("no", "none", "off"):
            self.assertEqual(VR.wanted(choice, 22), "none")

    def test_one_program_is_both_the_cuda_and_cpu_reader(self):
        directory = folder("strata-vision")
        gpu = VR.choose(directory, "gpu")
        self.assertEqual((gpu.mode, gpu.exe.name, gpu.cpu_exe.name),
                         ("gpu", "strata-vision", "strata-vision"))
        cpu = VR.choose(directory, "cpu")
        self.assertEqual((cpu.mode, cpu.exe.name, cpu.cpu_exe), ("cpu", "strata-vision", None))
        self.assertEqual(VR.choose(directory, "off").mode, "none")
        self.assertEqual(VR.choose(directory, None).mode, "none")

    def test_removed_vendor_readers_are_not_discovered(self):
        directory = folder("strata-vision-hip", "strata-vision-sycl")
        chosen = VR.choose(directory, "gpu")
        self.assertEqual(chosen.mode, "none")
        self.assertIn("no picture reader", chosen.note)

    def test_run_config_and_cpu_fallback_use_the_same_file(self):
        directory = folder("strata-vision")
        base = {"mmproj": "m.gguf", "model": "t.gguf", "max_tokens": 1024}
        section = VR.vision_section(VR.choose(directory, "gpu"), base)
        self.assertTrue(section["gpu"])
        self.assertEqual(Path(section["exe"]).name, "strata-vision")
        self.assertEqual(Path(section["cpu_exe"]).name, "strata-vision")
        fallback = VR.cpu_fallback(section)
        self.assertFalse(fallback["gpu"])
        self.assertEqual(fallback["exe"], section["exe"])
        self.assertNotIn("cpu_exe", fallback)
        self.assertEqual(fallback["mmproj"], "m.gguf")
        self.assertIsNone(VR.cpu_fallback({"exe": "strata-vision", "gpu": True}))


class FakeProc:
    def __init__(self, first_line: str):
        self.stdin, self.stdout = io.StringIO(), io.StringIO(first_line)
        self.killed = False

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return None


def make_vision(cfg: dict) -> "server.Vision":
    with mock.patch.object(server.Vision, "work_dir", staticmethod(lambda: Path(tempfile.gettempdir()))):
        return server.Vision(cfg, lazy=True)


class Fallback(unittest.TestCase):
    CFG = {
        "exe": "/e/strata-vision",
        "cpu_exe": "/e/strata-vision",
        "gpu": True,
        "mmproj": "/m/mm.gguf",
        "model": "/m/t.gguf",
        "max_tokens": 1024,
        "threads": 6,
    }

    def run_start(self, outcomes: list[str | Exception], cfg: dict | None = None):
        started = []
        remaining = list(outcomes)

        def popen(what, args, **kw):
            started.append(args)
            outcome = remaining.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return FakeProc(outcome)

        vision = make_vision(cfg or self.CFG)
        output = io.StringIO()
        with mock.patch.object(server, "popen", popen), \
                mock.patch("builtins.print", lambda *a, **k: output.write(" ".join(map(str, a)) + "\n")):
            error = None
            try:
                vision._start()
            except RuntimeError as exc:
                error = exc
        return vision, started, output.getvalue(), error

    def test_cuda_reader_starts_with_gpu(self):
        vision, started, log, error = self.run_start(["READY 2048\n"])
        self.assertIsNone(error)
        self.assertEqual(len(started), 1)
        self.assertIn("--gpu", started[0])
        self.assertEqual(log, "")
        self.assertFalse(vision.stopped)

    def test_cuda_failure_retries_same_program_on_cpu_with_one_line(self):
        vision, started, log, error = self.run_start(["ERR this GPU cannot start\n", "READY 2048\n"])
        self.assertIsNone(error)
        self.assertEqual([Path(args[0]).name for args in started], ["strata-vision", "strata-vision"])
        self.assertIn("--gpu", started[0])
        self.assertNotIn("--gpu", started[1])
        self.assertEqual(log.count("\n"), 1)
        self.assertIn("CPU", log)
        self.assertFalse(vision.stopped)
        self.assertIsNone(vision.fallback)

    def test_missing_or_empty_cuda_start_also_retries_cpu(self):
        for outcome in (FileNotFoundError("strata-vision"), ""):
            with self.subTest(outcome=type(outcome).__name__):
                vision, started, _, error = self.run_start([outcome, "READY 2048\n"])
                self.assertIsNone(error)
                self.assertEqual(len(started), 2)

    def test_cpu_retry_stays_selected_and_its_failure_is_reported(self):
        vision, started, _, error = self.run_start(["ERR nope\n", "READY 2048\n"])
        self.assertIsNone(error)
        started.clear()
        with mock.patch.object(server, "popen",
                               lambda what, args, **kw: started.append(args) or FakeProc("READY 2048\n")):
            vision.restart()
        self.assertEqual(len(started), 1)
        self.assertNotIn("--gpu", started[0])

        _, _, _, error = self.run_start(["ERR nope\n", "ERR cannot load the vision encoder\n"])
        self.assertIsNotNone(error)
        self.assertIn("cannot load", str(error))

    def test_config_without_fallback_does_not_retry(self):
        cfg = {key: value for key, value in self.CFG.items() if key != "cpu_exe"}
        vision, started, log, error = self.run_start(["ERR nope\n"], cfg)
        self.assertIsNotNone(error)
        self.assertEqual(len(started), 1)
        self.assertEqual(log, "")

    def test_cpu_config_does_not_use_gpu(self):
        cfg = {"exe": "/e/strata-vision", "gpu": False, "mmproj": "/m/mm.gguf", "model": "/m/t.gguf"}
        _, started, _, error = self.run_start(["READY 2048\n"], cfg)
        self.assertIsNone(error)
        self.assertNotIn("--gpu", started[0])


if __name__ == "__main__":
    unittest.main()
