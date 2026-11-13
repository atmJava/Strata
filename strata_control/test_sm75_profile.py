from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from strata_control import sm75_profile


def config() -> dict:
    return {
        "backend": "cuda",
        "arch": "qwen4exp",
        "parallel": 6,
        "gpu": [0, 1],
        "model_name": "qwen3.8-flash-next-iq3_xxs",
        "args": [
            "--max-context", "262144",
            "--batch", "6",
            "--batch-groups", "2",
            "--kv", "int8",
            "--kv-resident", "32768",
        ],
        "env": {},
    }


class Sm75Profile(unittest.TestCase):
    def setUp(self):
        self.env = sm75_profile.expected_env()

    def check(self, cfg, process_env=None, fill_env=True, env=None):
        expected = dict(self.env if env is None else env)
        if fill_env:
            cfg["env"] = dict(expected)
        if process_env is None:
            process_env = {}
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "env.json"
            path.write_text(json.dumps({"env": expected}), encoding="utf-8")
            return sm75_profile.check_config(cfg, process_env=process_env, env_path=path)

    def test_production_shape_passes(self):
        result = self.check(config())
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["observed"]["kv"], "int8")
        self.assertEqual(result["observed"]["env_sources"], {})     # no engine switch: the code decides

    def test_runtime_shape_mismatches_are_named(self):
        bad = copy.deepcopy(config())
        bad.update(backend="hip", arch="deepseek41", parallel=4, gpu=[0])
        bad["args"] = ["--max-context", "192000", "--batch", "4",
                       "--batch-groups", "1", "--kv", "fp16", "--kv-resident", "4096"]
        result = self.check(bad)
        self.assertFalse(result["ok"])
        self.assertEqual({item["code"] for item in result["errors"]}, {
            "backend_mismatch", "arch_mismatch", "parallel_mismatch", "batch_mismatch",
            "batch_groups_mismatch", "max_context_mismatch", "kv_mismatch",
            "kv_resident_mismatch", "gpu_shape",
        })

    def test_the_production_list_is_empty(self):
        """A run config holds deployment identity only (AGENTS.md): nothing has to come from its env map."""
        self.assertEqual(self.env, {})

    def test_env_missing_and_wrong_are_distinct(self):
        """With a list to check, a missing key and a wrong value are reported apart (the check still works)."""
        expected = {"STRATA_TEST_SWITCH": "1", "STRATA_TEST_OTHER": "2"}
        cfg = config()
        cfg["env"] = {"STRATA_TEST_SWITCH": "0"}
        result = self.check(cfg, fill_env=False, env=expected)
        codes = [item["code"] for item in result["errors"]]
        self.assertIn("env_missing", codes)                          # STRATA_TEST_OTHER
        self.assertIn("env_mismatch", codes)                         # STRATA_TEST_SWITCH
        self.assertEqual(result["observed"]["env_sources"], {"STRATA_TEST_SWITCH": "run_config"})

    def test_explicit_dflash2_is_part_of_the_server_profile(self):
        cfg = config()
        cfg["drafting"] = "dflash2"
        cfg["args"] += ["--draft", "dflash2", "--draft-model", "/data/dflash2/pack"]
        result = self.check(cfg)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["observed"]["drafting"], "dflash2")

    def test_an_unknown_drafter_is_refused(self):
        cfg = config()
        cfg["drafting"] = "telepathy"
        result = self.check(cfg)
        self.assertIn("drafting_unsupported", {item["code"] for item in result["errors"]})

    def test_nested_optional_engines_cannot_escape_the_cuda_profile(self):
        cfg = config()
        cfg["images"] = {"backend": "hip", "exe": "/opt/engine-image/sd-cli"}
        cfg["vision"] = {"gpu": True, "exe": "/opt/engine/strata-vision-sycl"}
        result = self.check(cfg)
        self.assertFalse(result["ok"])
        self.assertEqual({item["code"] for item in result["errors"]},
                         {"images_backend_mismatch", "vision_backend_mismatch"})

    def test_cuda_image_engine_and_single_vision_reader_pass(self):
        cfg = config()
        cfg["images"] = {"backend": "cuda", "exe": "/opt/engine-image/sd-cli"}
        cfg["vision"] = {
            "gpu": True,
            "exe": "/opt/engine/strata-vision",
            "cpu_exe": "/opt/engine/strata-vision",
        }
        result = self.check(cfg)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["observed"]["images_backend"], "cuda")
        self.assertTrue(result["observed"]["vision_gpu"])


if __name__ == "__main__":
    unittest.main()
