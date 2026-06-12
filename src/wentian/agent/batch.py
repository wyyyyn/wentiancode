"""v0.4 · C16 · F32（任务 T51）

工具调用按安全性分批：一条模型回复携带多个工具调用时，连续的只读段
并发执行，副作用/未知/被拦截的调用各自串行执行；结果始终按原调用顺序
产出（[读A, 写B, 读C] 中 C 绝不能先于 B 的写入运行）。

只 import stdlib 与 ``wentian.providers.base``——agent 层绝不 import
``wentian.tools``；registry 以鸭子类型传入（``.get(name) -> tool|None``，
工具暴露 ``requires_confirmation: bool``）。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from wentian.providers.base import ToolCallEvent

__all__ = ["Kind", "Wave", "classify", "partition_waves", "run_wave"]


#: 工具调用安全分类四态。
Kind = Literal["read_only", "side_effect", "unknown", "blocked"]


def classify(
    call: ToolCallEvent,
    registry: object,
    allowed: frozenset[str] | None,
) -> Kind:
    """对单个工具调用做安全分类（优先级：unknown > blocked > 读写判定）。

    - 未注册 → ``unknown``；
    - ``allowed`` 名单外 → ``blocked``（plan 模式拦截）；
    - ``requires_confirmation`` 为 False → ``read_only``；
    - 其余（含属性缺失，fail-safe 默认 True）→ ``side_effect``。
    """
    tool = registry.get(call.name)  # type: ignore[attr-defined]
    if tool is None:
        return "unknown"
    if allowed is not None and call.name not in allowed:
        return "blocked"
    if not getattr(tool, "requires_confirmation", True):
        return "read_only"
    return "side_effect"


@dataclass(frozen=True)
class Wave:
    """一批将以同一策略执行的调用：并发（只读段）或串行（单个调用）。"""
    calls: tuple[ToolCallEvent, ...]
    concurrent: bool


def partition_waves(
    calls: Sequence[ToolCallEvent],
    registry: object,
    allowed: frozenset[str] | None,
) -> list[Wave]:
    """按原始顺序把调用切成 Wave 列表。

    连续的 ``read_only`` 段（≥2 个）合并为一个并发 Wave；单调用的读段
    没有并行收益，退化为串行 Wave。其余每个调用独占一个串行 Wave，
    保持原位置（[读A, 写B, 读C] 中 C 绝不能先于 B 运行）。
    """
    waves: list[Wave] = []
    read_run: list[ToolCallEvent] = []

    def flush_reads() -> None:
        if read_run:
            waves.append(
                Wave(calls=tuple(read_run), concurrent=len(read_run) > 1)
            )
            read_run.clear()

    for call in calls:
        if classify(call, registry, allowed) == "read_only":
            read_run.append(call)
        else:
            flush_reads()
            waves.append(Wave(calls=(call,), concurrent=False))
    flush_reads()
    return waves


async def run_wave(
    wave: Wave,
    run_call: Callable[[ToolCallEvent], Awaitable[object]],
) -> AsyncIterator[tuple[str, ToolCallEvent, object | None]]:
    """执行一个 Wave，按原调用顺序产出 ``(phase, call, outcome)`` 元组。

    并发 Wave：先为所有调用创建 asyncio 任务（使其真正并行），再按原
    顺序逐个 yield ``("started", call, None)`` 与 ``("result", call, outcome)``。

    串行 Wave：``started`` 在执行 ``run_call`` 之前产出——⏺ 行必须先于
    确认提示出现。
    """
    if wave.concurrent:
        tasks = [asyncio.ensure_future(run_call(call)) for call in wave.calls]
        for call, task in zip(wave.calls, tasks):
            yield ("started", call, None)
            outcome = await task
            yield ("result", call, outcome)
    else:
        for call in wave.calls:
            yield ("started", call, None)
            outcome = await run_call(call)
            yield ("result", call, outcome)
