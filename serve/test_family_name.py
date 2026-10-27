"""serve/test_family_name.py - the API's model name for the supported model.

The config's ``model_name`` wins; without one, the server reports Qwen3.8-Flash-Next.

    python -m unittest serve.test_family_name
"""
from __future__ import annotations

import unittest

from serve.server import FAMILY_MODEL_NAMES, default_model_name


class FamilyName(unittest.TestCase):
    def test_config_name_wins(self):
        self.assertEqual(default_model_name({"model_name": "qwen3.8-flash-next-q2_0", "arch": "qwen4exp"}),
                         "qwen3.8-flash-next-q2_0")

    def test_arch_name(self):
        self.assertEqual(default_model_name({"arch": "qwen4exp"}), "qwen3.8-flash-next")

    def test_old_configs(self):
        self.assertEqual(default_model_name({}), "qwen3.8-flash-next")
        self.assertEqual(default_model_name({"arch": "something-else"}), "qwen3.8-flash-next")
        self.assertEqual(set(FAMILY_MODEL_NAMES), {"qwen4exp"})


if __name__ == "__main__":
    unittest.main()
