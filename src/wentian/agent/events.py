"""v0.4 · C14 · F30（任务 T47）

Agent Loop 事件契约：StopReason、AgentEvent 联合与 RoundResult。

本模块是 agent 层与上层（REPL/render）之间的唯一事件契约；v0.4 其余
agent 模块（流桥接、轮次执行、循环调度）都只依赖这里定义的类型。

只 import stdlib 与 ``wentian.providers.base``——agent 层绝不 import
``wentian.tools``（工具结果经 ``ToolResultReady.outcome`` 以鸭子类型传递）。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from wentian.providers.base import TextDelta, ThinkingDelta, ToolCallEvent, Usage

__all__ = [
    "StopReason",
    "RoundStart",
    "UsageUpdate",
    "StreamEnd",
    "ToolCallStarted",
    "ToolResultReady",
    "RoundEnd",
    "AgentDone",
    "AgentEvent",
    "RoundResult",
]


# ---------------------------------------------------------------------------
# 停止原因
# ---------------------------------------------------------------------------

class StopReason(enum.Enum):
    """Agent 循环终止原因（AgentDone.stop_reason）。"""
    COMPLETED = "completed"
    MAX_ROUNDS = "max_rounds"
    USER_CANCELLED = "user_cancelled"
    UNKNOWN_TOOL_LOOP = "unknown_tool_loop"
    STREAM_ERROR = "stream_error"


# ---------------------------------------------------------------------------
# Agent 事件
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class RoundStart:
    """一轮（round）开始；``index`` 为 1-based 轮次序号。"""
    index: int


@dataclass(frozen=True, slots=True)
class UsageUpdate:
    """token 用量更新：本轮用量与跨轮累计总量。"""
    round_usage: Usage
    total: Usage


@dataclass(frozen=True, slots=True)
class StreamEnd:
    """本轮模型流结束；``interrupted`` 标记是否被用户中断。"""
    index: int
    text: str
    interrupted: bool


@dataclass(frozen=True, slots=True)
class ToolCallStarted:
    """开始执行一个模型请求的工具调用。"""
    call: ToolCallEvent


@dataclass(frozen=True, slots=True)
class ToolResultReady:
    """一个工具调用执行完毕。

    ``outcome`` 为鸭子类型的 ToolOutcome 形对象——agent 层绝不 import
    ``wentian.tools``，故此处声明为 ``object``。
    """
    outcome: object


@dataclass(frozen=True, slots=True)
class RoundEnd:
    """一轮结束；``tool_results`` 为本轮产生的工具结果数。"""
    index: int
    tool_results: int


@dataclass(frozen=True, slots=True)
class AgentDone:
    """Agent 循环整体结束；永远是事件流的最后一个事件。"""
    stop_reason: StopReason
    text: str
    rounds: int
    usage: Usage | None
    error: str | None = None


# ThinkingDelta / TextDelta 直接复用 providers.base 的定义，不在此重定义。
AgentEvent = (RoundStart | ThinkingDelta | TextDelta | UsageUpdate | StreamEnd
              | ToolCallStarted | ToolResultReady | RoundEnd | AgentDone)


# ---------------------------------------------------------------------------
# 单轮结果
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RoundResult:
    """单轮流式调用的汇总结果（供循环调度层在轮间传递）。

    ``done_seen`` 标记本轮流是否收到了 Done 事件（False 表示流被中断或
    异常提前结束）。
    """
    text: str
    tool_calls: tuple[ToolCallEvent, ...]
    raw_content: list | None
    usage: Usage | None
    done_seen: bool
