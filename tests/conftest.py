"""Shared test fixtures including FakeProvider."""
import asyncio
import threading

import pytest
from typing import Iterator

from wentian.providers.base import (
    Provider,
    ToolSpec,
    StreamEvent,
    ThinkingDelta,
    TextDelta,
    Done,
    Message,
)


def run_to_list(aiter) -> list:
    """把一个异步迭代器在新事件循环里收集成 list（纯 pytest，无 pytest-asyncio）。"""

    async def _collect() -> list:
        return [item async for item in aiter]

    return asyncio.run(_collect())


class FakeProvider(Provider):
    """A deterministic fake backend for offline tests.

    Constructed with a preset list of StreamEvents; stream() records the
    messages it receives on self.calls and yields the preset events in order.
    Satisfies AC8: 'fake backend' that subclasses Provider.
    """

    name = "fake"

    def __init__(self, events: list[StreamEvent]) -> None:
        self._events = events
        self.calls: list[list[Message]] = []

    def stream(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
    ) -> Iterator[StreamEvent]:
        self.calls.append(messages)
        yield from self._events


class BlockingFakeProvider(Provider):
    """v0.2 · C6 · F18（任务 T22）— teaching marker.

    Yields the preset events, then blocks forever on a threading.Event that
    is never set — simulating a network read that hangs after partial
    content. NOTE: the daemon pump thread reading this provider leaks in
    tests (it stays blocked on the never-set Event); acceptable because
    daemon threads are reaped at process exit.
    """

    name = "blocking-fake"

    def __init__(self, events: list[StreamEvent]) -> None:
        self._events = events
        self._block = threading.Event()  # never set

    def stream(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
    ) -> Iterator[StreamEvent]:
        yield from self._events
        self._block.wait()  # blocks forever — pump thread dangles (daemon)


class ScriptedProvider(Provider):
    """v0.3 · C12 · F23（任务 T42/T43）— scripted tool-aware fake.

    Constructed with a list of "scripts" — one event list per stream() call.
    Records each call's (messages, system, tools) so tests can assert the
    round-2 call sees full history + the same system/tools kwargs. Accepts
    the v0.3 tools= kwarg (None when tools disabled).

    v0.4（任务 T52）从 tests/test_repl.py 原样提升至 conftest，并补记
    systems_seen，供 agent loop 测试断言 system 透传。
    """

    name = "scripted"

    def __init__(self, scripts: list[list[StreamEvent]]) -> None:
        self._scripts = scripts
        self.calls: list[list[Message]] = []
        self.tools_seen: list[object] = []
        self.systems_seen: list[str | None] = []

    def stream(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools=None,
    ) -> Iterator[StreamEvent]:
        idx = len(self.calls)
        self.calls.append([dict(m) for m in messages])
        self.tools_seen.append(tools)
        self.systems_seen.append(system)
        script = self._scripts[idx] if idx < len(self._scripts) else []
        yield from script


class FakeListener:
    """v0.2 · C6 · F18（任务 T23）— InterruptListener 测试替身。

    Hands out a FRESH threading.Event per __enter__ (so a set Event from an
    interrupted round never leaks into the next round) and counts
    enter/exit so tests can assert with-block balance — including the error
    path, where __exit__ must still run.

    The optional *arm* hook is called with each fresh Event right before it
    is returned; tests use it to pre-set the Event (zero-text interrupt) or
    schedule a ``threading.Timer`` (partial interrupt), possibly varying by
    round via a closure counter.
    """

    def __init__(self, arm=None) -> None:
        self._arm = arm
        self.enter_count = 0
        self.exit_count = 0
        self.last_event: threading.Event | None = None

    def __enter__(self) -> threading.Event:
        self.enter_count += 1
        event = threading.Event()
        self.last_event = event
        if self._arm is not None:
            self._arm(event)
        return event

    def __exit__(self, *exc) -> None:
        self.exit_count += 1


# --- Fixtures ---

@pytest.fixture
def fake_provider() -> FakeProvider:
    """A FakeProvider preset with [ThinkingDelta('a'), TextDelta('b'), Done(None)]."""
    return FakeProvider([ThinkingDelta("a"), TextDelta("b"), Done(None)])


@pytest.fixture
def fake_provider_factory():
    """Returns the FakeProvider class itself so tests can construct custom instances."""
    return FakeProvider
