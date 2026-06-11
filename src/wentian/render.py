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

from wentian.providers.base import Done, StreamEvent, TextDelta, ThinkingDelta
from wentian.ui.spinner import WaitingSpinner

__all__ = ["RenderResult", "Renderer"]

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
        True when the stream was cut short by the user (Esc 中断 — wired up
        in T23). Always False for now.
    """

    text: str
    interrupted: bool = False


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

        Returns
        -------
        RenderResult
            ``.text`` is the accumulated raw body text (Markdown source,
            thinking excluded, "" when no body deltas were received);
            ``.interrupted`` is True when the stream was cut short by the
            interrupt Event or Ctrl+C.
        """
        body_buffer: list[str] = []
        thinking_started = False
        first_event_seen = False
        done_seen = False
        interrupted = False
        live: Live | None = None

        pump: _StreamPump | None = None
        if interrupt is None:
            source: Iterator[StreamEvent] = events
        else:
            pump = _StreamPump(events)
            source = pump.drain(interrupt)

        self._spinner.start()
        try:
            try:
                for event in source:
                    if not first_event_seen:
                        # Live mutual exclusion: the spinner's own Live must
                        # be closed before a thinking print or the body Live
                        # opens.
                        first_event_seen = True
                        self._spinner.stop()

                    if isinstance(event, ThinkingDelta):
                        live = self._handle_thinking(
                            event.text,
                            first=not thinking_started,
                            live=live,
                            body_buffer=body_buffer,
                        )
                        thinking_started = True

                    elif isinstance(event, TextDelta):
                        body_buffer.append(event.text)
                        if self._console.is_terminal and live is None:
                            if thinking_started:
                                # Thinking chunks print with end="" — close
                                # the open line so the Live frame does not
                                # start mid-line.
                                self._console.print()
                            live = self._open_live(body_buffer)
                        # No explicit update needed: the Live's
                        # get_renderable closes over body_buffer (mutated in
                        # place) and the refresh thread repaints at
                        # refresh_per_second.

                    elif isinstance(event, Done):
                        # Done.usage is deliberately dropped — usage display
                        # is out of v0.1 scope.
                        done_seen = True
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
            self._spinner.stop()
            if live is not None:
                live.stop()  # transient=True erases the live region

        if interrupt is not None and interrupt.is_set() and not done_seen:
            # drain() returned early because the interrupt fired before the
            # stream completed.
            interrupted = True

        body_text = "".join(body_buffer)
        self._print_final_body(body_text)
        if interrupted and body_text:
            # Marker only when partial text exists (AC16): the partial body
            # stays in scrollback above this line; zero-text interrupts go
            # straight back to the input box without a trace.
            self._console.print(Text("⎿ 已中断", style="dim"))
        return RenderResult(text=body_text, interrupted=interrupted)

    # ------------------------------------------------------------------
    # Private: thinking display
    # ------------------------------------------------------------------

    def _handle_thinking(
        self,
        text: str,
        *,
        first: bool,
        live: Live | None,
        body_buffer: list[str],
    ) -> Live | None:
        """Print a thinking chunk, suspending the Live region if it is open.

        Printing through the console while a Live frame is active collides
        with the live region on the same line, so when *live* is open we
        stop it (transient erases the frame), print the thinking text, then
        re-open a fresh Live seeded with the current body buffer.

        Returns the (possibly new) Live handle.
        """
        if live is not None:
            live.stop()

        if first:
            self._print_thinking_prefix()
        self._print_thinking_chunk(text)

        if live is not None:
            # The chunk above printed with end="" — close the line so the
            # reopened Live frame does not start mid-line.
            self._console.print()
            live = self._open_live(body_buffer)
        return live

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

    # ------------------------------------------------------------------
    # Private: body display
    # ------------------------------------------------------------------

    def _open_live(self, body_buffer: list[str]) -> Live:
        """Open the transient Live used to stream body Markdown (TTY only).

        v0.2 · C5 · F17（任务 T21）: the renderable is built lazily via
        ``get_renderable`` — Markdown of the (in-place mutated) buffer plus
        the spinner's elapsed line, so the timer keeps ticking while the
        body streams. The spinner's own Live is already stopped by then;
        ``render_line()`` here is a pure render call, not a second Live.
        """

        def _compose() -> Group:
            return Group(
                Markdown("".join(body_buffer)),
                self._spinner.render_line(),
            )

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
