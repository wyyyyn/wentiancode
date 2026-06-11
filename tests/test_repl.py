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


# ===========================================================================
# T9 — slash commands
# ===========================================================================

class TestSlashHelp:
    def test_help_lists_all_commands(self, tmp_path):
        """/help output contains all six command names."""
        provider = FakeProvider([])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(
            provider, store, console, inputs=["/help", "/exit"]
        )
        repl.run()
        output = console.export_text()
        for cmd in ["/help", "/new", "/sessions", "/resume", "/provider", "/exit"]:
            assert cmd in output, f"Expected '{cmd}' in /help output"


class TestSlashNew:
    def test_new_creates_fresh_session(self, tmp_path):
        """/new replaces the current session id with a fresh one."""
        provider = FakeProvider([TextDelta("ok"), Done()])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, original_session = _make_repl(
            provider, store, console, inputs=["你好", "/new", "/exit"]
        )
        original_id = original_session.id
        repl.run()
        assert repl._session.id != original_id

    def test_new_preserves_old_session_file(self, tmp_path):
        """/new doesn't delete the previous session file."""
        provider = FakeProvider([TextDelta("ok"), Done()])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, original_session = _make_repl(
            provider, store, console, inputs=["你好", "/new", "/exit"]
        )
        original_id = original_session.id
        repl.run()
        old_file = tmp_path / f"{original_id}.json"
        assert old_file.exists()


class TestSlashSessions:
    def test_sessions_shows_existing_ids(self, tmp_path):
        """/sessions output contains the id of each saved session."""
        provider = FakeProvider([TextDelta("ok"), Done()])
        store = SessionStore(tmp_path)
        s = store.create(provider="fake")
        store.save(s)
        console = Console(record=True)
        repl, _ = _make_repl(
            provider, store, console, inputs=["/sessions", "/exit"]
        )
        repl.run()
        output = console.export_text()
        assert s.id in output


class TestSlashResume:
    def test_resume_loads_target_session(self, tmp_path):
        """/resume <id> switches the active session to the given id."""
        provider = FakeProvider([])
        store = SessionStore(tmp_path)
        target = store.create(provider="fake")
        target.messages.append({"role": "user", "content": "历史消息"})
        store.save(target)

        console = Console(record=True)
        repl, _ = _make_repl(
            provider, store, console,
            inputs=[f"/resume {target.id}", "/exit"]
        )
        repl.run()
        assert repl._session.id == target.id
        assert repl._session.messages[0]["content"] == "历史消息"

    def test_resume_bad_id_does_not_crash(self, tmp_path):
        """/resume with an unknown id prints an error but keeps running."""
        provider = FakeProvider([])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(
            provider, store, console, inputs=["/resume no-such-id", "/exit"]
        )
        repl.run()  # must not raise
        output = console.export_text()
        assert "找不到会话" in output


class TestSlashProvider:
    def test_provider_switches_provider(self, tmp_path):
        """/provider <name> calls provider_factory and switches the active provider."""
        old_provider = FakeProvider([])
        new_provider = FakeProvider([])
        new_provider.name = "new_fake"

        def factory(name: str) -> Provider:
            if name == "new_fake":
                return new_provider
            raise ConfigError(f"unknown: {name}")

        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(
            old_provider, store, console,
            inputs=["/provider new_fake", "/exit"],
            provider_factory=factory,
        )
        repl.run()
        assert repl._provider is new_provider

    def test_provider_unknown_name_does_not_crash(self, tmp_path):
        """/provider with an unknown name prints an error but doesn't crash."""
        provider = FakeProvider([])

        def factory(name: str) -> Provider:
            raise ConfigError(f"unknown: {name}")

        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(
            provider, store, console,
            inputs=["/provider no_such", "/exit"],
            provider_factory=factory,
        )
        repl.run()  # must not raise
        assert repl._provider is provider  # unchanged


class TestSlashUnknown:
    def test_unknown_slash_command_does_not_crash(self, tmp_path):
        """Unknown slash command prints 'unknown' hint and keeps going."""
        provider = FakeProvider([])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(
            provider, store, console, inputs=["/foobar", "/exit"]
        )
        repl.run()  # must not raise
        output = console.export_text()
        assert "未知" in output or "unknown" in output.lower()


# ===========================================================================
# T10 — error rollback
# ===========================================================================

class ErrorProvider(Provider):
    """Provider that always raises RuntimeError on stream()."""

    name = "error"

    def stream(
        self, messages: list[Message], *, system: str | None = None
    ) -> Iterator[StreamEvent]:
        raise RuntimeError("simulated provider failure")
        yield  # make it a generator (unreachable but satisfies type)


class TestErrorRollback:
    def test_provider_exception_does_not_crash_repl(self, tmp_path):
        """RuntimeError from provider.stream does not propagate out of run()."""
        provider = ErrorProvider()
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(
            provider, store, console, inputs=["hello", "/exit"]
        )
        repl.run()  # must not raise

    def test_provider_exception_rolls_back_user_message(self, tmp_path):
        """After a provider error the user message is NOT in session.messages."""
        provider = ErrorProvider()
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_repl(
            provider, store, console, inputs=["hello", "/exit"]
        )
        repl.run()
        assert session.messages == []

    def test_provider_exception_does_not_write_disk(self, tmp_path):
        """After a provider error no session file is written for that turn."""
        provider = ErrorProvider()
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_repl(
            provider, store, console, inputs=["hello", "/exit"]
        )
        repl.run()
        disk_file = tmp_path / f"{session.id}.json"
        assert not disk_file.exists()

    def test_provider_exception_prints_error(self, tmp_path):
        """After a provider error an error message is printed to the console."""
        provider = ErrorProvider()
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(
            provider, store, console, inputs=["hello", "/exit"]
        )
        repl.run()
        output = console.export_text()
        assert "错误" in output

    def test_can_chat_after_error(self, tmp_path):
        """After a provider error the next round works normally."""
        call_count = 0

        class SometimesErrorProvider(Provider):
            name = "sometimes_error"

            def stream(
                self, messages: list[Message], *, system: str | None = None
            ) -> Iterator[StreamEvent]:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise RuntimeError("first call fails")
                yield TextDelta("恢复成功")
                yield Done()

        provider = SometimesErrorProvider()
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_repl(
            provider, store, console,
            inputs=["第一轮会失败", "第二轮", "/exit"]
        )
        repl.run()
        assert len(session.messages) == 2
        assert session.messages[0]["content"] == "第二轮"
        assert session.messages[1]["content"] == "恢复成功"


# ===========================================================================
# T17 — REPL.status_line (C2/F16)
# ===========================================================================

class TestStatusLine:
    def test_fresh_repl_status_line(self, tmp_path):
        """Fresh REPL → status_line contains provider name, session id, '0 条消息'."""
        provider = FakeProvider([])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_repl(
            provider, store, console, inputs=[]
        )
        line = repl.status_line()
        assert "fake" in line
        assert session.id in line
        assert "0 条消息" in line

    def test_status_line_after_one_chat_round(self, tmp_path):
        """After one chat round → status_line shows '2 条消息'."""
        provider = FakeProvider([TextDelta("回答"), Done()])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(
            provider, store, console, inputs=["你好", "/exit"]
        )
        repl.run()
        line = repl.status_line()
        assert "2 条消息" in line

    def test_status_line_reflects_provider_switch(self, tmp_path):
        """After _cmd_provider → status_line contains the new provider name."""
        old_provider = FakeProvider([])
        new_provider = FakeProvider([])
        new_provider.name = "other"

        def factory(name: str) -> Provider:
            if name == "other":
                return new_provider
            raise ConfigError(f"unknown: {name}")

        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(
            old_provider, store, console,
            inputs=[],
            provider_factory=factory,
        )
        repl._cmd_provider("other")
        line = repl.status_line()
        assert "other" in line

    def test_status_line_reflects_new_session(self, tmp_path):
        """After _cmd_new → new session id in status_line and '0 条消息'."""
        provider = FakeProvider([TextDelta("ok"), Done()])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, original_session = _make_repl(
            provider, store, console, inputs=["你好", "/new", "/exit"]
        )
        original_id = original_session.id
        repl.run()
        line = repl.status_line()
        assert original_id not in line
        assert repl._session.id in line
        assert "0 条消息" in line

    def test_status_line_no_stray_colon_when_model_empty(self, tmp_path):
        """FakeProvider has empty model → no stray 'fake:' colon in status_line."""
        provider = FakeProvider([])
        # Verify FakeProvider has no meaningful model (inherits empty string)
        assert getattr(provider, "model", "") == ""
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(
            provider, store, console, inputs=[]
        )
        line = repl.status_line()
        assert "fake:" not in line

    def test_status_line_includes_model_when_present(self, tmp_path):
        """v0.2 · C2 · F16（任务 T17 补测）

        When provider.model is set, status_line must contain 'name:model'.
        Closes the model-present branch gap identified during T18 review.
        """
        provider = FakeProvider([])
        provider.model = "m1"
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(
            provider, store, console, inputs=[]
        )
        line = repl.status_line()
        assert "fake:m1" in line
