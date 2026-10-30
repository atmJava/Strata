"""tools/g0_audit.py 的离线测试。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import g0_audit  # noqa: E402


class G0Audit(unittest.TestCase):
    def test_noise_rules_are_shape_only_and_ordered(self):
        self.assertEqual(g0_audit.classify_noise({"prompt_tokens": 17, "output_tokens": 2}), "startup_warmup")
        self.assertEqual(g0_audit.classify_noise({"prompt_tokens": 75, "output_tokens": 400}),
                         "fixed_400_token_probe")
        self.assertEqual(g0_audit.classify_noise({"prompt_tokens": 60, "output_tokens": 48}), "short_smoke")
        self.assertIsNone(g0_audit.classify_noise({"prompt_tokens": 20000, "output_tokens": 50}))

    def test_percentile_uses_linear_interpolation(self):
        values = [1, 2, 3, 4]
        self.assertEqual(g0_audit.percentile(values, 0), 1)
        self.assertEqual(g0_audit.percentile(values, 0.5), 2.5)
        self.assertEqual(g0_audit.percentile(values, 1), 4)
        self.assertEqual(g0_audit.percentile([2.012, 2.018], 0.5), 2.015)
        self.assertEqual(g0_audit.percentile([52.995, 100.862], 0.5), 76.9285)

    def test_distribution_rounds_half_up_like_the_frozen_evidence(self):
        rows = [{"value": 2.015}, {"value": 60.175}]
        result = g0_audit.distribution(rows, "value", 2)
        self.assertEqual(result["p50"], 31.1)
        self.assertEqual(g0_audit.round_half_up(2.015, 2), 2.02)
        self.assertEqual(g0_audit.round_half_up(60.175, 2), 60.18)

    def test_capture_wrapper_keeps_the_raw_request_rows(self):
        row = {"id": "0123456789ab", "prompt_tokens": 17, "output_tokens": 2}
        snapshot = {"payload": {"requests": [row]}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            path.write_text(json.dumps(snapshot), encoding="utf-8")
            loaded = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(g0_audit.metric_rows(loaded), [row])


if __name__ == "__main__":
    unittest.main()
