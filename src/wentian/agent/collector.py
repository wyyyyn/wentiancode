"""v0.4 · C14 · F31（任务 T49）

RoundCollector：单轮流式回复的双路收集器。

v0.4 把 v0.3 渲染器"边消费边累积"的双重职责拆开：collector 在 agent 层
负责累积（text / tool_calls / usage / raw_content），同时把需要实时显示的
事件原样透传给上层渲染（F31 双路）。

只 import stdlib 与 ``wentian.providers.base`` / ``wentian.agent.events``——
绝不 import ``wentian.tools``、rich 或 prompt_toolkit。
"""

from __future__ import annotations

from wentian.agent.events import RoundResult
from wentian.providers.base import (
    Done,
    StreamEvent,
    TextDelta,
    ThinkingDelta,
    ToolCallEvent,
    Usage,
)

__all__ = ["RoundCollector"]


class RoundCollector:
    """双路收集器：feed() 把需要实时显示的事件原样返回（其余返回 None），
    同时完整累积本轮回复供循环决策（F31）。"""

    def __init__(self) -> None:
        self._text_parts: list[str] = []
        self._tool_calls: list[ToolCallEvent] = []
        self._usage: Usage | None = None
        self._raw_content: list[dict] | None = None
        self._done_seen: bool = False

    def feed(self, event: StreamEvent) -> ThinkingDelta | TextDelta | None:
        """消费一个流事件；返回需要实时显示的事件，其余返回 None。"""
        if isinstance(event, TextDelta):
            # 追加进 text 缓冲，并走实时显示路径。
            self._text_parts.append(event.text)
            return event
        if isinstance(event, ThinkingDelta):
            # 透传显示，不计入 text。
            return event
        if isinstance(event, ToolCallEvent):
            # 按序静默收集（v0.3 同规：流中不渲染工具调用）。
            self._tool_calls.append(event)
            return None
        if isinstance(event, Done):
            self._usage = event.usage
            self._raw_content = event.raw_content
            self._done_seen = True
            return None
        return None  # pragma: no cover - StreamEvent 联合已覆盖全部类型

    def result(self, *, interrupted: bool) -> RoundResult:
        """汇总本轮结果。

        RoundResult 没有 interrupted 字段；done_seen=False 即表示中断或
        异常提前终止。interrupted=True 时无论是否见过 Done，done_seen
        一律为 False（中断优先）。
        """
        return RoundResult(
            text="".join(self._text_parts),
            tool_calls=tuple(self._tool_calls),
            raw_content=self._raw_content,
            usage=self._usage,
            done_seen=self._done_seen and not interrupted,
        )
