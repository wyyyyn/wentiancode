"""Tests for REPL (T8, T9, T10, T17, T23, T42, T43)."""
from __future__ import annotations

import json
import threading
import pytest
from pathlib import Path
from typing import Iterator

from rich.console import Console

from conftest import BlockingFakeProvider, FakeListener, ScriptedProvider

from wentian.providers.base import (
    Provider,
    StreamEvent,
    TextDelta,
    ToolCallEvent,
    ToolSpec,
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
        self, messages: list[Message], *, system: str | None = None, tools=None
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
    renderer: Renderer | None = None,
    interrupt_listener=None,
):
    """Assemble a REPL with injected fakes. Returns (repl, session)."""
    from wentian.repl import REPL

    if session is None:
        session = store.create(provider=provider.name)

    if renderer is None:
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
        interrupt_listener=interrupt_listener,
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
        self, messages: list[Message], *, system: str | None = None, tools=None
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
                self, messages: list[Message], *, system: str | None = None,
                tools=None,
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


# ===========================================================================
# T23 — REPL interrupt semantics (v0.2 · C6 · F18)
# ===========================================================================

class RecordingRenderer(Renderer):
    """Renderer subclass that records the ``interrupt`` kwarg per call."""

    def __init__(self, console: Console) -> None:
        super().__init__(console)
        self.interrupt_args: list[object] = []

    def render_stream(self, events, *, interrupt=None):
        self.interrupt_args.append(interrupt)
        return super().render_stream(events, interrupt=interrupt)


class TestT23Interrupt:
    def test_partial_interrupt_keeps_partial_and_saves(self, tmp_path):
        """有部分正文中断 → partial 入史并落盘 (AC16).

        BlockingFakeProvider yields two deltas then blocks forever; the
        listener's Event fires 0.2s later (T22-proven timing: deltas are
        consumed within ms, interrupt cuts the silent wait)."""
        provider = BlockingFakeProvider([TextDelta("两"), TextDelta("段")])
        listener = FakeListener(
            arm=lambda ev: threading.Timer(0.2, ev.set).start()
        )
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_repl(
            provider, store, console, inputs=[], interrupt_listener=listener
        )

        repl._chat_once("问题")

        assert session.messages == [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "两段"},
        ]
        disk_file = tmp_path / f"{session.id}.json"
        assert disk_file.exists()
        data = json.loads(disk_file.read_text())
        assert data["messages"][1] == {"role": "assistant", "content": "两段"}

    def test_zero_text_interrupt_rolls_back_user_message(self, tmp_path):
        """零正文中断 → user 消息回滚、不落盘（历史无未答之问，AC16）。"""
        provider = BlockingFakeProvider([])  # blocks before any event
        listener = FakeListener(arm=lambda ev: ev.set())  # pre-set
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_repl(
            provider, store, console, inputs=[], interrupt_listener=listener
        )

        repl._chat_once("没等到回答的问题")

        assert session.messages == []
        assert list(tmp_path.glob("*.json")) == []

    def test_repl_continues_after_interrupt(self, tmp_path):
        """中断后 REPL 继续接受下一轮输入：第 1 轮零正文中断，第 2 轮正常。"""

        class TwoPhaseProvider(Provider):
            name = "two-phase"

            def __init__(self) -> None:
                self.call_count = 0
                self._block = threading.Event()  # never set

            def stream(
                self, messages: list[Message], *, system: str | None = None,
                tools=None,
            ) -> Iterator[StreamEvent]:
                self.call_count += 1
                if self.call_count == 1:
                    self._block.wait()  # round 1: hang before first event
                    return
                yield TextDelta("答2")
                yield Done()

        round_no = {"n": 0}

        def arm(event: threading.Event) -> None:
            round_no["n"] += 1
            if round_no["n"] == 1:
                event.set()  # only round 1 is interrupted

        provider = TwoPhaseProvider()
        listener = FakeListener(arm=arm)
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_repl(
            provider, store, console,
            inputs=["q1", "q2", "/exit"],
            interrupt_listener=listener,
        )

        repl.run()

        assert provider.call_count == 2
        assert session.messages == [
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "答2"},
        ]

    def test_default_repl_uses_null_listener_direct_path(self, tmp_path):
        """No listener injected → NullListener → renderer gets interrupt=None
        (direct path) and a plain chat round behaves exactly like v0.1."""
        provider = FakeProvider([TextDelta("回答"), Done()])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        renderer = RecordingRenderer(console)
        repl, session = _make_repl(
            provider, store, console,
            inputs=["你好", "/exit"],
            renderer=renderer,
        )

        repl.run()

        assert renderer.interrupt_args == [None]
        assert session.messages == [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "回答"},
        ]

    def test_enter_exit_balance_including_error_path(self, tmp_path):
        """__exit__ runs once per __enter__ even when the provider raises."""
        provider = ErrorProvider()
        listener = FakeListener()
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_repl(
            provider, store, console, inputs=[], interrupt_listener=listener
        )

        repl._chat_once("会失败")

        assert session.messages == []  # rollback unchanged on errors
        assert listener.enter_count == 1
        assert listener.exit_count == 1


# ===========================================================================
# T42 / T43 — single tool round orchestration (v0.3 · C12 · F23)
# ===========================================================================

# ScriptedProvider（v0.3 · C12 · F23，任务 T42/T43）已于 T52 提升至
# tests/conftest.py，从文件顶部 import；行为不变。

class FakeExecutor:
    """v0.3 · C12 · F23（任务 T42/T43）— records execute() calls, returns
    scripted ToolOutcome-like objects.

    Each scripted outcome is a simple namespace duck-typed to ToolOutcome
    (call_id/name/content/is_error/denied). Default success outcome echoes
    the call.
    """

    def __init__(self, outcomes: dict[str, object] | None = None) -> None:
        self.calls: list[tuple[str, str, object]] = []
        self._outcomes = outcomes or {}

    def execute(self, call_id: str, name: str, arguments):
        self.calls.append((call_id, name, arguments))
        if call_id in self._outcomes:
            return self._outcomes[call_id]
        return _Outcome(
            call_id=call_id,
            name=name,
            content=f"ran {name}",
            is_error=False,
            denied=False,
        )


class _Outcome:
    """Minimal ToolOutcome duck-type for FakeExecutor."""

    def __init__(self, *, call_id, name, content, is_error, denied=False):
        self.call_id = call_id
        self.name = name
        self.content = content
        self.is_error = is_error
        self.denied = denied


def _make_tool_repl(
    provider: Provider,
    store: SessionStore,
    console: Console,
    *,
    inputs: list[str],
    registry=None,
    executor=None,
    renderer: Renderer | None = None,
    interrupt_listener=None,
):
    """Assemble a REPL wired with registry/executor. Returns (repl, session)."""
    from wentian.repl import REPL

    session = store.create(provider=provider.name)
    if renderer is None:
        renderer = Renderer(console)

    input_iter = iter(inputs)

    def _input_fn(prompt: str = "") -> str:
        return next(input_iter)

    def provider_factory(name: str) -> Provider:
        raise ConfigError(f"unknown provider: {name}")

    repl = REPL(
        provider=provider,
        session=session,
        store=store,
        renderer=renderer,
        provider_factory=provider_factory,
        input_fn=_input_fn,
        interrupt_listener=interrupt_listener,
        registry=registry,
        executor=executor,
    )
    return repl, session


class _FakeRegistry:
    """Minimal registry exposing specs()."""

    def __init__(self, specs: list[ToolSpec]) -> None:
        self._specs = specs

    def specs(self) -> list[ToolSpec]:
        return self._specs


_SPEC = ToolSpec(name="read", description="read a file", parameters={"type": "object"})


class TestT42ToolRoundMainPath:
    def test_registry_none_is_v02_behavior(self, tmp_path):
        """registry=None → one round, user/assistant history, tools=None passed."""
        provider = ScriptedProvider([[TextDelta("回答"), Done()]])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_tool_repl(
            provider, store, console, inputs=[], registry=None, executor=None
        )

        repl._chat_once("你好")

        assert session.messages == [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "回答"},
        ]
        assert len(provider.calls) == 1
        assert provider.tools_seen == [None]

    def test_registry_passes_specs_to_provider(self, tmp_path):
        """With a registry → provider.stream receives tools == registry.specs()."""
        registry = _FakeRegistry([_SPEC])
        provider = ScriptedProvider([[TextDelta("回答"), Done()]])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_tool_repl(
            provider, store, console, inputs=[], registry=registry, executor=None
        )

        repl._chat_once("你好")

        assert provider.tools_seen[0] == [_SPEC]

    def test_tool_round_executes_and_builds_history(self, tmp_path):
        """Tool round → executor sees both calls in order; history sequence and save."""
        registry = _FakeRegistry([_SPEC])
        executor = FakeExecutor()
        provider = ScriptedProvider([
            [
                TextDelta("用工具"),
                ToolCallEvent(id="c1", name="read", arguments={"path": "a"}),
                ToolCallEvent(id="c2", name="read", arguments={"path": "b"}),
                Done(raw_content=[{"type": "text", "text": "用工具"}]),
            ],
            [TextDelta("第二轮答复"), Done()],
        ])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_tool_repl(
            provider, store, console, inputs=[],
            registry=registry, executor=executor,
        )

        repl._chat_once("做点事")

        # Executor saw both calls in order, args forwarded.
        assert [(c[0], c[1], c[2]) for c in executor.calls] == [
            ("c1", "read", {"path": "a"}),
            ("c2", "read", {"path": "b"}),
        ]
        msgs = session.messages
        assert [m["role"] for m in msgs] == [
            "user", "assistant", "tool", "tool", "assistant",
        ]
        # Round-1 assistant carries content + tool_calls + raw_content.
        assert msgs[1]["content"] == "用工具"
        assert msgs[1]["tool_calls"] == [
            {"id": "c1", "name": "read", "arguments": {"path": "a"}},
            {"id": "c2", "name": "read", "arguments": {"path": "b"}},
        ]
        assert msgs[1]["raw_content"] == [{"type": "text", "text": "用工具"}]
        # Tool messages.
        assert msgs[2] == {
            "role": "tool", "tool_call_id": "c1",
            "content": "ran read", "is_error": False,
        }
        assert msgs[3] == {
            "role": "tool", "tool_call_id": "c2",
            "content": "ran read", "is_error": False,
        }
        # Round-2 assistant = text only, no tool_calls key.
        assert msgs[4] == {"role": "assistant", "content": "第二轮答复"}
        # Saved to disk.
        disk_file = tmp_path / f"{session.id}.json"
        assert disk_file.exists()
        data = json.loads(disk_file.read_text())
        assert [m["role"] for m in data["messages"]] == [
            "user", "assistant", "tool", "tool", "assistant",
        ]

    def test_round2_call_sees_full_history_and_same_tools(self, tmp_path):
        """Round-2 stream sees user/assistant/tool/tool history and same tools=."""
        registry = _FakeRegistry([_SPEC])
        executor = FakeExecutor()
        provider = ScriptedProvider([
            [
                ToolCallEvent(id="c1", name="read", arguments={"path": "a"}),
                Done(),
            ],
            [TextDelta("ok"), Done()],
        ])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_tool_repl(
            provider, store, console, inputs=[],
            registry=registry, executor=executor,
        )

        repl._chat_once("做点事")

        assert len(provider.calls) == 2
        round2_roles = [m["role"] for m in provider.calls[1]]
        assert round2_roles == ["user", "assistant", "tool"]
        assert provider.tools_seen[1] == [_SPEC]

    def test_no_tool_calls_single_round_executor_untouched(self, tmp_path):
        """Reply without tool calls → single stream() call, executor never called."""
        registry = _FakeRegistry([_SPEC])
        executor = FakeExecutor()
        provider = ScriptedProvider([[TextDelta("纯文本"), Done()]])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_tool_repl(
            provider, store, console, inputs=[],
            registry=registry, executor=executor,
        )

        repl._chat_once("你好")

        assert len(provider.calls) == 1
        assert executor.calls == []
        assert session.messages == [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "纯文本"},
        ]


class TestT43ToolRoundEdgePaths:
    def test_round2_tool_calls_are_dropped_with_notice(self, tmp_path):
        """Round2 requests tools again → executor only round-1 count; notice
        printed; round2 assistant message has NO tool_calls key."""
        registry = _FakeRegistry([_SPEC])
        executor = FakeExecutor()
        provider = ScriptedProvider([
            [
                ToolCallEvent(id="c1", name="read", arguments={"path": "a"}),
                Done(),
            ],
            [
                TextDelta("还想用工具"),
                ToolCallEvent(id="c2", name="read", arguments={"path": "b"}),
                Done(),
            ],
        ])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_tool_repl(
            provider, store, console, inputs=[],
            registry=registry, executor=executor,
        )

        repl._chat_once("做点事")

        # Executor only ran the round-1 single call.
        assert len(executor.calls) == 1
        # 单轮 limitation notice printed.
        output = console.export_text()
        assert "单轮" in output
        # Round-2 assistant message: text only, no tool_calls key.
        last = session.messages[-1]
        assert last == {"role": "assistant", "content": "还想用工具"}
        assert "tool_calls" not in last

    def test_round1_interrupt_discards_tool_calls(self, tmp_path):
        """Round1 interrupted → executor never called, tool_calls discarded,
        v0.2 partial semantics intact (partial text in history + saved)."""
        registry = _FakeRegistry([_SPEC])
        executor = FakeExecutor()
        # BlockingFakeProvider yields a partial text + a tool call, then hangs.
        provider = BlockingFakeProvider([
            TextDelta("部分"),
            ToolCallEvent(id="c1", name="read", arguments={"path": "a"}),
        ])
        listener = FakeListener(
            arm=lambda ev: threading.Timer(0.2, ev.set).start()
        )
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_tool_repl(
            provider, store, console, inputs=[],
            registry=registry, executor=executor,
            interrupt_listener=listener,
        )

        repl._chat_once("问题")

        assert executor.calls == []
        # Partial text enters history as a plain assistant message, no tool_calls.
        assert session.messages == [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "部分"},
        ]
        assert "tool_calls" not in session.messages[1]
        disk_file = tmp_path / f"{session.id}.json"
        assert disk_file.exists()

    def test_round1_zero_text_interrupt_rolls_back(self, tmp_path):
        """Round1 zero-text interrupt → user msg rolled back, executor untouched."""
        registry = _FakeRegistry([_SPEC])
        executor = FakeExecutor()
        provider = BlockingFakeProvider([])  # blocks before any event
        listener = FakeListener(arm=lambda ev: ev.set())
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_tool_repl(
            provider, store, console, inputs=[],
            registry=registry, executor=executor,
            interrupt_listener=listener,
        )

        repl._chat_once("没等到回答")

        assert session.messages == []
        assert executor.calls == []
        assert list(tmp_path.glob("*.json")) == []

    def test_round2_exception_keeps_history_and_saves(self, tmp_path):
        """Round2 raises → user + assistant(tool_calls) + tool messages REMAIN
        and are saved; error printed; REPL keeps running."""
        registry = _FakeRegistry([_SPEC])
        executor = FakeExecutor()

        class Round2Raises(Provider):
            name = "r2raises"

            def __init__(self) -> None:
                self.calls = 0

            def stream(self, messages, *, system=None, tools=None):
                self.calls += 1
                if self.calls == 1:
                    yield ToolCallEvent(id="c1", name="read", arguments={"path": "a"})
                    yield Done()
                    return
                raise RuntimeError("round2 boom")
                yield  # pragma: no cover

        provider = Round2Raises()
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_tool_repl(
            provider, store, console, inputs=[],
            registry=registry, executor=executor,
        )

        repl._chat_once("做点事")

        roles = [m["role"] for m in session.messages]
        assert roles == ["user", "assistant", "tool"]
        # Error printed.
        assert "错误" in console.export_text()
        # Saved despite the round-2 failure.
        disk_file = tmp_path / f"{session.id}.json"
        assert disk_file.exists()
        data = json.loads(disk_file.read_text())
        assert [m["role"] for m in data["messages"]] == ["user", "assistant", "tool"]

    def test_denied_outcome_enters_history_as_error(self, tmp_path):
        """Denied outcome → tool message with is_error=True and refusal content."""
        registry = _FakeRegistry([_SPEC])
        denied = _Outcome(
            call_id="c1", name="write", content="用户拒绝执行 (拒绝执行)",
            is_error=True, denied=True,
        )
        executor = FakeExecutor(outcomes={"c1": denied})
        provider = ScriptedProvider([
            [
                ToolCallEvent(id="c1", name="write", arguments={"path": "a"}),
                Done(),
            ],
            [TextDelta("好的"), Done()],
        ])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_tool_repl(
            provider, store, console, inputs=[],
            registry=registry, executor=executor,
        )

        repl._chat_once("写文件")

        tool_msg = session.messages[2]
        assert tool_msg["role"] == "tool"
        assert tool_msg["is_error"] is True
        assert "拒绝" in tool_msg["content"]

    def test_unparseable_call_stored_as_empty_args_but_executor_gets_none(self, tmp_path):
        """arguments=None call → stored tool_calls entry uses {} but executor
        receives the original None (contract: unparseable never reach history)."""
        registry = _FakeRegistry([_SPEC])
        executor = FakeExecutor()
        provider = ScriptedProvider([
            [
                ToolCallEvent(id="c1", name="read", arguments=None),
                Done(),
            ],
            [TextDelta("ok"), Done()],
        ])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_tool_repl(
            provider, store, console, inputs=[],
            registry=registry, executor=executor,
        )

        repl._chat_once("做点事")

        # Stored history: arguments coerced to {}.
        assert session.messages[1]["tool_calls"] == [
            {"id": "c1", "name": "read", "arguments": {}},
        ]
        # Executor received the original None.
        assert executor.calls[0] == ("c1", "read", None)
