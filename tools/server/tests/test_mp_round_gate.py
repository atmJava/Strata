"""The round gate must itself be tested: a gate that always passes is worse than none.

Cases mirror PORTING_RULES.md §3: an 11-field Gate sheet with '→ PASS' passes; missing fields,
empty required values, non-integer counts, an xfail/skip result and a missing Gate fail.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import round_gate  # noqa: E402

GOOD = """## R99 · 2026-10-25 · test round
- 改了什么：无
- 数字：n/a

### Gate（本轮）
- 轮次：R99
- 项：S2
- 验收判据：TTFT 字段出现在响应里
- 实现：serve/server.py:1
- 测试：`pytest tests/test_s2.py` → PASS
- 生产：health 200，md5 abc
- 证据：/tmp/run.log + probe.py
- 回滚：恢复快照 /tmp/a3（演练：是）
- 台账：L-999
- 剩余未完成项：1（S3）
- 没测什么：并发 >4
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


def test_empty_evidence_fails():
    broken = GOOD.replace("- 证据：/tmp/run.log + probe.py\n", "- 证据：\n")
    errors = round_gate.validate_log(broken)
    assert any("证据" in error for error in errors)


def test_rollback_required_when_production_changed():
    broken = GOOD.replace("- 回滚：恢复快照 /tmp/a3（演练：是）\n", "- 回滚：\n")
    errors = round_gate.validate_log(broken)
    assert any("回滚" in error for error in errors)


def test_remaining_count_must_be_integer():
    broken = GOOD.replace("- 剩余未完成项：1（S3）", "- 剩余未完成项：一些（S3）")
    errors = round_gate.validate_log(broken)
    assert any("integer" in error for error in errors)


def test_measurement_round_may_skip_implementation():
    measurement = GOOD.replace("- 项：S2", "- 项：测量：6 路并发").replace(
        "- 实现：serve/server.py:1", "- 实现：无")
    assert round_gate.validate_log(measurement) == []


def test_no_rounds_fails():
    assert round_gate.validate_log("nothing here\n")


def test_specific_round_selection():
    older = GOOD.replace("R99", "R98").replace("S2", "S1").replace("L-999", "L-998")
    text = GOOD + "\n" + older
    assert round_gate.validate_log(text, "R98") == []
    assert round_gate.validate_log(text, "R97") != []
