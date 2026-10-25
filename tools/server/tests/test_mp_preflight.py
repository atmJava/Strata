"""Preflight must catch an inconsistent state/queue before a round starts."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import preflight  # noqa: E402

QUEUE = """| ID | 项 | 判据 | 状态 | 证据 | 备注 |
|---|---|---|---|---|---|
| C1 | capacity | x | 待做 | | |
| S2 | metrics | y | 待做 | | |

未完成采纳项：2
"""

STATE = """- 当前项：C1（容量矩阵）
- 未完成采纳项：2（见 WORK_QUEUE）
- 环境：health 200
"""


def test_consistent_state_passes():
    assert preflight.check_state(preflight.read_state(STATE), QUEUE) == []


def test_state_count_mismatch_fails():
    bad = STATE.replace("未完成采纳项：2", "未完成采纳项：3")
    errors = preflight.check_state(preflight.read_state(bad), QUEUE)
    assert any("queue counts" in error for error in errors)


def test_current_item_must_be_in_queue():
    bad = STATE.replace("当前项：C1", "当前项：R99")
    errors = preflight.check_state(preflight.read_state(bad), QUEUE)
    assert any("not in the work queue" in error for error in errors)


def test_missing_current_item_fails():
    bad = STATE.replace("- 当前项：C1（容量矩阵）\n", "")
    errors = preflight.check_state(preflight.read_state(bad), QUEUE)
    assert any("当前项" in error for error in errors)
