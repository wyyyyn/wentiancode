"""v0.12 · hooks 包公开接口。

仅导出 spec.py 的公开符号；不 import 任何其他 hooks 子模块
（conditions / config / actions / engine 由后续波次独立实现）。
"""

from wentian.hooks.spec import (
    INTERCEPT_EVENTS,
    Action,
    Clause,
    Condition,
    HookEvent,
    HookRule,
    HttpAction,
    Match,
    PromptAction,
    ShellAction,
    SubAgentAction,
)

__all__ = [
    "HookEvent",
    "INTERCEPT_EVENTS",
    "Match",
    "Clause",
    "Condition",
    "ShellAction",
    "PromptAction",
    "HttpAction",
    "SubAgentAction",
    "Action",
    "HookRule",
]
