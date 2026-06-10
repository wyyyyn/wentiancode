"""Tests for REPL (T8, T9, T10)."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from typing import Iterator

from rich.console import Console

from wentian.providers.base import (
    Provider,
    StreamEvent,
    TextDelta,
    Done,
    Message,
)
from wentian.session import Session, SessionStore
from wentian.render import Renderer
from wentian.config import ConfigError


# ---------------------------------------------------------------------------
# Helpers / tiny fakes
# ---------------------------------------------------------------------------

class FakeProvider(Provider):
    """Deterministic fake: records received messages, yields preset events."""

    name = "fake"

    def __init__(self, events: list[StreamEvent]) -> None:
        self._events = events
        self.calls: list[list[Message]] = []

    def stream(
        self, messages: list[Message], *, system: str | None = None
    ) -> Iterator[StreamEvent]:
        self.calls.append(list(messages))
        yield from self._events


def _make_repl(
    provider: Provider,
    store: SessionStore,
    console: Console,
    *,
    session: Session | None = None,
    inputs: list[str],
    provider_factory=None,
):
    """Assemble a REPL with injected fakes. Returns (repl, session)."""
    from wentian.repl import REPL

    if session is None:
        session = store.create(provider=provider.name)

    renderer = Renderer(console)

    input_iter = iter(inputs)

    def _input_fn(prompt: str = "") -> str:
        return next(input_iter)

    if provider_factory is None:
        def provider_factory(name: str) -> Provider:
            raise ConfigError(f"unknown provider: {name}")

    repl = REPL(
        provider=provider,
        session=session,
        store=store,
        renderer=renderer,
        provider_factory=provider_factory,
        input_fn=_input_fn,
    )
    return repl, session


# ===========================================================================
# T8 — one-round conversation
# ===========================================================================

class TestReplOneTurn:
    def test_run_exits_on_slash_exit(self, tmp_path):
        """run() returns normally when /exit is entered."""
        provider = FakeProvider([TextDelta("回答"), Done()])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(
            provider, store, console, inputs=["你好", "/exit"]
        )
        repl.run()  # must not raise

    def test_session_messages_after_one_turn(self, tmp_path):
        """After one user message the session has user+assistant messages."""
        provider = FakeProvider([TextDelta("回答"), Done()])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_repl(
            provider, store, console, inputs=["你好", "/exit"]
        )
        repl.run()
        assert len(session.messages) == 2
        assert session.messages[0] == {"role": "user", "content": "你好"}
        assert session.messages[1] == {"role": "assistant", "content": "回答"}

    def test_session_persisted_to_disk(self, tmp_path):
        """Session JSON file is written after one completed turn."""
        provider = FakeProvider([TextDelta("回答"), Done()])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_repl(
            provider, store, console, inputs=["你好", "/exit"]
        )
        repl.run()
        disk_file = tmp_path / f"{session.id}.json"
        assert disk_file.exists()
        data = json.loads(disk_file.read_text())
        assert len(data["messages"]) == 2

    def test_second_turn_carries_first_turn_history(self, tmp_path):
        """The second call to provider.stream receives the first-turn messages too."""
        provider = FakeProvider([TextDelta("回答"), Done()])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(
            provider, store, console, inputs=["第一轮", "第二轮", "/exit"]
        )
        repl.run()
        assert len(provider.calls) == 2
        second_call_messages = provider.calls[1]
        roles = [m["role"] for m in second_call_messages]
        assert roles == ["user", "assistant", "user"]

    def test_empty_input_does_not_call_provider(self, tmp_path):
        """Empty input line is skipped without calling the provider."""
        provider = FakeProvider([TextDelta("回答"), Done()])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(
            provider, store, console, inputs=["", "/exit"]
        )
        repl.run()
        assert provider.calls == []

    def test_eof_exits_cleanly(self, tmp_path):
        """EOFError from input_fn causes clean exit (no exception propagated)."""
        from wentian.repl import REPL

        provider = FakeProvider([TextDelta("回答"), Done()])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        session = store.create(provider=provider.name)
        renderer = Renderer(console)

        def _eof_input(prompt: str = "") -> str:
            raise EOFError

        repl = REPL(
            provider=provider,
            session=session,
            store=store,
            renderer=renderer,
            provider_factory=lambda n: provider,
            input_fn=_eof_input,
        )
        repl.run()  # must not raise
