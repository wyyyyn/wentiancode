"""v0.6 · C33 · F45（任务 T73）

模式兜底表——四档权限模式 × 三类工具的「规则未命中时」兜底裁决。

这是五层判定流水线（F46）的第 4 层：当黑名单 / 沙箱 / 规则引擎都未给出
终局裁决时，由当前权限模式按工具类别给出兜底。其值域**严格为
{ALLOW, ASK}，绝不产 DENY**——DENY 只可能来自黑名单、沙箱、显式 deny
规则或人在回路拒绝（F46）。

表以 ``dict[Mode, dict[Category, Verdict]]`` 写死 spec F45 矩阵；新增一档
权限模式只需扩表即可（N18）。

分层铁律: pure leaf module — 只依赖 stdlib 与
:mod:`wentian.permissions.decision`，不碰任何 SDK / 终端 UI /
provider / agent / tools。
"""

from __future__ import annotations

from wentian.permissions.decision import Category, Mode, Verdict

__all__ = ["MODE_FALLBACK", "mode_fallback"]

_ALLOW = Verdict.ALLOW
_ASK = Verdict.ASK

#: spec F45 模式兜底矩阵（值域严格 {ALLOW, ASK}，绝不含 DENY）。
MODE_FALLBACK: dict[Mode, dict[Category, Verdict]] = {
    Mode.DEFAULT: {
        Category.READ_ONLY: _ALLOW,
        Category.FILE_WRITE: _ASK,
        Category.COMMAND_EXEC: _ASK,
    },
    Mode.ACCEPT_EDITS: {
        Category.READ_ONLY: _ALLOW,
        Category.FILE_WRITE: _ALLOW,
        Category.COMMAND_EXEC: _ASK,
    },
    Mode.PLAN: {
        Category.READ_ONLY: _ALLOW,
        Category.FILE_WRITE: _ASK,
        Category.COMMAND_EXEC: _ASK,
    },
    Mode.BYPASS: {
        Category.READ_ONLY: _ALLOW,
        Category.FILE_WRITE: _ALLOW,
        Category.COMMAND_EXEC: _ALLOW,
    },
}


def mode_fallback(mode: Mode, category: Category) -> Verdict:
    """返回 ``mode`` 档下 ``category`` 类工具的兜底裁决（恒 ∈ {ALLOW, ASK}）。"""
    return MODE_FALLBACK[mode][category]
