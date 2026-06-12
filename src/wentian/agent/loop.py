"""v0.4 · C17 · F29（任务 T52）

AgentLoop：ReAct 多轮工具循环的调度核心。

每轮 = 流阶段（StreamBridge + RoundCollector 双路）→ 决策（无 tool_calls
即 COMPLETED 收束）→ 工具阶段（partition_waves 分批 + run_wave 按原序
产出，blocked 调用绝不触达 executor）→ 原子入史 → 下一轮。``run()``
**原地变更** *messages*；持久化归调用方（REPL）负责。

T52 只实现 COMPLETED 主路径；其余停止分支（MAX_ROUNDS / USER_CANCELLED /
UNKNOWN_TOOL_LOOP / STREAM_ERROR）由 T53 在既有结构上扩展。

只 import stdlib、``wentian.providers.base`` 与 ``wentian.agent.*``——
绝不 import ``wentian.tools``、rich、prompt_toolkit 或具体 provider；
registry / executor / interrupt_listener 一律鸭子类型传入。
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass

from wentian.agent.batch import classify, partition_waves, run_wave
from wentian.agent.bridge import StreamBridge, call_in_thread
from wentian.agent.collector import RoundCollector
from wentian.agent.events import (
    AgentDone,
    AgentEvent,
    RoundEnd,
    RoundStart,
    StopReason,
    StreamEnd,
    ToolCallStarted,
    ToolResultReady,
    UsageUpdate,
)
from wentian.providers.base import Message, ToolCallEvent, ToolSpec, Usage

__all__ = ["AgentLoop"]


@dataclass(frozen=True)
class _BlockedOutcome:
    """plan 模式拦截的合成结果——形状鸭子兼容 ToolOutcome，绝不触达 executor。

    ``denied=False``：拦截不是用户拒绝，而是模式限制（与 v0.3 确认流程的
    denied 语义区分开）。
    """
    call_id: str
    name: str
    content: str
    is_error: bool = True
    denied: bool = False


def _add_usage(total: Usage | None, round_usage: Usage) -> Usage:
    """跨轮累计 token 用量。"""
    if total is None:
        return round_usage
    return Usage(
        input_tokens=total.input_tokens + round_usage.input_tokens,
        output_tokens=total.output_tokens + round_usage.output_tokens,
    )


class AgentLoop:
    """ReAct 多轮循环调度器（事件以 AsyncIterator[AgentEvent] 产出）。"""

    def __init__(
        self,
        provider,
        *,
        registry: object | None = None,
        executor: object | None = None,
        interrupt_listener: object | None = None,
        max_rounds: int = 20,
        unknown_streak_limit: int = 2,
        allowed_tools: frozenset[str] | None = None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._executor = executor
        self._interrupt_listener = interrupt_listener
        self._max_rounds = max_rounds
        self._unknown_streak_limit = unknown_streak_limit
        self._allowed_tools = allowed_tools

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    async def run(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """运行多轮循环直到收束；**原地变更** *messages*（持久化归调用方）。"""
        total_usage: Usage | None = None

        for n in range(1, self._max_rounds + 1):
            yield RoundStart(n)

            # --- 流阶段：listener 只在这里武装（工具阶段不可中断） ---
            collector = RoundCollector()
            listener_ctx = (
                self._interrupt_listener
                if self._interrupt_listener is not None
                else contextlib.nullcontext(None)
            )
            interrupted = False
            with listener_ctx as interrupt_event:
                bridge = StreamBridge(
                    self._provider.stream(messages, system=system, tools=tools)
                )
                async for ev in bridge.drain(interrupt_event):
                    shown = collector.feed(ev)
                    if shown is not None:
                        yield shown
                interrupted = (
                    interrupt_event is not None and interrupt_event.is_set()
                )

            round_result = collector.result(interrupted=interrupted)
            if round_result.usage is not None:
                total_usage = _add_usage(total_usage, round_result.usage)
                yield UsageUpdate(round_usage=round_result.usage, total=total_usage)
            yield StreamEnd(n, round_result.text, interrupted)

            # --- 决策：无 tool_calls → COMPLETED 收束（T53 扩展其余分支） ---
            if not round_result.tool_calls:
                messages.append(
                    {"role": "assistant", "content": round_result.text}
                )
                yield AgentDone(
                    StopReason.COMPLETED,
                    text=round_result.text,
                    rounds=n,
                    usage=total_usage,
                )
                return

            # --- 工具阶段：分批执行，结果按原调用顺序收集 ---
            async def run_call(call: ToolCallEvent) -> object:
                # blocked 调用绝不触达 executor——直接合成拦截结果。
                if classify(call, self._registry, self._allowed_tools) == "blocked":
                    return self._make_blocked_outcome(call)
                return await call_in_thread(
                    self._executor.execute, call.id, call.name, call.arguments
                )

            results: list[tuple[ToolCallEvent, object]] = []
            waves = partition_waves(
                round_result.tool_calls, self._registry, self._allowed_tools
            )
            for wave in waves:
                async for kind, call, outcome in run_wave(wave, run_call):
                    if kind == "started":
                        yield ToolCallStarted(call)
                    else:  # "result"
                        yield ToolResultReady(outcome)
                        results.append((call, outcome))

            # --- 原子入史：全部结果收齐后一次性追加（v0.3 契约：
            #     executor 收原始 arguments 含 None，入史 None→{}） ---
            assistant_msg: Message = {
                "role": "assistant",
                "content": round_result.text,
                "tool_calls": [
                    {
                        "id": call.id,
                        "name": call.name,
                        "arguments": (
                            call.arguments if call.arguments is not None else {}
                        ),
                    }
                    for call in round_result.tool_calls
                ],
            }
            if round_result.raw_content is not None:
                assistant_msg["raw_content"] = round_result.raw_content
            messages.append(assistant_msg)
            # results 已按原调用顺序排列（run_wave 按 wave 原序产出，
            # partition_waves 保持全局原序）。
            for call, outcome in results:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": outcome.content,  # type: ignore[attr-defined]
                        "is_error": outcome.is_error,  # type: ignore[attr-defined]
                    }
                )

            yield RoundEnd(n, tool_results=len(results))

        # for 循环耗尽 = 达到 max_rounds 上限——T53 在此落 MAX_ROUNDS 分支。

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _make_blocked_outcome(self, call: ToolCallEvent) -> _BlockedOutcome:
        """合成 plan 模式拦截结果（提示模型当前限制与退出方式）。"""
        allowed = ", ".join(sorted(self._allowed_tools or ())) or "（无）"
        return _BlockedOutcome(
            call_id=call.id,
            name=call.name,
            content=(
                f"工具 {call.name} 在 plan 模式下被拦截：当前只允许只读工具"
                f"（{allowed}）。如需执行修改类操作，请先让用户用 /do 退出"
                f" plan 模式。"
            ),
        )
