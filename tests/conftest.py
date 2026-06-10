"""Shared test fixtures including FakeProvider."""
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


# --- Fixtures ---

@pytest.fixture
def fake_provider() -> FakeProvider:
    """A FakeProvider preset with [ThinkingDelta('a'), TextDelta('b'), Done(None)]."""
    return FakeProvider([ThinkingDelta("a"), TextDelta("b"), Done(None)])


@pytest.fixture
def fake_provider_factory():
    """Returns the FakeProvider class itself so tests can construct custom instances."""
    return FakeProvider
