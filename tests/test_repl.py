"""Tests for REPL (T8, T9, T10, T17, T23, T42, T43; v0.4 多轮语义迁移 + T55)."""

from __future__ import annotations

import json
import threading
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
    Usage,
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
        repl, _ = _make_repl(provider, store, console, inputs=["你好", "/exit"])
        repl.run()  # must not raise

    def test_session_messages_after_one_turn(self, tmp_path):
        """After one user message the session has user+assistant messages."""
        provider = FakeProvider([TextDelta("回答"), Done()])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_repl(provider, store, console, inputs=["你好", "/exit"])
        repl.run()
        assert len(session.messages) == 2
        assert session.messages[0] == {"role": "user", "content": "你好"}
        assert session.messages[1] == {"role": "assistant", "content": "回答"}

    def test_session_persisted_to_disk(self, tmp_path):
        """Session JSON file is written after one completed turn."""
        provider = FakeProvider([TextDelta("回答"), Done()])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_repl(provider, store, console, inputs=["你好", "/exit"])
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
        repl, _ = _make_repl(provider, store, console, inputs=["", "/exit"])
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
        repl, _ = _make_repl(provider, store, console, inputs=["/help", "/exit"])
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
        repl, _ = _make_repl(provider, store, console, inputs=["/sessions", "/exit"])
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
            provider, store, console, inputs=[f"/resume {target.id}", "/exit"]
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
            old_provider,
            store,
            console,
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
            provider,
            store,
            console,
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
        repl, _ = _make_repl(provider, store, console, inputs=["/foobar", "/exit"])
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
        repl, _ = _make_repl(provider, store, console, inputs=["hello", "/exit"])
        repl.run()  # must not raise

    def test_provider_exception_rolls_back_user_message(self, tmp_path):
        """After a provider error the user message is NOT in session.messages."""
        provider = ErrorProvider()
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_repl(provider, store, console, inputs=["hello", "/exit"])
        repl.run()
        assert session.messages == []

    def test_provider_exception_does_not_write_disk(self, tmp_path):
        """After a provider error no session file is written for that turn."""
        provider = ErrorProvider()
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_repl(provider, store, console, inputs=["hello", "/exit"])
        repl.run()
        disk_file = tmp_path / f"{session.id}.json"
        assert not disk_file.exists()

    def test_provider_exception_prints_error(self, tmp_path):
        """After a provider error an error message is printed to the console."""
        provider = ErrorProvider()
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(provider, store, console, inputs=["hello", "/exit"])
        repl.run()
        output = console.export_text()
        assert "错误" in output

    def test_can_chat_after_error(self, tmp_path):
        """After a provider error the next round works normally."""
        call_count = 0

        class SometimesErrorProvider(Provider):
            name = "sometimes_error"

            def stream(
                self,
                messages: list[Message],
                *,
                system: str | None = None,
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
            provider, store, console, inputs=["第一轮会失败", "第二轮", "/exit"]
        )
        repl.run()
        assert len(session.messages) == 2
        assert session.messages[0]["content"] == "第二轮"
        assert session.messages[1]["content"] == "恢复成功"


# ===========================================================================
# T17 — REPL.status_line (C2/F16)
# v0.6 · C38 · F47（任务 T78）— 首段由 provider:model 改为当前权限模式；
# 不再展示 provider 名（AC49）。原 T17 provider 名断言迁移到此约定。
# ===========================================================================


class TestStatusLine:
    def test_fresh_repl_status_line(self, tmp_path):
        """Fresh REPL → status_line 首段是权限模式 default、含会话 id、'0 条消息'。"""
        provider = FakeProvider([])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_repl(provider, store, console, inputs=[])
        line = repl.status_line()
        assert line.startswith("default")
        assert session.id in line
        assert "0 条消息" in line

    def test_status_line_after_one_chat_round(self, tmp_path):
        """After one chat round → status_line shows '2 条消息'."""
        provider = FakeProvider([TextDelta("回答"), Done()])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(provider, store, console, inputs=["你好", "/exit"])
        repl.run()
        line = repl.status_line()
        assert "2 条消息" in line

    def test_status_line_first_segment_is_mode_not_provider(self, tmp_path):
        """v0.6 · C38 · F47（任务 T78）— 首段显权限模式、不含 provider 名（AC49）。

        即便 /provider 切换后端，status_line 首段仍是模式而非 provider 名。
        """
        old_provider = FakeProvider([])
        new_provider = FakeProvider([])
        new_provider.name = "other"
        new_provider.model = "m9"

        def factory(name: str) -> Provider:
            if name == "other":
                return new_provider
            raise ConfigError(f"unknown: {name}")

        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(
            old_provider,
            store,
            console,
            inputs=[],
            provider_factory=factory,
        )
        repl._cmd_provider("other")
        line = repl.status_line()
        # 首段是权限模式；provider 名/型号都不出现。
        assert line.startswith("default")
        assert "other" not in line
        assert "m9" not in line

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

    def test_status_line_no_provider_name_when_model_empty(self, tmp_path):
        """v0.6 · C38 · F47（任务 T78）— 不再有 'fake:' 段（provider 名退出状态栏）。"""
        provider = FakeProvider([])
        assert getattr(provider, "model", "") == ""
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(provider, store, console, inputs=[])
        line = repl.status_line()
        assert "fake" not in line

    def test_status_line_no_provider_name_when_model_present(self, tmp_path):
        """v0.6 · C38 · F47（任务 T78）— provider.model 设了也不出现在状态栏。"""
        provider = FakeProvider([])
        provider.model = "m1"
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(provider, store, console, inputs=[])
        line = repl.status_line()
        assert "fake:m1" not in line
        assert "fake" not in line


# ===========================================================================
# T78 — Shift+Tab 模式循环 + status_line 首段显模式 + plan 统一（v0.6 · C38 · F47）
# ===========================================================================


class TestModeCycle:
    def test_cycle_advances_through_four_modes_and_wraps(self, tmp_path):
        """Shift+Tab 循环 default→acceptEdits→plan→bypassPermissions→default。"""
        from wentian.permissions.decision import Mode

        provider = FakeProvider([])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(provider, store, console, inputs=[])

        assert repl.get_mode() is Mode.DEFAULT
        repl.cycle_mode()
        assert repl.get_mode() is Mode.ACCEPT_EDITS
        repl.cycle_mode()
        assert repl.get_mode() is Mode.PLAN
        repl.cycle_mode()
        assert repl.get_mode() is Mode.BYPASS
        repl.cycle_mode()
        assert repl.get_mode() is Mode.DEFAULT  # wraps

    def test_status_line_shows_each_mode_value(self, tmp_path):
        """status_line 首段随 cycle_mode 显示对应模式的 value。"""
        provider = FakeProvider([])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(provider, store, console, inputs=[])

        assert repl.status_line().startswith("default")
        repl.cycle_mode()
        assert repl.status_line().startswith("acceptEdits")
        repl.cycle_mode()
        assert repl.status_line().startswith("plan")
        repl.cycle_mode()
        assert repl.status_line().startswith("bypassPermissions")

    def test_mode_survives_across_turns(self, tmp_path):
        """模式跨轮保持：聊一回合后 mode 不被重置（AC49）。"""
        from wentian.permissions.decision import Mode

        provider = FakeProvider([TextDelta("回答"), Done(), TextDelta("再答"), Done()])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(provider, store, console, inputs=["一", "二", "/exit"])
        repl.cycle_mode()  # → acceptEdits
        assert repl.get_mode() is Mode.ACCEPT_EDITS
        repl.run()
        # 跑完两轮对话后模式仍是 acceptEdits（未被重置）。
        assert repl.get_mode() is Mode.ACCEPT_EDITS

    def test_default_mode_injectable(self, tmp_path):
        """可注入初始模式（T79 从 settings.default_mode 注入的入口）。"""
        from wentian.permissions.decision import Mode
        from wentian.repl import REPL

        provider = FakeProvider([])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        session = store.create(provider=provider.name)

        def factory(name: str) -> Provider:
            raise ConfigError(name)

        repl = REPL(
            provider=provider,
            session=session,
            store=store,
            renderer=Renderer(console),
            provider_factory=factory,
            input_fn=lambda p="": "",
            default_mode=Mode.ACCEPT_EDITS,
        )
        assert repl.get_mode() is Mode.ACCEPT_EDITS
        assert repl.status_line().startswith("acceptEdits")

    def test_plan_command_sets_mode_plan(self, tmp_path):
        """/plan → mode==PLAN（plan 统一为一档）。"""
        from wentian.permissions.decision import Mode

        provider = FakeProvider([])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(provider, store, console, inputs=[])

        repl._dispatch_command("/plan")
        assert repl.get_mode() is Mode.PLAN
        assert repl._plan_mode is True  # 派生属性仍真

    def test_do_command_returns_to_default_not_previous(self, tmp_path):
        """/do 固定回 default（即便进入 plan 前是别的档），不恢复旧档。"""
        from wentian.permissions.decision import Mode

        provider = FakeProvider([])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(provider, store, console, inputs=[])

        repl.cycle_mode()  # acceptEdits
        repl._dispatch_command("/plan")  # PLAN
        repl._dispatch_command("/do")  # 固定回 default
        assert repl.get_mode() is Mode.DEFAULT
        assert repl._plan_mode is False


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
        listener = FakeListener(arm=lambda ev: threading.Timer(0.2, ev.set).start())
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
                self,
                messages: list[Message],
                *,
                system: str | None = None,
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
            provider,
            store,
            console,
            inputs=["q1", "q2", "/exit"],
            interrupt_listener=listener,
        )

        repl.run()

        assert provider.call_count == 2
        assert session.messages == [
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "答2"},
        ]

    def test_default_repl_plain_round_bypasses_render_stream(self, tmp_path):
        """v0.4 · C19 · F29（任务 T55 迁移，原
        test_default_repl_uses_null_listener_direct_path）

        迁移说明：v0.3 断言 render_stream 收到 interrupt=None（直接路径）；
        v0.4 回合由 AgentLoop + StreamView 驱动，REPL 不再调用
        render_stream（interrupt_args 应为空）。受保护行为不变：不注入
        监听器时纯对话回合行为与 v0.1/v0.2 全等。"""
        provider = FakeProvider([TextDelta("回答"), Done()])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        renderer = RecordingRenderer(console)
        repl, session = _make_repl(
            provider,
            store,
            console,
            inputs=["你好", "/exit"],
            renderer=renderer,
        )

        repl.run()

        assert renderer.interrupt_args == []
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
    max_rounds: int | None = None,
    plan_tools: tuple[str, ...] | None = None,
    pipeline=None,
    confirm_fn=None,
):
    """Assemble a REPL wired with registry/executor. Returns (repl, session).

    v0.4（任务 T55）— 可选透传 max_rounds / plan_tools 构造参数。
    v0.6（任务 T77）— 可选透传 pipeline / confirm_fn 构造参数。
    """
    from wentian.repl import REPL

    session = store.create(provider=provider.name)
    if renderer is None:
        renderer = Renderer(console)

    input_iter = iter(inputs)

    def _input_fn(prompt: str = "") -> str:
        return next(input_iter)

    def provider_factory(name: str) -> Provider:
        raise ConfigError(f"unknown provider: {name}")

    extra_kwargs = {}
    if max_rounds is not None:
        extra_kwargs["max_rounds"] = max_rounds
    if plan_tools is not None:
        extra_kwargs["plan_tools"] = plan_tools
    if pipeline is not None:
        extra_kwargs["pipeline"] = pipeline
    if confirm_fn is not None:
        extra_kwargs["confirm_fn"] = confirm_fn

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
        **extra_kwargs,
    )
    return repl, session


class _FakeTool:
    """v0.4（任务 T55）— 带 requires_confirmation 的最小工具替身。

    默认 True（side-effect）→ AgentLoop 分批后每个调用独占串行 wave，
    executor 的调用顺序确定，测试可做精确断言。
    """

    def __init__(self, requires_confirmation: bool = True) -> None:
        self.requires_confirmation = requires_confirmation


class _FakeRegistry:
    """Minimal registry：specs() 供 provider 声明，get() 供 loop 分类。

    v0.4（任务 T55）扩展：AgentLoop 的 classify 需要 ``get(name)``；默认把
    每个 spec 名注册为 _FakeTool()，名单外的名字返回 None（unknown）。
    """

    def __init__(self, specs: list[ToolSpec], tools: dict | None = None) -> None:
        self._specs = specs
        self._tools = (
            tools if tools is not None else {s.name: _FakeTool() for s in specs}
        )

    def specs(self) -> list[ToolSpec]:
        return self._specs

    def get(self, name: str):
        return self._tools.get(name)


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
        provider = ScriptedProvider(
            [
                [
                    TextDelta("用工具"),
                    ToolCallEvent(id="c1", name="read", arguments={"path": "a"}),
                    ToolCallEvent(id="c2", name="read", arguments={"path": "b"}),
                    Done(raw_content=[{"type": "text", "text": "用工具"}]),
                ],
                [TextDelta("第二轮答复"), Done()],
            ]
        )
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_tool_repl(
            provider,
            store,
            console,
            inputs=[],
            registry=registry,
            executor=executor,
        )

        repl._chat_once("做点事")

        # Executor saw both calls in order, args forwarded.
        assert [(c[0], c[1], c[2]) for c in executor.calls] == [
            ("c1", "read", {"path": "a"}),
            ("c2", "read", {"path": "b"}),
        ]
        msgs = session.messages
        assert [m["role"] for m in msgs] == [
            "user",
            "assistant",
            "tool",
            "tool",
            "assistant",
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
            "role": "tool",
            "tool_call_id": "c1",
            "content": "ran read",
            "is_error": False,
        }
        assert msgs[3] == {
            "role": "tool",
            "tool_call_id": "c2",
            "content": "ran read",
            "is_error": False,
        }
        # Round-2 assistant = text only, no tool_calls key.
        assert msgs[4] == {"role": "assistant", "content": "第二轮答复"}
        # Saved to disk.
        disk_file = tmp_path / f"{session.id}.json"
        assert disk_file.exists()
        data = json.loads(disk_file.read_text())
        assert [m["role"] for m in data["messages"]] == [
            "user",
            "assistant",
            "tool",
            "tool",
            "assistant",
        ]

    def test_round2_call_sees_full_history_and_same_tools(self, tmp_path):
        """Round-2 stream sees user/assistant/tool/tool history and same tools=."""
        registry = _FakeRegistry([_SPEC])
        executor = FakeExecutor()
        provider = ScriptedProvider(
            [
                [
                    ToolCallEvent(id="c1", name="read", arguments={"path": "a"}),
                    Done(),
                ],
                [TextDelta("ok"), Done()],
            ]
        )
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_tool_repl(
            provider,
            store,
            console,
            inputs=[],
            registry=registry,
            executor=executor,
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
            provider,
            store,
            console,
            inputs=[],
            registry=registry,
            executor=executor,
        )

        repl._chat_once("你好")

        assert len(provider.calls) == 1
        assert executor.calls == []
        assert session.messages == [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "纯文本"},
        ]


class TestT43ToolRoundEdgePaths:
    def test_max_rounds_brake_drops_pending_tools_with_notice(self, tmp_path):
        """v0.4 · C19 · F29（任务 T55 迁移，原
        test_round2_tool_calls_are_dropped_with_notice）

        迁移说明：v0.3「第 2 轮再请求工具 → 不执行 + 单轮提示」按多轮语义
        改写为 MAX_ROUNDS 刹车：max_rounds=2 时第 1 轮照常执行，第 2 轮再
        请求工具即触发上限。受保护行为不变——第二批不执行、黄提示出现、
        第 2 轮文本照常入史且无 tool_calls 键。"""
        registry = _FakeRegistry([_SPEC])
        executor = FakeExecutor()
        provider = ScriptedProvider(
            [
                [
                    ToolCallEvent(id="c1", name="read", arguments={"path": "a"}),
                    Done(),
                ],
                [
                    TextDelta("还想用工具"),
                    ToolCallEvent(id="c2", name="read", arguments={"path": "b"}),
                    Done(),
                ],
            ]
        )
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_tool_repl(
            provider,
            store,
            console,
            inputs=[],
            registry=registry,
            executor=executor,
            max_rounds=2,
        )

        repl._chat_once("做点事")

        # Executor only ran the round-1 single call (brake fires before
        # executing the round-2 batch).
        assert len(executor.calls) == 1
        # MAX_ROUNDS limitation notice printed.
        output = console.export_text()
        assert "上限" in output
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
        provider = BlockingFakeProvider(
            [
                TextDelta("部分"),
                ToolCallEvent(id="c1", name="read", arguments={"path": "a"}),
            ]
        )
        listener = FakeListener(arm=lambda ev: threading.Timer(0.2, ev.set).start())
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_tool_repl(
            provider,
            store,
            console,
            inputs=[],
            registry=registry,
            executor=executor,
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
            provider,
            store,
            console,
            inputs=[],
            registry=registry,
            executor=executor,
            interrupt_listener=listener,
        )

        repl._chat_once("没等到回答")

        assert session.messages == []
        assert executor.calls == []
        assert list(tmp_path.glob("*.json")) == []

    def test_round2_exception_keeps_history_and_saves(self, tmp_path):
        """v0.4 · C19 · F29（任务 T55 迁移，断言不变）— 第 2 轮流错误
        （STREAM_ERROR）→ user + assistant(tool_calls) + tool 消息保留并
        已落盘（逐轮落盘 + 终了落盘）；屏显错误；REPL 续命。"""
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
            provider,
            store,
            console,
            inputs=[],
            registry=registry,
            executor=executor,
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
        registry = _FakeRegistry(
            [_SPEC], tools={"read": _FakeTool(), "write": _FakeTool()}
        )
        denied = _Outcome(
            call_id="c1",
            name="write",
            content="用户拒绝执行 (拒绝执行)",
            is_error=True,
            denied=True,
        )
        executor = FakeExecutor(outcomes={"c1": denied})
        provider = ScriptedProvider(
            [
                [
                    ToolCallEvent(id="c1", name="write", arguments={"path": "a"}),
                    Done(),
                ],
                [TextDelta("好的"), Done()],
            ]
        )
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_tool_repl(
            provider,
            store,
            console,
            inputs=[],
            registry=registry,
            executor=executor,
        )

        repl._chat_once("写文件")

        tool_msg = session.messages[2]
        assert tool_msg["role"] == "tool"
        assert tool_msg["is_error"] is True
        assert "拒绝" in tool_msg["content"]

    def test_unparseable_call_stored_as_empty_args_but_executor_gets_none(
        self, tmp_path
    ):
        """arguments=None call → stored tool_calls entry uses {} but executor
        receives the original None (contract: unparseable never reach history)."""
        registry = _FakeRegistry([_SPEC])
        executor = FakeExecutor()
        provider = ScriptedProvider(
            [
                [
                    ToolCallEvent(id="c1", name="read", arguments=None),
                    Done(),
                ],
                [TextDelta("ok"), Done()],
            ]
        )
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_tool_repl(
            provider,
            store,
            console,
            inputs=[],
            registry=registry,
            executor=executor,
        )

        repl._chat_once("做点事")

        # Stored history: arguments coerced to {}.
        assert session.messages[1]["tool_calls"] == [
            {"id": "c1", "name": "read", "arguments": {}},
        ]
        # Executor received the original None.
        assert executor.calls[0] == ("c1", "read", None)


# ===========================================================================
# T55 — REPL 接入 AgentLoop：多轮回合（v0.4 · C19 · F29）
# ===========================================================================


class _CountingStore(SessionStore):
    """v0.4 · C19 · F29（任务 T55）— 记录 save() 次数的 SessionStore。

    验证逐轮落盘：每个产生工具结果的轮（RoundEnd）save 一次 + 回合终了
    一次。"""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.save_count = 0

    def save(self, session) -> None:
        self.save_count += 1
        return super().save(session)


class TestT55AgentLoopIntegration:
    def test_multi_round_history_and_per_round_saves(self, tmp_path):
        """三轮回合（工具→工具→文本）→ 历史完整成对入史；store.save 被调
        ≥ 工具轮数 + 终了一次；屏显含 ⏺/⎿ 与各轮正文。"""
        registry = _FakeRegistry([_SPEC])
        executor = FakeExecutor()
        provider = ScriptedProvider(
            [
                [
                    TextDelta("第一轮正文"),
                    ToolCallEvent(id="c1", name="read", arguments={"path": "a"}),
                    Done(),
                ],
                [
                    TextDelta("第二轮正文"),
                    ToolCallEvent(id="c2", name="read", arguments={"path": "b"}),
                    Done(),
                ],
                [TextDelta("最终答复"), Done()],
            ]
        )
        store = _CountingStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_tool_repl(
            provider,
            store,
            console,
            inputs=[],
            registry=registry,
            executor=executor,
        )

        repl._chat_once("做点事")

        assert [m["role"] for m in session.messages] == [
            "user",
            "assistant",
            "tool",
            "assistant",
            "tool",
            "assistant",
        ]
        assert session.messages[1]["content"] == "第一轮正文"
        assert session.messages[1]["tool_calls"] == [
            {"id": "c1", "name": "read", "arguments": {"path": "a"}},
        ]
        assert session.messages[3]["content"] == "第二轮正文"
        assert session.messages[3]["tool_calls"] == [
            {"id": "c2", "name": "read", "arguments": {"path": "b"}},
        ]
        assert session.messages[5] == {"role": "assistant", "content": "最终答复"}
        # 逐轮落盘：两个工具轮各一次 + 终了一次。
        assert store.save_count >= 3
        # 终盘也在磁盘上完整。
        data = json.loads((tmp_path / f"{session.id}.json").read_text())
        assert len(data["messages"]) == 6
        output = console.export_text()
        assert "⏺" in output
        assert "⎿" in output
        for text in ("第一轮正文", "第二轮正文", "最终答复"):
            assert text in output

    def test_run_tool_round_method_removed(self, tmp_path):
        """v0.3 的单轮工具方法 `_run_tool_round` 不复存在。"""
        provider = ScriptedProvider([[TextDelta("ok"), Done()]])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_tool_repl(provider, store, console, inputs=[])

        assert not hasattr(repl, "_run_tool_round")

    def test_constructor_accepts_max_rounds_and_plan_tools(self, tmp_path):
        """构造参数 max_rounds / plan_tools 可注入；纯对话回合照常工作。"""
        provider = ScriptedProvider([[TextDelta("ok"), Done()]])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_tool_repl(
            provider,
            store,
            console,
            inputs=[],
            max_rounds=5,
            plan_tools=("read_file",),
        )

        repl._chat_once("你好")

        assert session.messages == [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "ok"},
        ]

    def test_first_round_stream_error_rolls_back_with_tools(self, tmp_path):
        """首轮流错误（工具已接线）→ user 消息回滚、未落盘、executor 未动、
        屏显错误（len-baseline 回滚）。"""
        registry = _FakeRegistry([_SPEC])
        executor = FakeExecutor()
        provider = ErrorProvider()
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_tool_repl(
            provider,
            store,
            console,
            inputs=[],
            registry=registry,
            executor=executor,
        )

        repl._chat_once("hello")

        assert session.messages == []
        assert executor.calls == []
        assert list(tmp_path.glob("*.json")) == []
        assert "错误" in console.export_text()

    def test_unknown_tool_loop_stops_with_notice(self, tmp_path):
        """连续两轮全 unknown 调用 → UNKNOWN_TOOL_LOOP 停机：错误结果照常
        成对入史，黄提示文案出现。"""
        registry = _FakeRegistry([_SPEC])  # "ghost" 未注册 → unknown
        executor = FakeExecutor()
        provider = ScriptedProvider(
            [
                [ToolCallEvent(id="g1", name="ghost", arguments={}), Done()],
                [ToolCallEvent(id="g2", name="ghost", arguments={}), Done()],
            ]
        )
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_tool_repl(
            provider,
            store,
            console,
            inputs=[],
            registry=registry,
            executor=executor,
        )

        repl._chat_once("做点事")

        assert [m["role"] for m in session.messages] == [
            "user",
            "assistant",
            "tool",
            "assistant",
            "tool",
        ]
        assert len(executor.calls) == 2
        assert "未知工具" in console.export_text()
        # 已落盘（历史对下个用户轮保持一致）。
        assert (tmp_path / f"{session.id}.json").exists()

    def test_usage_line_rendered_when_reported(self, tmp_path):
        """脚本含 usage → 回合结束屏显一行 token 用量。"""
        provider = ScriptedProvider(
            [
                [
                    TextDelta("回答"),
                    Done(usage=Usage(input_tokens=12, output_tokens=7)),
                ],
            ]
        )
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_tool_repl(provider, store, console, inputs=[])

        repl._chat_once("你好")

        output = console.export_text()
        assert "tokens" in output
        assert "输入 12" in output
        assert "输出 7" in output
        assert "1 轮" in output

    def test_no_usage_no_usage_line(self, tmp_path):
        """脚本不含 usage → 不显示用量行。"""
        provider = ScriptedProvider([[TextDelta("回答"), Done()]])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_tool_repl(provider, store, console, inputs=[])

        repl._chat_once("你好")

        assert "tokens" not in console.export_text()


# ===========================================================================
# T66 — REPL 接线 + 计划模式提醒迁移（v0.5 · C22 · F35/F39）
# ===========================================================================


class TestT66RequestDecorator:
    """AC37 / AC38 / AC40 — request_decorator 注入 + system 稳定性验证。"""

    def test_env_reminder_injected_into_provider_messages(self, tmp_path):
        """AC37：跑一个回合，provider 收到的 messages 第一条 user 内容前含
        <system-reminder>（环境信息），说明 request_decorator 已接线。"""
        provider = ScriptedProvider([[TextDelta("回答"), Done()]])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_tool_repl(provider, store, console, inputs=[])

        repl._chat_once("你好")

        assert len(provider.calls) == 1
        first_user_msg = next(m for m in provider.calls[0] if m["role"] == "user")
        assert "<system-reminder>" in first_user_msg["content"]

    def test_env_reminder_not_persisted_to_store(self, tmp_path):
        """AC37（持久化纯净）：store 落盘的 messages 不含任何 <system-reminder>。
        decorator 只作用于请求路径，不 mutate 原始 session messages。"""
        import json

        provider = ScriptedProvider([[TextDelta("回答"), Done()]])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_tool_repl(provider, store, console, inputs=[])

        repl._chat_once("你好")

        # 内存中的 session.messages 不含 <system-reminder>。
        for msg in session.messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                assert "<system-reminder>" not in content, (
                    f"<system-reminder> 泄漏到 session.messages: {msg}"
                )

        # 磁盘上的 JSON 同样不含 <system-reminder>。
        disk_file = tmp_path / f"{session.id}.json"
        assert disk_file.exists()
        data = json.loads(disk_file.read_text())
        for msg in data["messages"]:
            content = msg.get("content", "")
            if isinstance(content, str):
                assert "<system-reminder>" not in content, (
                    f"<system-reminder> 泄漏到落盘 JSON: {msg}"
                )

    def test_system_stable_across_rounds_with_tools(self, tmp_path):
        """AC40（system 稳定）：registry + executor 接线时，多轮回合中
        provider 每次收到的 system 都与构造时传入的值完全一致（无追加后缀）。"""
        registry = _FakeRegistry([_SPEC])
        executor = FakeExecutor()
        provider = ScriptedProvider(
            [
                [TextDelta("回答"), Done()],
            ]
        )
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_tool_repl(
            provider,
            store,
            console,
            inputs=[],
            registry=registry,
            executor=executor,
        )
        # 直接设置 system（通过构造参数之外无法注入，这里手动设）。
        repl._system = "固定系统提示"

        repl._chat_once("你好")

        for sys_seen in provider.systems_seen:
            assert sys_seen == "固定系统提示", (
                f"system 被篡改：期望 '固定系统提示'，实际 {sys_seen!r}"
            )

    def test_env_reminder_contains_date_cwd_os(self, tmp_path):
        """AC37：环境提醒块包含日期、工作目录、操作系统信息。"""
        provider = ScriptedProvider([[TextDelta("回答"), Done()]])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_tool_repl(provider, store, console, inputs=[])

        repl._chat_once("你好")

        first_user_content = next(
            m["content"] for m in provider.calls[0] if m["role"] == "user"
        )
        # 环境提醒块应包含这几个关键字段名。
        assert "工作目录" in first_user_content
        assert "操作系统" in first_user_content
        assert "日期" in first_user_content


# ===========================================================================
# T77 (v0.6 · C37 · F48) — human-in-the-loop ask callback + permanent persist
# ===========================================================================


class _PermTool:
    """Fake tool carrying v0.6 permission metadata (category/friendly/args)."""

    def __init__(self, category, friendly_name, *, command_arg=None, path_args=()):
        from wentian.permissions.decision import Category  # noqa: F401

        self.category = category
        self.friendly_name = friendly_name
        self.command_arg = command_arg
        self.path_args = path_args
        # AgentLoop.classify reads requires_confirmation via duck typing.
        self.requires_confirmation = friendly_name != "Read"


class _PermRegistry:
    def __init__(self, specs, tools):
        self._specs = specs
        self._tools = tools

    def specs(self):
        return self._specs

    def get(self, name):
        return self._tools.get(name)


def _bash_perm_registry():
    from wentian.permissions.decision import Category

    spec = ToolSpec(
        name="run_command", description="run", parameters={"type": "object"}
    )
    tool = _PermTool(Category.COMMAND_EXEC, "Bash", command_arg="command")
    return _PermRegistry([spec], {"run_command": tool})


def _build_pipeline(tmp_path):
    from wentian.permissions.pipeline import PermissionPipeline
    from wentian.permissions.settings import load_settings

    settings = load_settings(tmp_path, user_path=tmp_path / "no-user.yaml")
    return PermissionPipeline(project_root=tmp_path, settings=settings)


class TestT77AskFlow:
    def test_ask_allow_once_runs_tool(self, tmp_path):
        """Ask → confirm returns ALLOW_ONCE → tool executes, result in history."""
        from wentian.ui.confirm import Choice

        registry = _bash_perm_registry()
        executor = FakeExecutor()
        pipeline = _build_pipeline(tmp_path)
        provider = ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="c1", name="run_command", arguments={"command": "ls"}
                    ),
                    Done(),
                ],
                [TextDelta("好的"), Done()],
            ]
        )
        store = SessionStore(tmp_path)
        console = Console(record=True)

        async def confirm_fn(**kwargs):
            return Choice.ALLOW_ONCE

        repl, session = _make_tool_repl(
            provider,
            store,
            console,
            inputs=[],
            registry=registry,
            executor=executor,
            pipeline=pipeline,
            confirm_fn=confirm_fn,
        )
        repl._chat_once("跑命令")

        # executor was reached (gate returned None → pass through)
        assert len(executor.calls) == 1
        roles = [m["role"] for m in session.messages]
        assert roles == ["user", "assistant", "tool", "assistant"]

    def test_ask_deny_feeds_back_and_continues(self, tmp_path):
        """Ask → confirm returns DENY → denied outcome fed back, loop continues."""
        from wentian.ui.confirm import Choice

        registry = _bash_perm_registry()
        executor = FakeExecutor()
        pipeline = _build_pipeline(tmp_path)
        provider = ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="c1", name="run_command", arguments={"command": "ls"}
                    ),
                    Done(),
                ],
                [TextDelta("换个办法"), Done()],
            ]
        )
        store = SessionStore(tmp_path)
        console = Console(record=True)

        async def confirm_fn(**kwargs):
            return Choice.DENY

        repl, session = _make_tool_repl(
            provider,
            store,
            console,
            inputs=[],
            registry=registry,
            executor=executor,
            pipeline=pipeline,
            confirm_fn=confirm_fn,
        )
        repl._chat_once("跑命令")

        # executor NOT reached (gate refused)
        assert executor.calls == []
        tool_msg = session.messages[2]
        assert tool_msg["role"] == "tool"
        assert tool_msg["is_error"] is True
        # loop continued: round 2 ran
        roles = [m["role"] for m in session.messages]
        assert roles == ["user", "assistant", "tool", "assistant"]

    def test_ask_allow_always_persists_rule(self, tmp_path):
        """ALLOW_ALWAYS → exact rule written to settings.local.yaml + live ruleset."""
        from wentian.ui.confirm import Choice
        from wentian.permissions.settings import load_settings
        from wentian.permissions.decision import Verdict

        registry = _bash_perm_registry()
        executor = FakeExecutor()
        pipeline = _build_pipeline(tmp_path)
        provider = ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="c1", name="run_command", arguments={"command": "ls -la"}
                    ),
                    Done(),
                ],
                [TextDelta("好的"), Done()],
            ]
        )
        store = SessionStore(tmp_path)
        console = Console(record=True)

        async def confirm_fn(**kwargs):
            return Choice.ALLOW_ALWAYS

        repl, session = _make_tool_repl(
            provider,
            store,
            console,
            inputs=[],
            registry=registry,
            executor=executor,
            pipeline=pipeline,
            confirm_fn=confirm_fn,
        )
        repl._chat_once("跑命令")

        # tool ran (allow)
        assert len(executor.calls) == 1

        # 1) file written
        local_file = tmp_path / ".wentian" / "settings.local.yaml"
        assert local_file.exists()
        reloaded = load_settings(tmp_path, user_path=tmp_path / "no-user.yaml")
        verdict = reloaded.rules.match(friendly="Bash", target="ls -la", is_path=False)
        assert verdict is Verdict.ALLOW

        # 2) live in-memory ruleset already has it (this session)
        live = pipeline.settings.rules.match(
            friendly="Bash", target="ls -la", is_path=False
        )
        assert live is Verdict.ALLOW

    def test_allow_always_persist_is_idempotent(self, tmp_path):
        """Two ALLOW_ALWAYS of the same rule → file has it once, no duplicate."""
        from wentian.ui.confirm import Choice
        import yaml

        registry = _bash_perm_registry()
        pipeline = _build_pipeline(tmp_path)

        async def confirm_fn(**kwargs):
            return Choice.ALLOW_ALWAYS

        store = SessionStore(tmp_path)
        console = Console(record=True)
        provider = ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="c1", name="run_command", arguments={"command": "ls -la"}
                    ),
                    Done(),
                ],
                [TextDelta("a"), Done()],
                [
                    ToolCallEvent(
                        id="c2", name="run_command", arguments={"command": "ls -la"}
                    ),
                    Done(),
                ],
                [TextDelta("b"), Done()],
            ]
        )
        repl, _ = _make_tool_repl(
            provider,
            store,
            console,
            inputs=[],
            registry=registry,
            executor=FakeExecutor(),
            pipeline=pipeline,
            confirm_fn=confirm_fn,
        )
        repl._chat_once("一")
        repl._chat_once("二")

        local_file = tmp_path / ".wentian" / "settings.local.yaml"
        data = yaml.safe_load(local_file.read_text())
        allow = data["permissions"]["allow"]
        assert allow.count("Bash(ls -la)") == 1

    def test_cancel_ends_turn_cleanly(self, tmp_path):
        """Esc/Ctrl+C during ask → Cancelled → turn ends, REPL alive, no crash."""
        from wentian.ui.confirm import Cancelled

        registry = _bash_perm_registry()
        executor = FakeExecutor()
        pipeline = _build_pipeline(tmp_path)
        provider = ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="c1", name="run_command", arguments={"command": "ls"}
                    ),
                    Done(),
                ],
            ]
        )
        store = SessionStore(tmp_path)
        console = Console(record=True)

        async def confirm_fn(**kwargs):
            raise Cancelled()

        repl, session = _make_tool_repl(
            provider,
            store,
            console,
            inputs=[],
            registry=registry,
            executor=executor,
            pipeline=pipeline,
            confirm_fn=confirm_fn,
        )
        # Must not raise / must not exit the program.
        repl._chat_once("跑命令")
        # executor never reached
        assert executor.calls == []
        # REPL still usable: a follow-up plain chat works.
        provider2 = ScriptedProvider([[TextDelta("继续"), Done()]])
        repl._provider = provider2
        repl._chat_once("你好")
        assert any(m.get("content") == "继续" for m in session.messages)


class TestT77PipelineNoneRegression:
    def test_pipeline_none_is_v05_behavior(self, tmp_path):
        """pipeline=None → no gate, tools execute as before (v0.5)."""
        registry = _bash_perm_registry()
        executor = FakeExecutor()
        provider = ScriptedProvider(
            [
                [
                    ToolCallEvent(
                        id="c1", name="run_command", arguments={"command": "ls"}
                    ),
                    Done(),
                ],
                [TextDelta("好的"), Done()],
            ]
        )
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_tool_repl(
            provider,
            store,
            console,
            inputs=[],
            registry=registry,
            executor=executor,
            pipeline=None,
        )
        repl._chat_once("跑命令")
        # No gate → executor runs.
        assert len(executor.calls) == 1
        roles = [m["role"] for m in session.messages]
        assert roles == ["user", "assistant", "tool", "assistant"]
