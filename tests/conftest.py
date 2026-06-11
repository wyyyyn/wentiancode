"""Shared test fixtures including FakeProvider."""
import threading

import pytest
from typing import Iterator

from wentian.providers.base import (
    Provider,
    StreamEvent,
    ThinkingDelta,
    TextDelta,
    Done,
    Message,
)


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
        self, messages: list[Message], *, system: str | None = None
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
        self, messages: list[Message], *, system: str | None = None
    ) -> Iterator[StreamEvent]:
        yield from self._events
        self._block.wait()  # blocks forever — pump thread dangles (daemon)


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
