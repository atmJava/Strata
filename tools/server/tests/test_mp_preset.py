"""T7 - the preset gate refuses wrong configs (docs/FEATURE_PARITY.md F6, R8 negative cases).

`check_preset.py` is a pure function over a config dict, so these cases need no server.
They prove the gate is not a rubber stamp: each broken preset must produce at least one error,
and the production shape must pass.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import check_preset  # noqa: E402


def _config(**overrides):
    args = ["--batch", "4", "--batch-groups", "2", "--max-context", "256000", "--kv-resident", "32768",
            "--conversation-cache-mib", "12288", "--vram-reserve-mib", "1280"]
    config = {"parallel": 4, "args": args}
    config.update(overrides)
    return config


def test_good_preset_passes():
    errors, _notes = check_preset.check(_config(), None, "111")
    assert errors == []


def test_batch_must_equal_parallel():
    errors, _ = check_preset.check(_config(args=["--batch", "2", "--max-context", "256000"]), None, "111")
    assert errors and "--batch 2 != parallel 4" in errors[0]


def test_groups_must_divide_parallel():
    errors, _ = check_preset.check(_config(args=["--batch", "4", "--batch-groups", "3",
                                                 "--max-context", "256000"]), None, "111")
    assert errors and "must divide parallel 4" in errors[0]


def test_context_above_the_model_maximum():
    errors, _ = check_preset.check(_config(args=["--batch", "4", "--max-context", "999999"]), None, "111")
    assert errors and "999999" in errors[0]


def test_conversation_cache_floor():
    errors, _ = check_preset.check(_config(args=["--batch", "4", "--max-context", "256000",
                                                 "--conversation-cache-mib", "1024"]), None, "111")
    assert errors and "conversation-cache-mib" in errors[0]


def test_kv_resident_floor():
    errors, _ = check_preset.check(_config(args=["--batch", "4", "--max-context", "256000",
                                                 "--kv-resident", "4096"]), None, "111")
    assert errors and "kv-resident" in errors[0]
