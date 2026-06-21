"""v0.12 · C94 · F77/F78/F81/F82（任务 T118）— Hook 规则模型。

纯叶子模块：HookEvent 枚举(10) + INTERCEPT_EVENTS + Match/Clause/Condition +
四 Action dataclass + Action 联合别名 + HookRule。

分层铁律（N41）：仅 stdlib（dataclasses / enum / typing）；零运行时业务 import。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Union

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


# ---------------------------------------------------------------------------
# 事件枚举 —— 十个生命周期事件
# ---------------------------------------------------------------------------


class HookEvent(Enum):
    SESSION_START = "SessionStart"  # 会话级
    SESSION_END = "SessionEnd"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"  # 消息级
    STOP = "Stop"
    ROUND_START = "RoundStart"  # 轮次级
    ROUND_END = "RoundEnd"
    PRE_TOOL_USE = "PreToolUse"  # 工具级（唯一可拦截事件）
    POST_TOOL_USE = "PostToolUse"
    PRE_COMPACT = "PreCompact"  # 系统级
    NOTIFICATION = "Notification"


# 拦截类事件集：禁止 background=True（F82）
INTERCEPT_EVENTS: frozenset[HookEvent] = frozenset({HookEvent.PRE_TOOL_USE})


# ---------------------------------------------------------------------------
# 条件模型
# ---------------------------------------------------------------------------


class Match(Enum):
    ALL = "all"  # 所有子句均须命中
    ANY = "any"  # 任一子句命中即可


@dataclass(frozen=True)
class Clause:
    """单个条件子句：取 ctx[field] 与 pattern 做 match_one 判定。"""

    field: str
    pattern: str


@dataclass(frozen=True)
class Condition:
    """条件集合；clauses 为空时恒真（无条件触发）。"""

    match: Match = Match.ALL
    clauses: tuple[Clause, ...] = ()


# ---------------------------------------------------------------------------
# Action dataclasses —— 四种执行动作
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ShellAction:
    """执行 shell 命令；stdin 注入 context JSON。"""

    command: str
    timeout: int | None = None


@dataclass(frozen=True)
class PromptAction:
    """向下次请求注入 system-reminder 文本；支持 {field} 占位符。"""

    text: str


@dataclass(frozen=True)
class HttpAction:
    """向 url 发送 HTTP 请求，body 为 context JSON。"""

    url: str
    method: str = "POST"
    timeout: int | None = None


@dataclass(frozen=True)
class SubAgentAction:
    """子 Agent 动作（本版占位，不执行实际子 Agent）。"""

    prompt: str = ""


# Action 联合别名（类型注释与运行时均可用）
Action = Union[ShellAction, PromptAction, HttpAction, SubAgentAction]


# ---------------------------------------------------------------------------
# HookRule —— 规则三要素 + 执行控制
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HookRule:
    """一条 Hook 规则：事件 + 动作 + 可选条件 + 执行控制。"""

    event: HookEvent
    action: Action  # type: ignore[type-arg]
    condition: Condition | None = None  # None → 无条件（恒触发）
    once: bool = False  # True → 整个会话内只触发一次
    background: bool = False  # True → 异步 daemon 线程；拦截事件上禁用
