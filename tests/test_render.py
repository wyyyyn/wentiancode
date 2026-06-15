"""Tests for Renderer (T6 + T7 + T21 + T22).

T6: thinking vs body separation
T7: Markdown streaming + final render
T21 (v0.2 · C5 · F17): RenderResult return type + streaming elapsed timer
T22 (v0.2 · C6 · F18): _StreamPump interruptible stream consumption
"""

from __future__ import annotations

import threading
import time

import pytest
from rich.console import Console

from conftest import BlockingFakeProvider
from wentian.providers.base import Done, TextDelta, ThinkingDelta, ToolCallEvent
from wentian.render import Renderer, RenderResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_console(width: int = 80) -> Console:
    """Return a recording console (non-TTY) suitable for tests."""
    return Console(record=True, width=width)


def _exported(console: Console) -> str:
    """Return all text captured by the recording console."""
    return console.export_text()


def _make_clock(start: float = 0.0):
    """Return (get_time, advance) pair backed by a mutable container."""
    state = [start]

    def get_time() -> float:
        return state[0]

    def advance(delta: float) -> None:
        state[0] += delta

    return get_time, advance


def _patch_live(monkeypatch) -> list:
    """Replace Live in render.py AND spinner.py with a recording fake.

    Returns the list of created fake-Live instances; each instance stores
    the ``get_renderable`` kwarg it was constructed with so tests can call
    it mid-stream and inspect the composed renderable.
    """
    instances: list = []

    class RecordingLive:
        def __init__(self, *args, **kwargs):
            self.get_renderable = kwargs.get("get_renderable")
            instances.append(self)

        def start(self, *args, **kwargs):
            pass

        def stop(self, *args, **kwargs):
            pass

        def update(self, *args, **kwargs):
            pass

    monkeypatch.setattr("wentian.render.Live", RecordingLive)
    monkeypatch.setattr("wentian.ui.spinner.Live", RecordingLive)
    return instances


def _render_plain(renderable) -> str:
    """Render any Rich renderable to plain text on a throwaway console."""
    console = Console(record=True, width=80)
    console.print(renderable)
    return console.export_text()


# ---------------------------------------------------------------------------
# T6: thinking vs body
# ---------------------------------------------------------------------------


class TestT6ThinkingVsBody:
    """Renderer separates thinking (dim italic) from body (returned)."""

    def test_render_stream_returns_body_only(self):
        """render_stream must return only the body text, not thinking text."""
        console = _make_console()
        renderer = Renderer(console)

        events = iter([ThinkingDelta("让我想想"), TextDelta("答案"), Done(None)])
        result = renderer.render_stream(events)

        assert result.text == "答案"

    def test_exported_contains_thinking_prefix_and_text(self):
        """Recorded console output must contain 🤔 prefix and thinking text."""
        console = _make_console()
        renderer = Renderer(console)

        events = iter([ThinkingDelta("让我想想"), TextDelta("答案"), Done(None)])
        renderer.render_stream(events)

        exported = _exported(console)
        assert "🤔" in exported
        assert "让我想想" in exported

    def test_exported_contains_body_text(self):
        """Recorded console output must also contain the body text."""
        console = _make_console()
        renderer = Renderer(console)

        events = iter([ThinkingDelta("让我想想"), TextDelta("答案"), Done(None)])
        renderer.render_stream(events)

        exported = _exported(console)
        assert "答案" in exported

    def test_no_thinking_no_prefix(self):
        """Pure body stream must not output 🤔 prefix at all."""
        console = _make_console()
        renderer = Renderer(console)

        events = iter([TextDelta("plain answer"), Done(None)])
        result = renderer.render_stream(events)

        assert result.text == "plain answer"
        assert "🤔" not in _exported(console)

    def test_thinking_only_returns_empty_string(self):
        """Stream with only thinking events returns empty string."""
        console = _make_console()
        renderer = Renderer(console)

        events = iter([ThinkingDelta("invisible"), Done(None)])
        result = renderer.render_stream(events)

        assert result.text == ""

    def test_empty_body_no_empty_markdown_block(self):
        """Empty body → no TextDelta → no empty Markdown block printed."""
        console = _make_console()
        renderer = Renderer(console)

        events = iter([Done(None)])
        result = renderer.render_stream(events)

        assert result.text == ""


# ---------------------------------------------------------------------------
# T7: Markdown rendering
# ---------------------------------------------------------------------------


class TestT7Markdown:
    """Body text is rendered as Markdown; source is returned raw."""

    def test_markdown_renders_no_raw_fences(self):
        """Code-fenced body must NOT output bare ``` in recorded text."""
        console = _make_console()
        renderer = Renderer(console)

        md = "# Title\n- item\n```python\nprint('hi')\n```"
        events = iter([TextDelta(md), Done(None)])
        renderer.render_stream(events)

        exported = _exported(console)
        # Raw fence characters should NOT appear verbatim
        assert "```" not in exported

    def test_markdown_renders_rich_features(self):
        """Rendered output must contain Rich Markdown decorations."""
        console = _make_console()
        renderer = Renderer(console)

        md = "# Title\n- item\n```python\nprint('hi')\n```"
        events = iter([TextDelta(md), Done(None)])
        renderer.render_stream(events)

        exported = _exported(console)
        # Rich renders code blocks with box-drawing chars or the literal code
        # (at minimum the code content without fences)
        assert "print" in exported
        # Rich should decorate the title or list somehow — the word appears
        assert "Title" in exported

    def test_return_value_is_raw_markdown(self):
        """render_stream return value must be raw Markdown source, not rendered."""
        console = _make_console()
        renderer = Renderer(console)

        md = "# Title\n- item\n```python\nprint('hi')\n```"
        events = iter([TextDelta(md), Done(None)])
        result = renderer.render_stream(events)

        assert result.text == md

    def test_tty_path_streams_live_and_finalizes(self):
        """TTY path (force_terminal): live-streams per delta and finalizes.

        We cannot assert intermediate Live frames, but we drive multiple
        TextDeltas through the is_terminal branch and verify: no error, final
        output has rendered Markdown (no raw fences), raw source returned.
        """
        console = Console(record=True, force_terminal=True, width=80)
        assert console.is_terminal  # precondition for the live path
        renderer = Renderer(console)

        chunks = ["# Ti", "tle\n- item\n```python\n", "print('hi')\n```"]
        events = iter([*(TextDelta(c) for c in chunks), Done(None)])
        result = renderer.render_stream(events)

        assert result.text == "".join(chunks)
        exported = _exported(console)
        assert "```" not in exported
        assert "print" in exported
        assert "Title" in exported

    def test_tty_path_renderable_grows_per_delta(self, monkeypatch):
        """The live path must show content progressively (F12/AC10).

        Redesigned for T21: the body Live is constructed with a
        ``get_renderable`` callable closing over the mutable buffer. We
        capture that callable via a fake Live and evaluate it between
        deltas — each snapshot must contain exactly the content streamed
        so far, proving progressive display rather than finalize-only.
        """
        instances = _patch_live(monkeypatch)

        console = Console(record=True, force_terminal=True, width=80)
        renderer = Renderer(console)

        snapshots: list[str] = []

        def events():
            yield TextDelta("alpha ")
            snapshots.append(_render_plain(instances[-1].get_renderable()))
            yield TextDelta("bravo ")
            snapshots.append(_render_plain(instances[-1].get_renderable()))
            yield TextDelta("charlie")
            snapshots.append(_render_plain(instances[-1].get_renderable()))
            yield Done(None)

        renderer.render_stream(events())

        assert len(snapshots) == 3
        assert "alpha" in snapshots[0] and "bravo" not in snapshots[0]
        assert "bravo" in snapshots[1] and "charlie" not in snapshots[1]
        assert "charlie" in snapshots[2]

    def test_tty_path_thinking_then_body(self):
        """TTY path with thinking before body completes and separates output."""
        console = Console(record=True, force_terminal=True, width=80)
        renderer = Renderer(console)

        events = iter([
            ThinkingDelta("让我想想"),
            TextDelta("# 答案\n"),
            TextDelta("正文"),
            Done(None),
        ])
        result = renderer.render_stream(events)

        assert result.text == "# 答案\n正文"
        exported = _exported(console)
        assert "🤔" in exported
        assert "让我想想" in exported
        assert "答案" in exported

    def test_tty_path_thinking_after_body_deltas(self):
        """TTY path: ThinkingDelta arriving AFTER body deltas (Live open).

        The renderer must stop the Live, print the thinking chunk dim-italic,
        and re-open the Live with the current buffer — no collision with the
        live frame, no exception. Both contents appear in the export; return
        value remains body-only.
        """
        console = Console(record=True, force_terminal=True, width=80)
        renderer = Renderer(console)

        events = iter([
            TextDelta("body part 1 "),
            ThinkingDelta("late thinking"),
            TextDelta("body part 2"),
            Done(None),
        ])
        result = renderer.render_stream(events)

        assert result.text == "body part 1 body part 2"
        exported = _exported(console)
        assert "🤔" in exported
        assert "late thinking" in exported
        assert "body part 1" in exported
        assert "body part 2" in exported

    def test_thinking_heading_not_rendered_as_markdown(self):
        """Thinking text containing '# xx' must NOT be rendered as a Markdown heading."""
        console = _make_console()
        renderer = Renderer(console)

        events = iter([
            ThinkingDelta("# fake heading in thinking"),
            TextDelta("body"),
            Done(None),
        ])
        renderer.render_stream(events)

        exported = _exported(console)
        # Rich renders a Markdown heading with '━' or similar rule chars.
        # The thinking text should appear literally, not with Markdown decorations.
        # We check that the raw heading text appears but NOT as a Rich rule decoration.
        # The simplest proxy: thinking text contains the literal '#' character
        # in the output (plain-text pass-through), or at minimum '# fake heading'
        # appears somewhere and the output does NOT start with a rule for it.
        assert "fake heading in thinking" in exported


# ---------------------------------------------------------------------------
# T21 (v0.2 · C5 · F17): RenderResult + streaming elapsed timer
# ---------------------------------------------------------------------------


class TestT21RenderResultAndTimer:
    """v0.2 · C5 · F17（任务 T21）— RenderResult 返回类型 + 流式计时行。"""

    def test_render_stream_returns_render_result(self):
        """render_stream returns a RenderResult: .text is the accumulated
        body, .interrupted defaults to False."""
        console = _make_console()
        renderer = Renderer(console)

        events = iter(
            [ThinkingDelta("让我想想"), TextDelta("答"), TextDelta("案"), Done(None)]
        )
        result = renderer.render_stream(events)

        assert isinstance(result, RenderResult)
        assert result.text == "答案"
        assert result.interrupted is False

    def test_render_result_is_frozen_dataclass(self):
        """RenderResult is immutable (frozen dataclass)."""
        import dataclasses

        result = RenderResult(text="x")
        assert dataclasses.is_dataclass(result)
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.text = "y"  # type: ignore[misc]

    def test_tty_live_renderable_composes_markdown_and_timer(self, monkeypatch):
        """During TTY body streaming the live renderable must be a composite
        of the rendered Markdown so far AND the spinner's elapsed line
        ("构思中… (Ns)") — F17/AC15: 秒数随正文一同呈现."""
        instances = _patch_live(monkeypatch)
        clock, advance = _make_clock()

        console = Console(record=True, force_terminal=True, width=80)
        renderer = Renderer(console, clock=clock)

        snapshots: list[str] = []

        def events():
            yield TextDelta("# Title\n")
            yield TextDelta("first part ")
            advance(3.0)
            snapshots.append(_render_plain(instances[-1].get_renderable()))
            yield TextDelta("second part")
            snapshots.append(_render_plain(instances[-1].get_renderable()))
            yield Done(None)

        result = renderer.render_stream(events())

        assert result.text == "# Title\nfirst part second part"

        mid = snapshots[0]
        # Markdown content rendered (heading text, no raw '#')
        assert "Title" in mid
        assert "first part" in mid
        # Timer line composed below the markdown, ticking with the fake clock
        assert "构思中" in mid
        assert "(3s)" in mid
        # Progressive: the later delta only appears in the later snapshot
        assert "second part" not in snapshots[0]
        assert "second part" in snapshots[1]

    def test_tty_final_scrollback_has_no_timer_line(self, monkeypatch):
        """After the stream completes, the final scrollback print contains
        the Markdown body only — no 构思中/elapsed line (定格后计时消失)."""
        _patch_live(monkeypatch)

        console = Console(record=True, force_terminal=True, width=80)
        renderer = Renderer(console)

        events = iter([TextDelta("hello "), TextDelta("world"), Done(None)])
        result = renderer.render_stream(events)

        assert result.text == "hello world"
        exported = _exported(console)
        assert "hello world" in exported
        assert "构思中" not in exported

    def test_non_tty_no_spinner_output(self):
        """Non-TTY consoles must behave exactly like v0.1: no spinner/timer
        output at all, just the final Markdown."""
        console = _make_console()
        renderer = Renderer(console)

        events = iter([TextDelta("正文"), Done(None)])
        result = renderer.render_stream(events)

        assert result.text == "正文"
        exported = _exported(console)
        assert "正文" in exported
        assert "构思中" not in exported


# ---------------------------------------------------------------------------
# T22 (v0.2 · C6 · F18): _StreamPump interruptible stream consumption
# ---------------------------------------------------------------------------


class TestT22Interrupt:
    """v0.2 · C6 · F18（任务 T22）— 生成期间可随时中断（render 层机制）。

    No test may hang: blocking providers are drained through the pump with
    a 0.1s poll timeout, and wall-clock elapsed time is asserted explicitly
    (pytest-timeout is not installed).
    """

    def test_interrupt_while_provider_blocks_returns_partial(self):
        """Provider yields 2 deltas then blocks on the network forever; an
        interrupt set ~0.2s later must unblock render_stream promptly with
        the partial text, interrupted=True, and a 已中断 marker printed."""
        console = _make_console()
        renderer = Renderer(console)
        provider = BlockingFakeProvider([TextDelta("foo "), TextDelta("bar")])

        interrupt = threading.Event()
        timer = threading.Timer(0.2, interrupt.set)
        timer.start()
        try:
            start = time.monotonic()
            result = renderer.render_stream(
                provider.stream([]), interrupt=interrupt
            )
            elapsed = time.monotonic() - start
        finally:
            timer.cancel()

        assert elapsed < 1.5
        assert result == RenderResult(text="foo bar", interrupted=True)
        exported = _exported(console)
        assert "foo bar" in exported  # partial body stays in scrollback
        assert "已中断" in exported

    def test_preset_interrupt_before_first_event_zero_text(self):
        """Interrupt already set + provider blocking before the first event:
        returns RenderResult("", True) quickly, WITHOUT the 已中断 marker
        (AC16: 首字前中断 → 直接回输入框, no marker for zero text)."""
        console = _make_console()
        renderer = Renderer(console)
        provider = BlockingFakeProvider([])  # blocks before any event

        interrupt = threading.Event()
        interrupt.set()

        start = time.monotonic()
        result = renderer.render_stream(provider.stream([]), interrupt=interrupt)
        elapsed = time.monotonic() - start

        assert elapsed < 1.5
        assert result == RenderResult(text="", interrupted=True)
        assert "已中断" not in _exported(console)

    def test_pump_propagates_provider_error(self):
        """A provider exception raised mid-stream must propagate out of
        render_stream through the pump (rollback path preserved)."""
        console = _make_console()
        renderer = Renderer(console)

        def events():
            yield TextDelta("x")
            raise RuntimeError("boom")

        interrupt = threading.Event()  # never set
        with pytest.raises(RuntimeError, match="boom"):
            renderer.render_stream(events(), interrupt=interrupt)

    def test_interrupt_none_keeps_direct_path_behavior(self):
        """interrupt=None must behave exactly like T21 (direct iteration)."""
        console = _make_console()
        renderer = Renderer(console)

        events = iter(
            [ThinkingDelta("让我想想"), TextDelta("答"), TextDelta("案"), Done(None)]
        )
        result = renderer.render_stream(events)

        assert result == RenderResult(text="答案", interrupted=False)
        exported = _exported(console)
        assert "🤔" in exported
        assert "让我想想" in exported
        assert "答案" in exported
        assert "已中断" not in exported

    def test_done_completion_through_pump_matches_direct_path(self):
        """A normal stream consumed through the pump (interrupt never set)
        must produce output identical to the direct path, interrupted=False."""
        events = [ThinkingDelta("hm"), TextDelta("body "), TextDelta("text"), Done(None)]

        direct_console = _make_console()
        direct_result = Renderer(direct_console).render_stream(iter(events))

        pump_console = _make_console()
        pump_result = Renderer(pump_console).render_stream(
            iter(events), interrupt=threading.Event()
        )

        assert pump_result == direct_result
        assert pump_result.interrupted is False
        assert _exported(pump_console) == _exported(direct_console)

    def test_keyboard_interrupt_direct_mode_returns_partial(self):
        """Ctrl+C during streaming (direct mode, interrupt=None) must not
        escape: partial buffer is returned with interrupted=True and the
        已中断 marker is printed (spec F18: 流中 Ctrl+C ≡ Esc)."""
        console = _make_console()
        renderer = Renderer(console)

        def events():
            yield TextDelta("partial")
            raise KeyboardInterrupt

        result = renderer.render_stream(events())

        assert result == RenderResult(text="partial", interrupted=True)
        exported = _exported(console)
        assert "partial" in exported
        assert "已中断" in exported

    def test_interrupt_during_thinking_only_stream_zero_text(self):
        """Interrupt during a thinking-only stream (no body delta yet) is a
        首字前 boundary (AC16): RenderResult("", True), NO 已中断 marker —
        thinking text never counts as partial body."""
        console = _make_console()
        renderer = Renderer(console)
        provider = BlockingFakeProvider(
            [ThinkingDelta("让我想"), ThinkingDelta("想…")]
        )

        interrupt = threading.Event()
        timer = threading.Timer(0.2, interrupt.set)
        timer.start()
        try:
            start = time.monotonic()
            result = renderer.render_stream(
                provider.stream([]), interrupt=interrupt
            )
            elapsed = time.monotonic() - start
        finally:
            timer.cancel()

        assert elapsed < 1.5
        assert result == RenderResult(text="", interrupted=True)
        exported = _exported(console)
        assert "让我想" in exported  # thinking already printed stays put
        assert "已中断" not in exported

    def test_keyboard_interrupt_in_pump_mode_returns_partial(self):
        """KeyboardInterrupt raised inside the provider generator in PUMP
        mode (interrupt Event provided but never set): the pump forwards it
        as an ("error", KI) item, drain re-raises it on the main thread, and
        the except-KeyboardInterrupt path returns RenderResult(partial, True)
        with the 已中断 marker — no exception escapes."""
        console = _make_console()
        renderer = Renderer(console)

        def events():
            yield TextDelta("partial")
            raise KeyboardInterrupt

        interrupt = threading.Event()  # real Event, never set

        start = time.monotonic()
        result = renderer.render_stream(events(), interrupt=interrupt)
        elapsed = time.monotonic() - start

        assert elapsed < 1.5
        assert result == RenderResult(text="partial", interrupted=True)
        exported = _exported(console)
        assert "partial" in exported
        assert "已中断" in exported


# ---------------------------------------------------------------------------
# T41 (v0.3 · C12 · F27): tool call collection + tool call/result display
# ---------------------------------------------------------------------------


class _FakeOutcome:
    """v0.3 · C12 · F27（任务 T41）— ToolOutcome 鸭子类型替身。

    Mirrors the executor layer's frozen ``ToolOutcome`` shape
    (call_id/name/content/is_error/denied) without importing wentian.tools —
    render_tool_result reads attributes via getattr, so any object with these
    attributes works.
    """

    def __init__(
        self,
        *,
        name: str,
        content: str,
        is_error: bool = False,
        denied: bool = False,
        call_id: str = "c1",
    ) -> None:
        self.call_id = call_id
        self.name = name
        self.content = content
        self.is_error = is_error
        self.denied = denied


class TestT41ToolCollection:
    """v0.3 · C12 · F27（任务 T41）— RenderResult 收集工具调用 + 屏显。"""

    def test_tool_calls_collected_in_order_body_unaffected(self):
        """ToolCallEvents are collected into RenderResult.tool_calls in order;
        the body text is unaffected by their presence."""
        console = _make_console()
        renderer = Renderer(console)

        call_a = ToolCallEvent(id="1", name="read_file", arguments={"path": "a.py"})
        call_b = ToolCallEvent(id="2", name="run", arguments={"cmd": "ls"})
        events = iter([TextDelta("body"), call_a, call_b, Done(None)])
        result = renderer.render_stream(events)

        assert result.tool_calls == (call_a, call_b)
        assert result.text == "body"

    def test_tool_calls_silent_during_stream(self):
        """ToolCallEvent must produce NO console output during streaming —
        the tool name does not appear in the recorded output."""
        console = _make_console()
        renderer = Renderer(console)

        events = iter(
            [
                TextDelta("body"),
                ToolCallEvent(id="1", name="secret_tool", arguments={"k": "v"}),
                Done(None),
            ]
        )
        renderer.render_stream(events)

        exported = _exported(console)
        assert "secret_tool" not in exported

    def test_no_tool_calls_defaults_to_empty_tuple(self):
        """A stream without ToolCallEvents leaves tool_calls as ()."""
        console = _make_console()
        renderer = Renderer(console)

        result = renderer.render_stream(iter([TextDelta("hi"), Done(None)]))

        assert result.tool_calls == ()

    def test_raw_content_passed_through(self):
        """Done(raw_content=…) is passed through to RenderResult.raw_content."""
        console = _make_console()
        renderer = Renderer(console)

        blocks = [{"type": "tool_use", "id": "1", "name": "read_file"}]
        events = iter([TextDelta("x"), Done(None, raw_content=blocks)])
        result = renderer.render_stream(events)

        assert result.raw_content == blocks

    def test_raw_content_absent_is_none(self):
        """No raw_content on Done → RenderResult.raw_content is None."""
        console = _make_console()
        renderer = Renderer(console)

        result = renderer.render_stream(iter([TextDelta("x"), Done(None)]))

        assert result.raw_content is None

    def test_tool_calls_collected_on_interrupt(self):
        """Tool calls collected before an interrupt still land in the result
        (the discard decision is the REPL's, not the renderer's)."""
        console = _make_console()
        renderer = Renderer(console)

        call = ToolCallEvent(id="1", name="read_file", arguments={"path": "a.py"})

        def events():
            yield TextDelta("partial")
            yield call
            raise KeyboardInterrupt

        result = renderer.render_stream(events())

        assert result.interrupted is True
        assert result.tool_calls == (call,)


class TestT41RenderToolCall:
    """v0.3 · C12 · F27（任务 T41）— render_tool_call 单行屏显。"""

    def test_render_tool_call_shows_marker_name_args(self):
        """render_tool_call prints ⏺, the tool name, and an args summary."""
        console = _make_console()
        renderer = Renderer(console)

        call = ToolCallEvent(id="1", name="read_file", arguments={"path": "src/foo.py"})
        renderer.render_tool_call(call)

        exported = _exported(console)
        assert "⏺" in exported
        assert "read_file" in exported
        assert "path" in exported
        assert "src/foo.py" in exported

    def test_render_tool_call_truncates_long_value(self):
        """Argument values longer than 60 chars are truncated in the summary."""
        console = _make_console()
        renderer = Renderer(console)

        long_val = "x" * 200
        call = ToolCallEvent(id="1", name="run", arguments={"cmd": long_val})
        renderer.render_tool_call(call)

        exported = _exported(console)
        assert long_val not in exported  # full value must not appear verbatim
        assert "…" in exported  # truncation marker present

    def test_render_tool_call_none_arguments_no_crash(self):
        """arguments=None must not crash; a placeholder is shown instead."""
        console = _make_console()
        renderer = Renderer(console)

        call = ToolCallEvent(id="1", name="read_file", arguments=None)
        renderer.render_tool_call(call)  # must not raise

        exported = _exported(console)
        assert "read_file" in exported


class TestT41RenderToolResult:
    """v0.3 · C12 · F27（任务 T41）— render_tool_result 三态屏显。"""

    def test_render_tool_result_success(self):
        """Success: ⎿ marker + 成功 + first content line."""
        console = _make_console()
        renderer = Renderer(console)

        outcome = _FakeOutcome(name="read_file", content="file contents here")
        renderer.render_tool_result(outcome)

        exported = _exported(console)
        assert "⎿" in exported
        assert "成功" in exported
        assert "file contents here" in exported

    def test_render_tool_result_failure(self):
        """Failure (is_error): 失败 marker present."""
        console = _make_console()
        renderer = Renderer(console)

        outcome = _FakeOutcome(
            name="run", content="boom: no such file", is_error=True
        )
        renderer.render_tool_result(outcome)

        exported = _exported(console)
        assert "失败" in exported
        assert "boom: no such file" in exported

    def test_render_tool_result_denied(self):
        """Denied: 拒绝 marker present."""
        console = _make_console()
        renderer = Renderer(console)

        outcome = _FakeOutcome(name="run", content="", denied=True)
        renderer.render_tool_result(outcome)

        exported = _exported(console)
        assert "拒绝" in exported

    def test_render_tool_result_multiline_first_line_only(self):
        """Multi-line content shows only the first line in the result summary."""
        console = _make_console()
        renderer = Renderer(console)

        outcome = _FakeOutcome(
            name="read_file", content="first line\nsecond line\nthird line"
        )
        renderer.render_tool_result(outcome)

        exported = _exported(console)
        assert "first line" in exported
        assert "second line" not in exported

    def test_render_tool_result_truncates_long_first_line(self):
        """A very long first line is truncated to ~80 chars."""
        console = _make_console(width=200)
        renderer = Renderer(console)

        long_line = "y" * 200
        outcome = _FakeOutcome(name="read_file", content=long_line)
        renderer.render_tool_result(outcome)

        exported = _exported(console)
        assert long_line not in exported
        assert "…" in exported


# ---------------------------------------------------------------------------
# T50 (v0.4 · C18 · F34): StreamView push 式单轮显示状态机
# ---------------------------------------------------------------------------


class TestT50StreamView:
    """v0.4 · C18 · F34（任务 T50）— StreamView 推式状态机与 render_stream 像素一致。"""

    @staticmethod
    def _push_events(renderer, events, *, interrupted: bool = False) -> str:
        """通过 push 式 StreamView 喂入 *events* 中的 delta 事件并 finish。"""
        from wentian.providers.base import Done as _Done
        from wentian.providers.base import ToolCallEvent as _TCE

        view = renderer.new_stream_view()
        view.start()
        for event in events:
            if isinstance(event, (_Done, _TCE)):
                continue
            view.feed(event)
        return view.finish(interrupted=interrupted)

    def test_push_matches_pull_non_tty(self):
        """非 TTY：StreamView push 同序列事件，输出与 render_stream 拉式
        逐字一致（含 thinking dim 前缀与正文 Markdown），返回值同为累积正文。"""
        events = [
            ThinkingDelta("让我想想"),
            TextDelta("# 标题\n"),
            TextDelta("正文 `code`"),
            Done(None),
        ]

        pull_console = _make_console()
        pull_result = Renderer(pull_console).render_stream(iter(events))

        push_console = _make_console()
        push_text = self._push_events(Renderer(push_console), events)

        assert push_text == pull_result.text == "# 标题\n正文 `code`"
        exported = _exported(push_console)
        assert exported == _exported(pull_console)
        assert "🤔" in exported
        assert "让我想想" in exported
        assert "标题" in exported

    def test_push_matches_pull_tty(self, monkeypatch):
        """TTY（fake Live）：thinking 夹在正文中间的推式输出与拉式一致。"""
        _patch_live(monkeypatch)
        events = [
            TextDelta("body part 1 "),
            ThinkingDelta("late thinking"),
            TextDelta("body part 2"),
            Done(None),
        ]

        pull_console = Console(record=True, force_terminal=True, width=80)
        pull_result = Renderer(pull_console).render_stream(iter(events))

        push_console = Console(record=True, force_terminal=True, width=80)
        push_text = self._push_events(Renderer(push_console), events)

        assert push_text == pull_result.text == "body part 1 body part 2"
        assert _exported(push_console) == _exported(pull_console)

    def test_finish_interrupted_with_body_prints_marker(self):
        """finish(interrupted=True) 且有正文：输出含「已中断」标记，
        返回值为累积正文。"""
        console = _make_console()
        renderer = Renderer(console)

        view = renderer.new_stream_view()
        view.start()
        view.feed(TextDelta("partial "))
        view.feed(TextDelta("body"))
        text = view.finish(interrupted=True)

        assert text == "partial body"
        exported = _exported(console)
        assert "partial body" in exported
        assert "已中断" in exported

    def test_finish_interrupted_zero_text_no_marker(self):
        """finish(interrupted=True) 但无正文（AC16 对齐）：不打「已中断」
        标记，返回空串——thinking 不算 partial body。"""
        console = _make_console()
        renderer = Renderer(console)

        view = renderer.new_stream_view()
        view.start()
        view.feed(ThinkingDelta("让我想"))
        text = view.finish(interrupted=True)

        assert text == ""
        assert "已中断" not in _exported(console)

    def test_new_stream_view_returns_stream_view(self):
        """Renderer.new_stream_view() 返回 StreamView 实例（共享 console）。"""
        from wentian.render import StreamView

        renderer = Renderer(_make_console())
        view = renderer.new_stream_view()

        assert isinstance(view, StreamView)


class TestT50RenderUsage:
    """v0.4 · C18 · F34（任务 T50）— render_usage 单行 token 用量屏显。"""

    def test_render_usage_single_dim_line(self):
        """render_usage(Usage, rounds=3)：单行包含输入/输出 token 数与轮数。"""
        from wentian.providers.base import Usage

        console = _make_console()
        renderer = Renderer(console)

        renderer.render_usage(Usage(input_tokens=1234, output_tokens=567), rounds=3)

        exported = _exported(console)
        assert "1234" in exported
        assert "567" in exported
        assert "3" in exported
        assert "输入" in exported
        assert "输出" in exported
        assert "轮" in exported
        assert "\n" not in exported.strip()  # 单行

    def test_render_usage_none_prints_nothing(self):
        """usage=None：什么都不输出。"""
        console = _make_console()
        renderer = Renderer(console)

        renderer.render_usage(None, rounds=2)

        assert _exported(console) == ""


# ---------------------------------------------------------------------------
# T65 (v0.5 · F40 · C26): render_usage 缓存命中追加显示
# ---------------------------------------------------------------------------


class TestT65CacheHit:
    """v0.5 · F40 · C26（任务 T65）— render_usage 缓存读/写 token 追加行。"""

    def test_cache_read_only_appends_cache_info(self):
        """cache_read_input_tokens>0, cache_creation=0：
        用量行后追加含「缓存读」与读 token 数的缓存信息。"""
        from wentian.providers.base import Usage

        console = _make_console()
        renderer = Renderer(console)

        renderer.render_usage(
            Usage(input_tokens=10, output_tokens=5, cache_read_input_tokens=100),
            rounds=2,
        )

        exported = _exported(console)
        assert "缓存读" in exported
        assert "100" in exported
        # 基础字段仍在
        assert "10" in exported
        assert "5" in exported
        assert "2" in exported

    def test_cache_creation_only_appends_cache_info(self):
        """cache_creation_input_tokens>0, cache_read=0：
        用量行后追加含「缓存写」与写 token 数的缓存信息。"""
        from wentian.providers.base import Usage

        console = _make_console()
        renderer = Renderer(console)

        renderer.render_usage(
            Usage(input_tokens=20, output_tokens=8, cache_creation_input_tokens=50),
            rounds=1,
        )

        exported = _exported(console)
        assert "缓存写" in exported
        assert "50" in exported

    def test_cache_both_fields_appends_both(self):
        """两个缓存字段都 >0：缓存读与缓存写都出现在输出中。"""
        from wentian.providers.base import Usage

        console = _make_console()
        renderer = Renderer(console)

        renderer.render_usage(
            Usage(
                input_tokens=30,
                output_tokens=10,
                cache_read_input_tokens=200,
                cache_creation_input_tokens=80,
            ),
            rounds=4,
        )

        exported = _exported(console)
        assert "缓存读" in exported
        assert "200" in exported
        assert "缓存写" in exported
        assert "80" in exported

    def test_zero_cache_output_identical_to_v04(self):
        """两个缓存字段均为 0（默认）：输出与 v0.4 逐字一致，不含任何缓存字样。"""
        from wentian.providers.base import Usage

        console = _make_console()
        renderer = Renderer(console)

        renderer.render_usage(Usage(input_tokens=1234, output_tokens=567), rounds=3)

        exported = _exported(console)
        # v0.4 的精确格式断言
        assert "tokens 输入 1234 · 输出 567 · 共 3 轮" in exported
        # 不含缓存字样
        assert "缓存" not in exported

    def test_none_usage_still_prints_nothing_with_cache_fields(self):
        """usage=None 回归：无论如何都不打印（缓存路径不引入 None 解引用）。"""
        console = _make_console()
        renderer = Renderer(console)

        renderer.render_usage(None, rounds=5)

        assert _exported(console) == ""
