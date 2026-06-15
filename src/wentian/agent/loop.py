"""v0.5 · C25 · F39/F40（任务 T64）— request_decorator + 缓存字段累计
v0.4 · C17 · F29（任务 T52；T53 补五停机分支）

AgentLoop：ReAct 多轮工具循环的调度核心。

每轮 = 流阶段（StreamBridge + RoundCollector 双路）→ 决策（停机判定）→
工具阶段（partition_waves 分批 + run_wave 按原序产出，blocked 调用绝不
触达 executor）→ 原子入史 → 下一轮。``run()`` **原地变更** *messages*；
持久化归调用方（REPL）负责。

五种停机（StopReason，AgentDone 永远是最后一个事件）：

- COMPLETED — 某轮无 tool_calls，正常收束；
- USER_CANCELLED — 流阶段被中断：有部分文字则只存 assistant 文本（丢弃
  tool_calls），零文字则该轮零入史；
- STREAM_ERROR — 流抛异常：整轮丢弃（部分文字也不入史），异常绝不逃逸
  ``run()``，错误信息进 ``AgentDone.error``；
- MAX_ROUNDS — 第 max_rounds 轮仍要工具：不执行（失控刹车，刹车点不再
  发"最后一批"），只存文本（存未执行的 tool_calls 会被 API 400 拒收）；
- UNKNOWN_TOOL_LOOP — 连续 ``unknown_streak_limit`` 轮（默认 2）的调用
  全部是未注册名：错误结果照常按对入史（历史对下个用户轮保持一致）后
  停机。blocked（计划模式拦截）刻意不计入连击。

只 import stdlib、``wentian.providers.base`` 与 ``wentian.agent.*``——
绝不 import ``wentian.tools``、rich、prompt_toolkit 或具体 provider；
registry / executor / interrupt_listener 一律鸭子类型传入。
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Callable
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
    """跨轮累计 token 用量（含缓存字段）。"""
    if total is None:
        return round_usage
    return Usage(
        input_tokens=total.input_tokens + round_usage.input_tokens,
        output_tokens=total.output_tokens + round_usage.output_tokens,
        cache_creation_input_tokens=(
            total.cache_creation_input_tokens
            + round_usage.cache_creation_input_tokens
        ),
        cache_read_input_tokens=(
            total.cache_read_input_tokens
            + round_usage.cache_read_input_tokens
        ),
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
        request_decorator: Callable[[list[Message], int], list[Message]] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """运行多轮循环直到收束；**原地变更** *messages*（持久化归调用方）。

        *request_decorator(messages, round_index) -> messages*（可选）：
        每轮调用 provider 之前，用它算出本轮实际发出的 outgoing messages；
        入史、持久化、决策仍只对 messages 原件操作——decorator 产出只喂给
        本次 stream，绝不写回 messages，也不作为后续轮次的基础（每轮重算）。
        """
        total_usage: Usage | None = None
        unknown_streak = 0

        for n in range(1, self._max_rounds + 1):
            yield RoundStart(n)

            # --- 每轮重算 outgoing：decorator 只影响本次 provider 调用 ---
            outgoing = request_decorator(messages, n) if request_decorator is not None else messages

            # --- 流阶段：listener 只在这里武装（工具阶段不可中断） ---
            collector = RoundCollector()
            listener_ctx = (
                self._interrupt_listener
                if self._interrupt_listener is not None
                else contextlib.nullcontext(None)
            )
            interrupted = False
            stream_error: Exception | None = None
            with listener_ctx as interrupt_event:
                bridge = StreamBridge(
                    self._provider.stream(outgoing, system=system, tools=tools)
                )
                try:
                    async for ev in bridge.drain(interrupt_event):
                        shown = collector.feed(ev)
                        if shown is not None:
                            yield shown
                except Exception as exc:  # noqa: BLE001 — 流错误绝不逃逸 run()
                    stream_error = exc
                interrupted = (
                    interrupt_event is not None and interrupt_event.is_set()
                )

            # --- 停机：STREAM_ERROR——整轮丢弃（部分文字也不入史），
            #     此前各轮的原子块已在 messages 里 ---
            if stream_error is not None:
                yield AgentDone(
                    StopReason.STREAM_ERROR,
                    text="",
                    rounds=n,
                    usage=total_usage,
                    error=str(stream_error),
                )
                return

            round_result = collector.result(interrupted=interrupted)
            if round_result.usage is not None:
                total_usage = _add_usage(total_usage, round_result.usage)
                yield UsageUpdate(round_usage=round_result.usage, total=total_usage)
            yield StreamEnd(n, round_result.text, interrupted)

            # --- 停机：USER_CANCELLED——有部分文字只存文本（丢弃
            #     tool_calls），零文字零入史 ---
            if interrupted:
                if round_result.text:
                    messages.append(
                        {"role": "assistant", "content": round_result.text}
                    )
                yield AgentDone(
                    StopReason.USER_CANCELLED,
                    text=round_result.text,
                    rounds=n,
                    usage=total_usage,
                )
                return

            # --- 停机：COMPLETED——无 tool_calls，正常收束 ---
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

            # --- 停机：MAX_ROUNDS——最后一轮仍要工具：失控刹车，不执行，
            #     只存文本（存未执行的 tool_calls 会被 API 400 拒收） ---
            if n == self._max_rounds:
                messages.append(
                    {"role": "assistant", "content": round_result.text}
                )
                yield AgentDone(
                    StopReason.MAX_ROUNDS,
                    text=round_result.text,
                    rounds=n,
                    usage=total_usage,
                )
                return

            # --- unknown 连击记账：调用非空且全部 unknown 才计一轮；
            #     其他工具轮清零。blocked 刻意不计入（计划模式拦截不该
            #     被当成模型幻觉工具名） ---
            kinds = [
                classify(call, self._registry, self._allowed_tools)
                for call in round_result.tool_calls
            ]
            if kinds and all(kind == "unknown" for kind in kinds):
                unknown_streak += 1
            else:
                unknown_streak = 0

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

            # --- 停机：UNKNOWN_TOOL_LOOP——结果先入史、RoundEnd 先发，
            #     历史对下个用户轮保持一致，然后才判停 ---
            if unknown_streak >= self._unknown_streak_limit:
                yield AgentDone(
                    StopReason.UNKNOWN_TOOL_LOOP,
                    text=round_result.text,
                    rounds=n,
                    usage=total_usage,
                )
                return

        # 不可达：第 max_rounds 轮要么 COMPLETED/USER_CANCELLED/STREAM_ERROR
        # 收束，要么命中 MAX_ROUNDS 刹车——循环体内必 return。

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _make_blocked_outcome(self, call: ToolCallEvent) -> _BlockedOutcome:
        """合成计划模式拦截结果（提示模型当前限制与退出方式）。"""
        allowed = ", ".join(sorted(self._allowed_tools or ())) or "（无）"
        return _BlockedOutcome(
            call_id=call.id,
            name=call.name,
            content=(
                f"工具 {call.name} 在计划模式（plan mode）下被拦截：当前只"
                f"允许只读工具（{allowed}）。如需执行修改类操作，请先让"
                f"用户用 /do 退出计划模式。"
            ),
        )
