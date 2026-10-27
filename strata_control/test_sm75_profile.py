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

    def check(self, cfg, process_env=None, fill_env=True):
        if fill_env:
            cfg["env"] = {key: value for key, value in self.env.items()
                          if key != "STRATA_ADMIT_SHORT_FIRST"}
        if process_env is None:
            process_env = {"STRATA_ADMIT_SHORT_FIRST": "2048"}
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "env.json"
            path.write_text(json.dumps({"env": self.env}), encoding="utf-8")
            return sm75_profile.check_config(cfg, process_env=process_env, env_path=path)

    def test_production_shape_passes(self):
        result = self.check(config())
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["observed"]["kv"], "int8")
        self.assertEqual(result["observed"]["env_sources"]["STRATA_ADMIT_SHORT_FIRST"], "process")

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

    def test_env_missing_and_wrong_are_distinct(self):
        cfg = config()
        cfg["env"] = {key: value for key, value in self.env.items()
                      if key != "STRATA_SM75_PERF"}
        cfg["env"]["STRATA_HC_Q8"] = "0"
        result = self.check(cfg, fill_env=False)
        codes = [item["code"] for item in result["errors"]]
        self.assertIn("env_missing", codes)
        self.assertIn("env_mismatch", codes)

    def test_admit_short_first_must_reach_the_server_process(self):
        cfg = config()
        cfg["env"]["STRATA_ADMIT_SHORT_FIRST"] = "2048"
        result = self.check(cfg, process_env={}, fill_env=False)
        self.assertFalse(result["ok"])
        self.assertIn("env_missing", [item["code"] for item in result["errors"]])
        self.assertIn("env_server_switch_in_config",
                      [item["code"] for item in result["warnings"]])

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
