"""Renderer — displays thinking and body stream events in the terminal.

Thinking events are shown in dim italic plain text (never Markdown-rendered).
Body events are accumulated, rendered as Rich Markdown, and the raw source is
returned for session history.

TTY path (T7 + T21): a WaitingSpinner shows an animated elapsed-seconds line
while waiting for the first event; at the first event the spinner's own Live
is stopped (Live mutual exclusion) and, once body deltas arrive, a transient
``rich.live.Live`` is opened whose ``get_renderable`` composes
``Group(Markdown(buffer), spinner.render_line())`` — the elapsed line keeps
ticking under the streaming Markdown (F17/AC15). The buffer list is mutated
in place, so the closure always sees the latest content; Live's internal
refresh thread caps repaints at ~10 fps. After the stream ends the Live is
closed (transient erases it, including the timer line) and the final
``Markdown(full)`` is printed once so scrollback keeps the rendered output
without any timer.

Non-TTY path (tests / pipes): Live and spinner output are skipped entirely;
the final rendered Markdown is printed once at the end.

v0.4 · C18 · F34（任务 T50）: the per-stream display state machine lives in
the push-mode :class:`StreamView` (``start → feed* → finish``), driven
per-round by the agent loop; ``render_stream`` is a thin pull-wrapper around
it with pixel-identical output. ``render_usage`` prints the per-loop token
usage line.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text

from wentian.providers.base import (
    Done,
    StreamEvent,
    TextDelta,
    ThinkingDelta,
    ToolCallEvent,
    Usage,
)
from wentian.ui.spinner import WaitingSpinner

__all__ = ["RenderResult", "Renderer", "StreamView"]

_THINKING_PREFIX = "🤔 思考中…"


@dataclass(frozen=True)
class RenderResult:
    """v0.2 · C5 · F17（任务 T21）— render_stream 的结构化返回值。

    Attributes
    ----------
    text:
        The accumulated raw body text (Markdown source). Thinking text is
        excluded. ``""`` when no body deltas were received.
    interrupted:
        True when the stream was cut short by the user — via the
        ``interrupt`` Event or Ctrl+C (v0.2 · C6 · F18，任务 T22). REPL
        semantics land in T23, the Esc listener in T24.
    tool_calls:
        v0.3 · C12 · F27（任务 T41）— the ``ToolCallEvent`` instances the
        model requested, in arrival order; ``()`` when none. Collected even
        on interrupt — the discard decision lives in the REPL layer.
    raw_content:
        v0.3 · C12 · F27（任务 T41）— the provider-native assistant content
        blocks from the final ``Done.raw_content`` (passed through verbatim
        for faithful tool-result continuation); ``None`` when absent.
    """

    text: str
    interrupted: bool = False
    tool_calls: tuple = ()
    raw_content: list | None = None


class _StreamPump:
    """v0.2 · C6 · F18（任务 T22）— 可中断的流消费泵。

    The main thread originally blocked directly on the provider's network
    read, so it could not react to an interrupt while waiting for the next
    event. The pump moves that blocking ``next()`` onto a daemon thread and
    feeds events through a :class:`queue.Queue`; the main thread polls the
    queue with a short timeout via :meth:`drain`, checking the interrupt
    Event between polls — both 等待首内容期间 and 正文流式期间 stay
    responsive.

    Items on the queue are ``(kind, payload)`` tuples:
    ``("event", StreamEvent)`` / ``("error", BaseException)`` /
    ``("end", None)``.

    After :meth:`stop`, the pump thread exits at the next event boundary
    and closes the underlying generator (same-thread, legal). If the thread
    is blocked inside a network read, it simply dangles until the SDK
    timeout — acceptable because it is a daemon thread.
    """

    def __init__(self, events: Iterator[StreamEvent]) -> None:
        self._events = events
        self._queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        """Pump thread body: forward events until stop/end/error."""
        try:
            for event in self._events:
                if self._stop.is_set():
                    # Generator close from the iterating thread is legal;
                    # discard the event already pulled — the consumer left.
                    close = getattr(self._events, "close", None)
                    if close is not None:
                        close()
                    return
                self._queue.put(("event", event))
        except BaseException as exc:  # noqa: BLE001 — includes KeyboardInterrupt
            # Exactly one terminal item per stream: an error replaces "end".
            self._queue.put(("error", exc))
            return
        self._queue.put(("end", None))

    def stop(self) -> None:
        """Ask the pump thread to exit at the next event boundary."""
        self._stop.set()

    def drain(self, interrupt: threading.Event) -> Iterator[StreamEvent]:
        """Yield events until end/error, returning early when *interrupt* is set.

        Polls the queue with a 0.1s timeout so the interrupt is noticed
        within ~100ms even while the provider is silent. The interrupt is
        also checked BEFORE yielding an already-dequeued event, so no extra
        content leaks out after the user interrupts.
        """
        while True:
            try:
                kind, payload = self._queue.get(timeout=0.1)
            except queue.Empty:
                if interrupt.is_set():
                    self.stop()
                    return
                continue
            if kind == "event":
                if interrupt.is_set():
                    self.stop()
                    return
                yield payload  # type: ignore[misc]
            elif kind == "error":
                raise payload  # type: ignore[misc]
            else:  # "end"
                return


class StreamView:
    """v0.4 · C18 · F34（任务 T50）— push 式单轮显示状态机。

    spinner-直到首事件、dim 斜体 thinking、瞬态 Live Markdown 正文、定稿落
    滚动区。从 ``Renderer.render_stream`` 的拉式事件循环中原样抽出，像素与
    拉式路径一致；v0.4 的 agent loop 通过 ``start → feed* → finish`` 按轮
    推动显示，而 render_stream 仍以薄包装方式复用本类。

    非 TTY（测试/管道）下 Live 与 spinner 输出全部跳过，只在 finish 时打
    一次终稿 Markdown — 与 render_stream 既有行为一致。

    Parameters
    ----------
    console:
        共享的 Rich Console。
    spinner:
        可重启的 WaitingSpinner（每次 start() 都开新 Live）。
    """

    def __init__(self, console: Console, spinner: WaitingSpinner) -> None:
        self._console = console
        self._spinner = spinner
        self._body_buffer: list[str] = []
        # 正文 Live 打开后才到达的 thinking（R1 reasoning 与 content 交错时）。
        # 并入正文 Live 的 renderable 显示，绝不 stop/reopen 正文 Live——后者
        # 在「stop → 直接打印 → reopen」夹层里会把瞬态擦除算错行数，留下重复
        # 正文残影（同一段回复打印多遍的根因）。
        self._late_thinking: list[str] = []
        self._thinking_started = False
        self._first_event_seen = False
        self._live: Live | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """v0.4 · C18 · F34（任务 T50）— 启动等待 spinner（首事件前的活动反馈）。"""
        self._spinner.start()

    def feed(self, event: ThinkingDelta | TextDelta) -> None:
        """v0.4 · C18 · F34（任务 T50）— 喂入一个增量事件并更新显示。

        首事件 → 停 spinner（Live 互斥：spinner 自己的 Live 必须先关，
        thinking 打印或正文 Live 才能开）；thinking → dim 斜体即时打印；
        正文 → 缓冲 + 惰性开瞬态 Live（缓冲原位 mutate，Live 的
        get_renderable 闭包始终看到最新内容）。
        """
        if not self._first_event_seen:
            self._first_event_seen = True
            self._spinner.stop()

        if isinstance(event, ThinkingDelta):
            if self._console.is_terminal and self._live is not None:
                # 正文 Live 已开 → 交错 thinking：进缓冲、并入 Live 的
                # renderable（refresh 线程自动重绘），绝不 stop/reopen。无前置
                # thinking 时补 🤔 前缀，使本块在屏上仍带头部标识。
                if not self._thinking_started:
                    self._late_thinking.append(f"{_THINKING_PREFIX}\n")
                self._late_thinking.append(event.text)
            else:
                # 正文前的 thinking（或非 TTY）：直接打印、留底，行为不变。
                self._handle_thinking(event.text, first=not self._thinking_started)
            self._thinking_started = True

        elif isinstance(event, TextDelta):
            self._body_buffer.append(event.text)
            if self._console.is_terminal and self._live is None:
                if self._thinking_started:
                    # Thinking chunks print with end="" — close the open
                    # line so the Live frame does not start mid-line.
                    self._console.print()
                self._live = self._open_live()
            # No explicit update needed: the Live's get_renderable closes
            # over the buffer (mutated in place) and the refresh thread
            # repaints at refresh_per_second.

    def finish(self, *, interrupted: bool) -> str:
        """v0.4 · C18 · F34（任务 T50）— 收尾：停 spinner/Live、终稿落滚动区。

        interrupted 且正文非空时追加 dim「⎿ 已中断」标记（AC16：零正文
        中断不留痕迹）。返回累积的原始正文（Markdown 源，thinking 不计）。
        """
        self._stop_displays()
        body_text = "".join(self._body_buffer)
        self._print_final_body(body_text)
        if self._late_thinking:
            # 交错 thinking 只存在于已被擦除的瞬态 Live 里——重打一次落滚动区，
            # 否则它会随 Live 消失。dim 斜体，紧跟正文之后。
            self._console.print(Text("".join(self._late_thinking), style="dim italic"))
        if interrupted and body_text:
            # Marker only when partial text exists (AC16): the partial body
            # stays in scrollback above this line; zero-text interrupts go
            # straight back to the input box without a trace.
            self._console.print(Text("⎿ 已中断", style="dim"))
        return body_text

    # ------------------------------------------------------------------
    # Private: display plumbing
    # ------------------------------------------------------------------

    def _stop_displays(self) -> None:
        """停掉 spinner 与正文 Live（幂等）。

        v0.4 · C18 · F34（任务 T50）— 错误路径（异常向上传播）也走这里：
        只清屏显，不打终稿，与抽取前 render_stream 的 finally 行为一致。
        """
        self._spinner.stop()
        if self._live is not None:
            self._live.stop()  # transient=True erases the live region
            self._live = None

    def _handle_thinking(self, text: str, *, first: bool) -> None:
        """Print a thinking chunk directly to the console (no Live open).

        只在正文 Live 未开时调用（正文前的 thinking，或非 TTY）；正文 Live 已
        开后的交错 thinking 改走 :meth:`feed` 的 ``_late_thinking`` 分支，绝不
        在这里 stop/reopen Live——那个夹层会让瞬态擦除算错行数、留下重复正文。
        """
        if first:
            self._print_thinking_prefix()
        self._print_thinking_chunk(text)

    def _print_thinking_prefix(self) -> None:
        """Print the 🤔 思考中… header line."""
        self._console.print(
            Text(_THINKING_PREFIX, style="dim italic"),
        )

    def _print_thinking_chunk(self, text: str) -> None:
        """Stream a chunk of thinking text in dim italic plain text."""
        self._console.print(
            Text(text, style="dim italic"),
            end="",
        )

    def _open_live(self) -> Live:
        """Open the transient Live used to stream body Markdown (TTY only).

        v0.2 · C5 · F17（任务 T21）: the renderable is built lazily via
        ``get_renderable`` — Markdown of the (in-place mutated) buffer plus
        the spinner's elapsed line, so the timer keeps ticking while the
        body streams. The spinner's own Live is already stopped by then;
        ``render_line()`` here is a pure render call, not a second Live.
        """
        body_buffer = self._body_buffer
        late_thinking = self._late_thinking

        def _compose() -> Group:
            parts: list = [Markdown("".join(body_buffer))]
            if late_thinking:
                # 交错到达的 thinking，dim 斜体并入同一 Live（不另开 Live）。
                parts.append(Text("".join(late_thinking), style="dim italic"))
            parts.append(self._spinner.render_line())
            return Group(*parts)

        # Default vertical_overflow="ellipsis" truncates the live viewport for
        # very tall replies — accepted v0.1 tradeoff; the final scrollback
        # print is always complete (see spec/plan.md).
        live = Live(
            get_renderable=_compose,
            console=self._console,
            transient=True,
            refresh_per_second=10,
        )
        live.start()
        return live

    def _print_final_body(self, body_text: str) -> None:
        """Print the final rendered Markdown once for scrollback.

        Skipped for empty body (no TextDelta at all) — no empty block.
        The timer line is never part of this final print (定格后计时消失).
        """
        if not body_text:
            return
        self._console.print(Markdown(body_text))


class Renderer:
    """Renders a stream of StreamEvents to a Rich Console.

    Parameters
    ----------
    console:
        A Rich Console instance. Inject ``Console(record=True)`` in tests.
    clock:
        Zero-argument callable returning a float timestamp (seconds), used
        by the waiting/elapsed timer. Defaults to :func:`time.monotonic`.
        Inject a fake clock in tests.
    """

    def __init__(
        self,
        console: Console,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._console = console
        self._spinner = WaitingSpinner(console, clock=clock)

    @property
    def console(self) -> Console:
        """The underlying Rich Console instance."""
        return self._console

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def render_stream(
        self,
        events: Iterator[StreamEvent],
        *,
        interrupt: threading.Event | None = None,
    ) -> RenderResult:
        """Consume *events* and render them to the console.

        v0.2 · C5 · F17（任务 T21）— returns :class:`RenderResult` and
        provides 持续活动反馈: the waiting spinner runs until the first
        event, and its elapsed line stays visible under the streaming
        Markdown.

        v0.2 · C6 · F18（任务 T22）— 可中断消费: when *interrupt* is given,
        events are pulled through a :class:`_StreamPump` daemon thread so
        the main thread can notice the interrupt within ~100ms, even while
        blocked waiting for the first byte. On interrupt the partial body
        (if any) is finalized to scrollback followed by a dim ``⎿ 已中断``
        marker; zero-text interrupts print no marker (AC16: 首字前中断 →
        直接回输入框). ``KeyboardInterrupt`` during streaming is treated
        the same way in both modes (spec F18: 流中 Ctrl+C ≡ Esc) instead
        of crashing the REPL.

        The renderer owns the event loop: thinking deltas are printed
        immediately in dim italic; body deltas update a live Markdown view
        (TTY) or are buffered silently (non-TTY). After the stream ends the
        final rendered Markdown is printed once for scrollback (timer line
        excluded).

        v0.4 · C18 · F34（任务 T50）— 薄拉式包装：本方法只保留事件循环骨架
        （_StreamPump/drain、ToolCallEvent 收集、Done/raw_content 捕获、
        KeyboardInterrupt 与 interrupt 旗标判定、RenderResult 组装），全部
        显示委托给 :class:`StreamView`，像素与抽取前一致。

        Returns
        -------
        RenderResult
            ``.text`` is the accumulated raw body text (Markdown source,
            thinking excluded, "" when no body deltas were received);
            ``.interrupted`` is True when the stream was cut short by the
            interrupt Event or Ctrl+C.
        """
        tool_calls: list[ToolCallEvent] = []
        raw_content: list | None = None
        done_seen = False
        interrupted = False

        pump: _StreamPump | None = None
        if interrupt is None:
            source: Iterator[StreamEvent] = events
        else:
            pump = _StreamPump(events)
            source = pump.drain(interrupt)

        view = self.new_stream_view()
        view.start()
        try:
            try:
                for event in source:
                    if isinstance(event, (ThinkingDelta, TextDelta)):
                        view.feed(event)

                    elif isinstance(event, ToolCallEvent):
                        # v0.3 · C12 · F27（任务 T41）— collect silently: tool
                        # calls never print during streaming, never enter the
                        # body buffer, and never touch the Live frame. The
                        # surface display (render_tool_call) is driven by the
                        # REPL after the stream ends.
                        tool_calls.append(event)

                    elif isinstance(event, Done):
                        # Done.usage is deliberately dropped here — per-loop
                        # usage display is the agent loop's job (render_usage).
                        # v0.3 · C12 · F27（任务 T41）— pass raw_content through
                        # for faithful tool-result continuation.
                        raw_content = event.raw_content
                        done_seen = True
                        if pump is not None:
                            # We stop consuming here, so let the pump thread
                            # close the generator at its next event boundary
                            # instead of dangling on a dead stream.
                            pump.stop()
                        break
            except KeyboardInterrupt:
                # F18: 流中 Ctrl+C ≡ Esc — keep the partial buffer, never
                # let the exception crash the REPL (both direct and pump
                # mode: the pump forwards KeyboardInterrupt as an error
                # item, re-raised by drain on this thread).
                interrupted = True
                if pump is not None:
                    pump.stop()
        finally:
            # Error path (e.g. provider exception via the pump): stop the
            # spinner/Live without printing a final body — same as the
            # pre-extraction finally. On normal/interrupted exits finish()
            # below repeats this as an idempotent no-op.
            view._stop_displays()

        if interrupt is not None and interrupt.is_set() and not done_seen:
            # drain() returned early because the interrupt fired before the
            # stream completed.
            interrupted = True

        body_text = view.finish(interrupted=interrupted)
        return RenderResult(
            text=body_text,
            interrupted=interrupted,
            tool_calls=tuple(tool_calls),
            raw_content=raw_content,
        )

    def new_stream_view(self) -> StreamView:
        """v0.4 · C18 · F34（任务 T50）— 发放一个 push 式 StreamView。

        共享本 Renderer 的 console 与可重启 spinner（WaitingSpinner 每次
        start() 都开全新 Live，与 render_stream 既有用法一致）；agent loop
        每轮取一个新 view 驱动 ``start → feed* → finish``。
        """
        return StreamView(self._console, self._spinner)

    def render_usage(self, usage: Usage | None, rounds: int) -> None:
        """v0.4 · C18 · F34（任务 T50）— 单行 dim token 用量屏显。

        形如 ``tokens 输入 1234 · 输出 567 · 共 3 轮``；``usage`` 为 None
        时什么都不打印（provider 未上报用量 → 不留空行）。

        v0.5 · F40 · C26（任务 T65）— 有缓存命中时在用量行后追加缓存信息：
        ``· 缓存读 X``（cache_read_input_tokens > 0）和/或
        ``· 缓存写 Y``（cache_creation_input_tokens > 0）；
        两个字段均为 0 时输出与 v0.4 逐字一致。
        """
        if usage is None:
            return
        base = (
            f"tokens 输入 {usage.input_tokens} · 输出 {usage.output_tokens}"
            f" · 共 {rounds} 轮"
        )
        cache_parts: list[str] = []
        if usage.cache_read_input_tokens > 0:
            cache_parts.append(f"缓存读 {usage.cache_read_input_tokens}")
        if usage.cache_creation_input_tokens > 0:
            cache_parts.append(f"缓存写 {usage.cache_creation_input_tokens}")
        suffix = (" · " + " · ".join(cache_parts)) if cache_parts else ""
        self._console.print(Text(base + suffix, style="dim"))

    # ------------------------------------------------------------------
    # Public: tool call / result display
    # ------------------------------------------------------------------

    def render_tool_call(self, call: ToolCallEvent) -> None:
        """Print a tool call line, e.g. ``⏺ read_file(path="src/foo.py")``.

        v0.3 · C12 · F27（任务 T41）— displayed by the REPL once the stream
        ends, distinct from the Markdown body (bold ⏺ marker). Each argument
        value is ``repr``-ed and truncated to 60 chars via
        :meth:`_summarize_args`; ``arguments=None`` shows a ``(?)`` placeholder
        so an unparseable call still renders without crashing.
        """
        summary = self._summarize_args(call.arguments)
        line = Text()
        line.append("⏺ ", style="bold #C84B31")
        line.append(call.name, style="bold")
        line.append(summary, style="dim")
        self._console.print(line)

    def render_tool_result(self, outcome: object) -> None:
        """Print a dim, indented ``⎿`` result line for a tool outcome.

        v0.3 · C12 · F27（任务 T41）— duck-types the executor's ``ToolOutcome``
        (reads ``name`` / ``content`` / ``is_error`` / ``denied`` via getattr,
        no import of wentian.tools). Three visually distinct states, each with
        a state-colored ``⎿`` marker so a glance down the column reads as a
        column of green/red/yellow dots:

        - denied → yellow ``  ⎿ 已拒绝``
        - is_error → red marker + bold red ``失败`` + red ``· {首行}``
        - otherwise → green marker + green ``成功`` + dim ``· {首行截 80}``

        Only the first line of multi-line content is shown, truncated to ~80
        chars.
        """
        denied = bool(getattr(outcome, "denied", False))
        is_error = bool(getattr(outcome, "is_error", False))
        content = getattr(outcome, "content", "") or ""

        if denied:
            line = Text()
            line.append("  ⎿ ", style="yellow")
            line.append("已拒绝", style="yellow")
            self._console.print(line)
            return

        first_line = content.split("\n", 1)[0]
        snippet = self._truncate(first_line, 80)
        line = Text()
        if is_error:
            line.append("  ⎿ ", style="red")
            line.append("失败", style="bold red")
            line.append(f" · {snippet}", style="red")
        else:
            line.append("  ⎿ ", style="green")
            line.append("成功", style="green")
            line.append(f" · {snippet}", style="dim")
        self._console.print(line)

    @staticmethod
    def _summarize_args(arguments: dict | None) -> str:
        """Build a ``(k=repr(v), …)`` argument summary for a tool call line.

        v0.3 · C12 · F27（任务 T41）— each value is ``repr``-ed then truncated
        to 60 chars; ``None`` (unparseable arguments) yields the ``(?)``
        placeholder.
        """
        if arguments is None:
            return "(?)"
        parts = [
            f"{key}={Renderer._truncate(repr(value), 60)}"
            for key, value in arguments.items()
        ]
        return "(" + ", ".join(parts) + ")"

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        """Truncate *text* to *limit* chars, appending ``…`` when shortened."""
        if len(text) <= limit:
            return text
        return text[:limit] + "…"
