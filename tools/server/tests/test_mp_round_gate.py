"""The round gate must itself be tested: a gate that always passes is worse than none.

Cases mirror docs/MULTIPATH_RULES.md v0.2: a Gate sheet with all keys and '→ PASS' passes;
missing keys, a missing Gate, or an xfail/skip result fail.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import round_gate  # noqa: E402

GOOD = """## R99 · 2026-10-25 · test round
- 改了什么：无
- 数字：n/a

### Gate（本轮）
- 项：S2
- 验收判据：TTFT 字段出现在响应里
- 实现：serve/server.py:1
- 测试：`pytest tests/test_s2.py` → PASS
- 生产：health 200，md5 abc
- 台账：L-999
- 剩余未完成采纳项：1（S3）
"""


def test_good_round_passes():
    assert round_gate.validate_log(GOOD) == []


def test_missing_gate_fails():
    errors = round_gate.validate_log("## R1 · x\n- 改了什么：无\n")
    assert errors and "### Gate" in errors[0]


def test_missing_key_fails():
    broken = GOOD.replace("- 台账：L-999\n", "")
    errors = round_gate.validate_log(broken)
    assert any("台账" in error for error in errors)


def test_xfail_is_not_pass():
    broken = GOOD.replace("→ PASS", "→ XFAIL")
    errors = round_gate.validate_log(broken)
    assert errors and any("PASS" in error for error in errors)


def test_skip_is_not_pass():
    broken = GOOD.replace("→ PASS", "→ PASS, skip")
    errors = round_gate.validate_log(broken)
    assert errors


def test_no_rounds_fails():
    assert round_gate.validate_log("nothing here\n")


def test_specific_round_selection():
    older = GOOD.replace("R99", "R98").replace("S2", "S1").replace("L-999", "L-998")
    text = GOOD + "\n" + older
    assert round_gate.validate_log(text, "R98") == []
    assert round_gate.validate_log(text, "R97") != []
