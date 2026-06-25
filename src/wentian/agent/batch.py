"""v0.4 · C16 · F32 (task T51)

Tool calls are batched by safety: when a model response carries multiple tool calls,
consecutive read-only segments run concurrently; side-effect/unknown/blocked calls
each run serially; results are always yielded in the original call order
([readA, writeB, readC]: C must never run before B's write completes).

Only imports stdlib and ``wentian.providers.base`` — the agent layer must never import
``wentian.tools``; registry is passed in as duck type (``.get(name) -> tool|None``,
tool exposes ``requires_confirmation: bool``).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from wentian.providers.base import ToolCallEvent

__all__ = ["Kind", "Wave", "classify", "partition_waves", "run_wave"]


#: Four states of tool call safety classification.
Kind = Literal["read_only", "side_effect", "unknown", "blocked"]


def classify(
    call: ToolCallEvent,
    registry: object,
    allowed: frozenset[str] | None,
) -> Kind:
    """Classify a single tool call by safety (priority: unknown > blocked > read/write decision).

    - Not registered → ``unknown``;
    - Outside ``allowed`` list → ``blocked`` (plan-mode intercept);
    - ``requires_confirmation`` is False → ``read_only``;
    - Otherwise (including missing attribute, fail-safe default True) → ``side_effect``.
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
    """A batch of calls to be executed with the same strategy: concurrent (read-only segment) or serial (single call)."""

    calls: tuple[ToolCallEvent, ...]
    concurrent: bool


def partition_waves(
    calls: Sequence[ToolCallEvent],
    registry: object,
    allowed: frozenset[str] | None,
) -> list[Wave]:
    """Split calls into a list of Waves in original order.

    Consecutive ``read_only`` segments (≥2) are merged into one concurrent Wave; a
    single-call read segment has no parallelism benefit and degrades to a serial Wave.
    Each remaining call gets its own serial Wave, preserving its original position
    ([readA, writeB, readC]: C must never run before B).
    """
    waves: list[Wave] = []
    read_run: list[ToolCallEvent] = []

    def flush_reads() -> None:
        if read_run:
            waves.append(Wave(calls=tuple(read_run), concurrent=len(read_run) > 1))
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
    """Execute a Wave, yielding ``(phase, call, outcome)`` tuples in the original call order.

    Concurrent Wave: first create asyncio tasks for all calls (making them truly
    parallel), then yield ``("started", call, None)`` and ``("result", call, outcome)``
    for each in original order.

    Serial Wave: ``started`` is yielded before executing ``run_call`` — the ⏺ line
    must appear before the confirmation prompt.
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
