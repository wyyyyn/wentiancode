"""v0.12 · C99 · F78/F79/F83（任务 T124）— REPL hook seam integration tests.

TDD RED phase: all tests in this file are expected to FAIL before the
implementation changes are made to repl.py / compactor.py / cli.py.

Test coverage:
1. Seam firing — FakeHookEngine records fire(event, ctx) calls; assert all
   expected seams fire with correct context keys.
2. PreToolUse block — pretool returns reason → is_error outcome, no
   permission_gate consulted; pretool=None → falls through; pretool raising →
   fail-open.
3. Notification — when permission gate returns ASK, fire(NOTIFICATION, ...).
4. Injection — drain_injections returns text → next request contains
   <system-reminder>; session.messages unchanged; not persisted.
5. No-hooks regression — hooks=None → all seams no-op, no crash.
"""

from __future__ import annotations

from typing import Iterator

from rich.console import Console

from wentian.hooks.spec import HookEvent
from wentian.providers.base import (
    Done,
    Message,
    Provider,
    StreamEvent,
    TextDelta,
    ToolCallEvent,
    ToolSpec,
)
from wentian.render import Renderer
from wentian.session import Session, SessionStore


# ---------------------------------------------------------------------------
# FakeHookEngine — records fire calls; configurable pretool / drain_injections
# ---------------------------------------------------------------------------


class FakeHookEngine:
    """Test double for HookEngine.

    Records all ``fire(event, ctx)`` calls; ``pretool`` and
    ``drain_injections`` are configurable via constructor kwargs.
    """

    def __init__(
        self,
        *,
        pretool_return=None,
        drain_return: str = "",
        pretool_raises: Exception | None = None,
    ) -> None:
        self.fired: list[tuple[HookEvent, dict]] = []
        self._pretool_return = pretool_return
        self._drain_return = drain_return
        self._pretool_raises = pretool_raises
        self.pretool_calls: list[dict] = []
        self.drain_calls: int = 0
        self.close_calls: int = 0

    def fire(self, event: HookEvent, context: dict) -> None:
        self.fired.append((event, dict(context)))

    def pretool(self, context: dict) -> str | None:
        self.pretool_calls.append(dict(context))
        if self._pretool_raises is not None:
            raise self._pretool_raises
        return self._pretool_return

    def drain_injections(self) -> str:
        self.drain_calls += 1
        return self._drain_return

    def close(self) -> None:
        self.close_calls += 1


# ---------------------------------------------------------------------------
# FakeRegistry / FakeExecutor for tool round tests
# ---------------------------------------------------------------------------


class FakeRegistry:
    """Minimal duck-typed ToolRegistry with one tool spec."""

    def __init__(self, spec: ToolSpec) -> None:
        self._spec = spec

    def specs(self) -> list[ToolSpec]:
        return [self._spec]

    def get(self, name: str):
        return None  # not needed for these tests


class FakeExecutor:
    """Minimal duck-typed ToolExecutor — returns a fake outcome."""

    def __init__(self, content: str = "tool_result") -> None:
        self._content = content
        self.executed: list[tuple] = []

    def execute(self, call_id: str, name: str, arguments: dict | None) -> object:
        self.executed.append((call_id, name, arguments))
        return _FakeOutcome(
            content=self._content, is_error=False, call_id=call_id, tool_name=name
        )


class _FakeOutcome:
    """Duck-typed ToolOutcome."""

    def __init__(
        self,
        *,
        content: str,
        is_error: bool,
        denied: bool = False,
        call_id: str = "",
        tool_name: str = "",
    ) -> None:
        self.content = content
        self.is_error = is_error
        self.denied = denied
        self.call_id = call_id
        self.name = tool_name


# ---------------------------------------------------------------------------
# ScriptedProvider for tool-round seam tests
# ---------------------------------------------------------------------------


class ToolRoundProvider(Provider):
    """Provider that does one tool call round then completes.

    Round 1: ToolCallEvent + Done
    Round 2: TextDelta("done") + Done
    """

    name = "tool-round"
    context_window = 200_000

    def __init__(self, tool_name: str = "my_tool") -> None:
        self._tool_name = tool_name
        self.calls: list[list[Message]] = []
        self._round = 0

    def stream(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools=None,
    ) -> Iterator[StreamEvent]:
        self.calls.append(list(messages))
        if self._round == 0:
            self._round += 1
            yield ToolCallEvent(
                id="call_1", name=self._tool_name, arguments={"arg": "val"}
            )
            yield Done()
        else:
            self._round += 1
            yield TextDelta("done")
            yield Done()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SPEC = ToolSpec(
    name="my_tool",
    description="a test tool",
    parameters={"type": "object", "properties": {}, "required": []},
)


def _make_repl(
    provider: Provider,
    store: SessionStore,
    console: Console,
    *,
    session: Session | None = None,
    inputs: list[str],
    hooks=None,
    registry=None,
    executor=None,
    pipeline=None,
    confirm_fn=None,
):
    """Assemble a REPL with hook engine injected."""
    from wentian.repl import REPL

    if session is None:
        session = store.create(provider=provider.name)

    renderer = Renderer(console)
    input_iter = iter(inputs)

    def _input_fn(prompt: str = "") -> str:
        return next(input_iter)

    repl = REPL(
        provider=provider,
        session=session,
        store=store,
        renderer=renderer,
        provider_factory=lambda n: provider,
        input_fn=_input_fn,
        registry=registry,
        executor=executor,
        pipeline=pipeline,
        confirm_fn=confirm_fn,
        hooks=hooks,
    )
    return repl, session


# ===========================================================================
# 1. Seam firing
# ===========================================================================


class TestSeamFiring:
    """Assert all expected hook events fire with correct context keys."""

    def test_session_start_fires(self, tmp_path):
        """SESSION_START is fired when the REPL run() starts."""
        engine = FakeHookEngine()
        provider = ToolRoundProvider()
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(
            provider,
            store,
            console,
            inputs=["/exit"],
            hooks=engine,
        )
        repl.run()
        fired_events = [e for e, _ in engine.fired]
        assert HookEvent.SESSION_START in fired_events

    def test_session_end_fires(self, tmp_path):
        """SESSION_END is fired when the REPL exits."""
        engine = FakeHookEngine()
        provider = ToolRoundProvider()
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(
            provider,
            store,
            console,
            inputs=["/exit"],
            hooks=engine,
        )
        repl.run()
        fired_events = [e for e, _ in engine.fired]
        assert HookEvent.SESSION_END in fired_events

    def test_user_prompt_submit_fires(self, tmp_path):
        """USER_PROMPT_SUBMIT fires with prompt context key after user input."""
        engine = FakeHookEngine()
        provider = ToolRoundProvider()
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(
            provider,
            store,
            console,
            inputs=["hello world", "/exit"],
            hooks=engine,
            registry=FakeRegistry(_SPEC),
            executor=FakeExecutor(),
        )
        repl.run()
        fired_events_and_ctx = engine.fired
        ups = [
            ctx
            for ev, ctx in fired_events_and_ctx
            if ev == HookEvent.USER_PROMPT_SUBMIT
        ]
        assert len(ups) >= 1
        assert "prompt" in ups[0]
        assert ups[0]["prompt"] == "hello world"

    def test_round_start_fires(self, tmp_path):
        """ROUND_START fires at the beginning of each agent round."""
        engine = FakeHookEngine()
        provider = ToolRoundProvider()
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(
            provider,
            store,
            console,
            inputs=["hello", "/exit"],
            hooks=engine,
            registry=FakeRegistry(_SPEC),
            executor=FakeExecutor(),
        )
        repl.run()
        fired_events = [e for e, _ in engine.fired]
        assert HookEvent.ROUND_START in fired_events

    def test_post_tool_use_fires_with_tool_name(self, tmp_path):
        """POST_TOOL_USE fires after each tool result, with tool_name key."""
        engine = FakeHookEngine()
        provider = ToolRoundProvider("my_tool")
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(
            provider,
            store,
            console,
            inputs=["hello", "/exit"],
            hooks=engine,
            registry=FakeRegistry(_SPEC),
            executor=FakeExecutor(),
        )
        repl.run()
        ptu = [ctx for ev, ctx in engine.fired if ev == HookEvent.POST_TOOL_USE]
        assert len(ptu) >= 1
        assert "tool_name" in ptu[0]
        assert ptu[0]["tool_name"] == "my_tool"

    def test_round_end_fires(self, tmp_path):
        """ROUND_END fires at the end of each agent round."""
        engine = FakeHookEngine()
        provider = ToolRoundProvider()
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(
            provider,
            store,
            console,
            inputs=["hello", "/exit"],
            hooks=engine,
            registry=FakeRegistry(_SPEC),
            executor=FakeExecutor(),
        )
        repl.run()
        fired_events = [e for e, _ in engine.fired]
        assert HookEvent.ROUND_END in fired_events

    def test_stop_fires_with_stop_reason(self, tmp_path):
        """STOP fires when agent completes, with stop_reason key."""
        engine = FakeHookEngine()
        provider = ToolRoundProvider()
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(
            provider,
            store,
            console,
            inputs=["hello", "/exit"],
            hooks=engine,
            registry=FakeRegistry(_SPEC),
            executor=FakeExecutor(),
        )
        repl.run()
        stop_fired = [ctx for ev, ctx in engine.fired if ev == HookEvent.STOP]
        assert len(stop_fired) >= 1
        assert "stop_reason" in stop_fired[0]

    def test_hooks_closed_on_exit(self, tmp_path):
        """HookEngine.close() is called when REPL exits."""
        engine = FakeHookEngine()
        provider = ToolRoundProvider()
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(
            provider,
            store,
            console,
            inputs=["/exit"],
            hooks=engine,
        )
        repl.run()
        assert engine.close_calls >= 1


# ===========================================================================
# 2. PreToolUse block
# ===========================================================================


class TestPreToolUseBlock:
    """Test that pretool interception works correctly."""

    def test_pretool_returns_reason_blocks_tool(self, tmp_path):
        """pretool returning a reason string → is_error=True outcome, executor NOT called."""
        engine = FakeHookEngine(pretool_return="blocked: dangerous command")
        executor = FakeExecutor()
        provider = ToolRoundProvider("my_tool")
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_repl(
            provider,
            store,
            console,
            inputs=["do something", "/exit"],
            hooks=engine,
            registry=FakeRegistry(_SPEC),
            executor=executor,
        )
        repl.run()
        # executor was NOT called
        assert len(executor.executed) == 0
        # pretool was called
        assert len(engine.pretool_calls) >= 1
        # The tool result in session should be is_error=True with the reason
        tool_msgs = [m for m in session.messages if m.get("role") == "tool"]
        assert len(tool_msgs) >= 1
        assert "blocked" in tool_msgs[0].get("content", "")
        assert tool_msgs[0].get("is_error") is True

    def test_pretool_returns_none_falls_through(self, tmp_path):
        """pretool returning None → executor IS called (not blocked)."""
        engine = FakeHookEngine(pretool_return=None)
        executor = FakeExecutor()
        provider = ToolRoundProvider("my_tool")
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(
            provider,
            store,
            console,
            inputs=["do something", "/exit"],
            hooks=engine,
            registry=FakeRegistry(_SPEC),
            executor=executor,
        )
        repl.run()
        # executor WAS called
        assert len(executor.executed) >= 1

    def test_pretool_raises_fails_open(self, tmp_path):
        """pretool raising an exception → fail-open (executor called, no crash)."""
        engine = FakeHookEngine(pretool_raises=RuntimeError("oops"))
        executor = FakeExecutor()
        provider = ToolRoundProvider("my_tool")
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(
            provider,
            store,
            console,
            inputs=["do something", "/exit"],
            hooks=engine,
            registry=FakeRegistry(_SPEC),
            executor=executor,
        )
        # Should NOT raise
        repl.run()
        # fail-open: executor should still be called
        assert len(executor.executed) >= 1


# ===========================================================================
# 3. Notification on permission ASK
# ===========================================================================


class TestNotificationOnPermissionAsk:
    """NOTIFICATION fires when permission pipeline yields ASK."""

    def test_notification_fires_on_ask(self, tmp_path):
        """When permission gate returns ASK, NOTIFICATION hook fires."""
        from wentian.permissions.decision import Decision, Verdict

        engine = FakeHookEngine()
        executor = FakeExecutor()
        provider = ToolRoundProvider("my_tool")
        store = SessionStore(tmp_path)
        console = Console(record=True)

        # Fake pipeline that always ASKs — must match PermissionPipeline.decide signature
        class FakePipeline:
            project_root = tmp_path

            class settings:
                from wentian.permissions.decision import Mode as _Mode

                default_mode = _Mode.DEFAULT

                class rules:
                    pass

            def decide(self, *, friendly, category, mode, command, paths):
                return Decision(
                    verdict=Verdict.ASK, reason="need confirmation", source="test"
                )

        # confirm_fn that always denies (to avoid UI)
        from wentian.ui.confirm import Choice

        async def fake_confirm(*, tool_name, preview, reason):
            return Choice.DENY

        repl, _ = _make_repl(
            provider,
            store,
            console,
            inputs=["do something", "/exit"],
            hooks=engine,
            registry=FakeRegistry(_SPEC),
            executor=executor,
            pipeline=FakePipeline(),
            confirm_fn=fake_confirm,
        )
        repl.run()
        notifs = [ctx for ev, ctx in engine.fired if ev == HookEvent.NOTIFICATION]
        assert len(notifs) >= 1
        assert notifs[0].get("kind") == "permission_ask"


# ===========================================================================
# 4. Injection via drain_injections
# ===========================================================================


class TestInjection:
    """drain_injections text is injected into next request as <system-reminder>."""

    def test_drain_injections_in_outgoing_request(self, tmp_path):
        """drain_injections text appears in provider request as <system-reminder>."""
        injection_text = "remember to be careful"
        engine = FakeHookEngine(drain_return=injection_text)

        class RecordingProvider(Provider):
            name = "recording"
            context_window = 200_000

            def __init__(self):
                self.received_messages: list[list[Message]] = []

            def stream(self, messages, *, system=None, tools=None):
                self.received_messages.append(list(messages))
                yield TextDelta("ok")
                yield Done()

        record_provider = RecordingProvider()
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_repl(
            record_provider,
            store,
            console,
            inputs=["test prompt", "/exit"],
            hooks=engine,
        )
        repl.run()

        # The injection text should appear in the outgoing messages
        assert len(record_provider.received_messages) >= 1
        outgoing = record_provider.received_messages[0]
        # Find any message with the injection text
        all_content = " ".join(str(m.get("content", "")) for m in outgoing)
        assert injection_text in all_content
        assert "<system-reminder>" in all_content

    def test_injection_does_not_modify_session_messages(self, tmp_path):
        """Session messages do NOT contain the injected reminder text."""
        injection_text = "remember X special text"
        engine = FakeHookEngine(drain_return=injection_text)

        class SimpleProvider(Provider):
            name = "simple"
            context_window = 200_000

            def stream(self, messages, *, system=None, tools=None):
                yield TextDelta("response")
                yield Done()

        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_repl(
            SimpleProvider(),
            store,
            console,
            inputs=["my query", "/exit"],
            hooks=engine,
        )
        repl.run()

        # Session messages must not contain the injected text
        for msg in session.messages:
            content = str(msg.get("content", ""))
            assert injection_text not in content, (
                f"Injection text leaked into session.messages: {msg}"
            )


# ===========================================================================
# 5. No-hooks regression
# ===========================================================================


class TestNoHooksRegression:
    """hooks=None → all seams are no-ops, behavior identical to pre-hook."""

    def test_no_hooks_no_crash_simple_turn(self, tmp_path):
        """REPL with hooks=None runs a normal turn without any crash."""
        from wentian.providers.base import TextDelta, Done

        class SimpleProvider(Provider):
            name = "simple"
            context_window = 200_000

            def stream(self, messages, *, system=None, tools=None):
                yield TextDelta("answer")
                yield Done()

        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_repl(
            SimpleProvider(),
            store,
            console,
            inputs=["hello", "/exit"],
            hooks=None,  # no hooks
        )
        repl.run()
        # Normal turn: user + assistant messages
        assert len(session.messages) == 2

    def test_no_hooks_no_crash_tool_round(self, tmp_path):
        """REPL with hooks=None completes a tool round without crash."""
        provider = ToolRoundProvider("my_tool")
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_repl(
            provider,
            store,
            console,
            inputs=["hello", "/exit"],
            hooks=None,
            registry=FakeRegistry(_SPEC),
            executor=FakeExecutor(),
        )
        repl.run()
        # Tool round completed
        assert len(session.messages) > 0

    def test_hooks_none_is_default(self, tmp_path):
        """REPL can be constructed without hooks kwarg (defaults to None)."""
        from wentian.repl import REPL

        store = SessionStore(tmp_path)
        session = store.create(provider="test")
        console = Console(record=True)

        class SimpleProvider(Provider):
            name = "simple"
            context_window = 200_000

            def stream(self, messages, *, system=None, tools=None):
                yield TextDelta("ok")
                yield Done()

        repl = REPL(
            provider=SimpleProvider(),
            session=session,
            store=store,
            renderer=Renderer(console),
            provider_factory=lambda n: SimpleProvider(),
            input_fn=iter(["/exit"]).__next__,
            # No hooks= kwarg — must default to None without error
        )
        assert repl._hooks is None


# ===========================================================================
# 6. REAL HookEngine end-to-end (not a fake) — proves condition matching on
#    flattened tool args (F79/AC97), shell exit-2 interception, and mid-loop
#    injection reaching the NEXT round (F83). These exercise the real
#    parse_hooks → HookEngine → repl gate/decorator path that the FakeHookEngine
#    tests above deliberately bypass.
# ===========================================================================


class _CommandToolProvider(Provider):
    """Round 1: a tool call carrying a ``command`` argument; round 2: text+Done."""

    name = "cmd-tool"
    context_window = 200_000

    def __init__(self, command: str) -> None:
        self._command = command
        self._round = 0
        self.received: list[list[Message]] = []

    def stream(self, messages, *, system=None, tools=None):
        self.received.append(list(messages))
        if self._round == 0:
            self._round += 1
            yield ToolCallEvent(
                id="call_1", name="my_tool", arguments={"command": self._command}
            )
            yield Done()
        else:
            self._round += 1
            yield TextDelta("done")
            yield Done()


class TestRealEngineEndToEnd:
    """Real HookEngine built from parse_hooks — the true security/injection path."""

    _BLOCK_RM_RULE = [
        {
            "event": "PreToolUse",
            "if": {
                "match": "all",
                "clauses": [{"field": "command", "pattern": "/rm.*-rf/"}],
            },
            "action": {
                "type": "shell",
                "command": "echo 'blocked dangerous rm' >&2; exit 2",
            },
        }
    ]

    def test_real_pretool_regex_blocks_dangerous_command(self, tmp_path):
        """A real rule (regex on flattened ``command`` + shell exit 2) blocks rm -rf."""
        from wentian.hooks.config import parse_hooks
        from wentian.hooks.engine import HookEngine

        engine = HookEngine(parse_hooks(self._BLOCK_RM_RULE))
        executor = FakeExecutor()
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_repl(
            _CommandToolProvider("rm -rf /"),
            store,
            console,
            inputs=["delete everything", "/exit"],
            hooks=engine,
            registry=FakeRegistry(_SPEC),
            executor=executor,
        )
        repl.run()
        engine.close()
        # Blocked: executor never ran; tool result is the hook's deny reason.
        assert len(executor.executed) == 0
        tool_msgs = [m for m in session.messages if m.get("role") == "tool"]
        assert len(tool_msgs) >= 1
        assert tool_msgs[0].get("is_error") is True
        assert "blocked dangerous rm" in tool_msgs[0].get("content", "")

    def test_real_pretool_benign_command_falls_through(self, tmp_path):
        """A command not matching the regex is NOT blocked — executor runs."""
        from wentian.hooks.config import parse_hooks
        from wentian.hooks.engine import HookEngine

        engine = HookEngine(parse_hooks(self._BLOCK_RM_RULE))
        executor = FakeExecutor()
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_repl(
            _CommandToolProvider("ls -la"),
            store,
            console,
            inputs=["list files", "/exit"],
            hooks=engine,
            registry=FakeRegistry(_SPEC),
            executor=executor,
        )
        repl.run()
        engine.close()
        # Benign command → condition does not match → executor IS called.
        assert len(executor.executed) >= 1

    def test_real_posttool_prompt_injection_reaches_next_round(self, tmp_path):
        """A PostToolUse prompt action's text reaches the NEXT round's request."""
        from wentian.hooks.config import parse_hooks
        from wentian.hooks.engine import HookEngine

        rules = parse_hooks(
            [
                {
                    "event": "PostToolUse",
                    "action": {"type": "prompt", "text": "INJECTED_AFTER_TOOL"},
                }
            ]
        )
        engine = HookEngine(rules)
        executor = FakeExecutor()
        store = SessionStore(tmp_path)
        console = Console(record=True)
        provider = _CommandToolProvider("ls")
        repl, _ = _make_repl(
            provider,
            store,
            console,
            inputs=["go", "/exit"],
            hooks=engine,
            registry=FakeRegistry(_SPEC),
            executor=executor,
        )
        repl.run()
        engine.close()
        # Two provider rounds were issued; the injection (fired on PostToolUse in
        # round 1) must land in round 2's request, NOT round 1's.
        assert len(provider.received) >= 2
        round1 = " ".join(str(m.get("content", "")) for m in provider.received[0])
        round2 = " ".join(str(m.get("content", "")) for m in provider.received[1])
        assert "INJECTED_AFTER_TOOL" not in round1
        assert "INJECTED_AFTER_TOOL" in round2
        assert "<system-reminder>" in round2
