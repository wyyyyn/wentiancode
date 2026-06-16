"""v0.6 · C33 · F45（任务 T73）

模式兜底表测试——四档 × 三类共 12 格逐格断言（spec F45 矩阵），
外加值域恒为 {ALLOW, ASK}、绝不产 DENY 的硬约束。
"""
from __future__ import annotations

import pytest

from wentian.permissions.decision import Category, Mode, Verdict
from wentian.permissions.modes import mode_fallback

A = Verdict.ALLOW
K = Verdict.ASK

# spec F45 矩阵：四档 × 三类，逐格期望值。
_EXPECTED = [
    # default：只读 Allow / 文件写 Ask / 命令 Ask
    (Mode.DEFAULT, Category.READ_ONLY, A),
    (Mode.DEFAULT, Category.FILE_WRITE, K),
    (Mode.DEFAULT, Category.COMMAND_EXEC, K),
    # acceptEdits：只读 Allow / 文件写 Allow / 命令 Ask
    (Mode.ACCEPT_EDITS, Category.READ_ONLY, A),
    (Mode.ACCEPT_EDITS, Category.FILE_WRITE, A),
    (Mode.ACCEPT_EDITS, Category.COMMAND_EXEC, K),
    # plan：只读 Allow / 文件写 Ask / 命令 Ask
    (Mode.PLAN, Category.READ_ONLY, A),
    (Mode.PLAN, Category.FILE_WRITE, K),
    (Mode.PLAN, Category.COMMAND_EXEC, K),
    # bypassPermissions：全 Allow
    (Mode.BYPASS, Category.READ_ONLY, A),
    (Mode.BYPASS, Category.FILE_WRITE, A),
    (Mode.BYPASS, Category.COMMAND_EXEC, A),
]


@pytest.mark.parametrize("mode, category, expected", _EXPECTED)
def test_mode_fallback_matrix(mode: Mode, category: Category, expected: Verdict) -> None:
    """12 格逐格断言：每个 (模式, 类别) 命中 spec F45 矩阵期望裁决。"""
    assert mode_fallback(mode, category) is expected


def test_matrix_covers_all_twelve_cells() -> None:
    """覆盖完整性：恰好 4 档 × 3 类 = 12 格。"""
    assert len(_EXPECTED) == 12
    assert len(Mode) == 4
    assert len(Category) == 3


@pytest.mark.parametrize("mode", list(Mode))
@pytest.mark.parametrize("category", list(Category))
def test_value_range_is_allow_or_ask_never_deny(mode: Mode, category: Category) -> None:
    """值域硬约束：兜底裁决恒 ∈ {ALLOW, ASK}，绝不产 DENY。"""
    verdict = mode_fallback(mode, category)
    assert verdict in {Verdict.ALLOW, Verdict.ASK}
    assert verdict is not Verdict.DENY
