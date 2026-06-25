"""Renderer — displays thinking and body stream events in the terminal.

v0.14 · F103/F104/F105: thinking events are NEVER shown as text — the
chain-of-thought is hidden behind a dedicated braille "🧠 Thinking" animation
(:class:`~wentian.ui.thinking_animation.ThinkingAnimation`), and when the
thinking phase ends a persistent ``💭 Thinking Ns`` breadcrumb is left in
scrollback. Body events are accumulated, rendered through the custom Markdown
theme (:func:`~wentian.ui.markdown_theme.render_markdown`), and the raw source
is returned for session history.

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

v0.4 · C18 · F34 (task T50): the per-stream display state machine lives in
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
from rich.text import Text

from wentian.providers.base import (
    Done,
    StreamEvent,
    TextDelta,
    ThinkingDelta,
    ToolCallEvent,
    Usage,
)
from wentian.ui.markdown_theme import render_markdown
from wentian.ui.spinner import WaitingSpinner
from wentian.ui.thinking_animation import ThinkingAnimation

__all__ = ["RenderResult", "Renderer", "StreamView"]


@dataclass(frozen=True)
class RenderResult:
    """v0.2 · C5 · F17 (task T21) — Structured return value of render_stream.

    Attributes
    ----------
    text:
        The accumulated raw body text (Markdown source). Thinking text is
        excluded. ``""`` when no body deltas were received.
    interrupted:
        True when the stream was cut short by the user — via the
        ``interrupt`` Event or Ctrl+C (v0.2 · C6 · F18, task T22). REPL
        semantics land in T23, the Esc listener in T24.
    tool_calls:
        v0.3 · C12 · F27 (task T41) — the ``ToolCallEvent`` instances the
        model requested, in arrival order; ``()`` when none. Collected even
        on interrupt — the discard decision lives in the REPL layer.
    raw_content:
        v0.3 · C12 · F27 (task T41) — the provider-native assistant content
        blocks from the final ``Done.raw_content`` (passed through verbatim
        for faithful tool-result continuation); ``None`` when absent.
    """

    text: str
    interrupted: bool = False
    tool_calls: tuple = ()
    raw_content: list | None = None


class _StreamPump:
    """v0.2 · C6 · F18 (task T22) — Interruptible stream consumer pump.

    The main thread originally blocked directly on the provider's network
    read, so it could not react to an interrupt while waiting for the next
    event. The pump moves that blocking ``next()`` onto a daemon thread and
    feeds events through a :class:`queue.Queue`; the main thread polls the
    queue with a short timeout via :meth:`drain`, checking the interrupt
    Event between polls — both while waiting for first content and during body streaming stay
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
    """v0.4 · C18 · F34 (task T50) — Push-mode per-round display state machine.

    Spinner until first event, v0.14 thinking animation (braille 🧠, chain-of-thought not shown) + ending 💭
    breadcrumb, transient Live with custom-themed Markdown body, final text committed to scrollback. Extracted verbatim from
    ``Renderer.render_stream``'s pull-mode event loop; the v0.4 agent loop
    drives display per round via ``start → feed* → finish``, while render_stream still reuses
    this class as a thin wrapper.

    Under non-TTY (tests/pipes) Live and spinner output are skipped entirely; only the final
    Markdown is printed once at finish — consistent with render_stream's existing behavior.

    Parameters
    ----------
    console:
        Shared Rich Console.
    spinner:
        Restartable WaitingSpinner (each start() opens a new Live).
    thinking_anim:
        Restartable ThinkingAnimation (v0.14 · F103/F104, braille animation during thinking phase +
        ending 💭 breadcrumb, replacing the old chain-of-thought text display).
    """

    def __init__(
        self,
        console: Console,
        spinner: WaitingSpinner,
        thinking_anim: ThinkingAnimation,
    ) -> None:
        self._console = console
        self._spinner = spinner
        # v0.14 · F103/F104 — Dedicated thinking animation (braille + 🧠 Thinking), replacing the original
        # per-character chain-of-thought text display from v0.1; restartable, each round's start() opens a new Live.
        self._thinking_anim = thinking_anim
        self._body_buffer: list[str] = []
        # Whether the thinking animation was started before the body (determines whether a 💭 breadcrumb is left at the end).
        self._thinking_anim_started = False
        self._thinking_phase_ended = False
        self._first_event_seen = False
        self._live: Live | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """v0.4 · C18 · F34 (task T50) — Start the waiting spinner (activity feedback before the first event)."""
        self._spinner.start()

    def feed(self, event: ThinkingDelta | TextDelta) -> None:
        """v0.4 · C18 · F34 (task T50) — Feed one incremental event and update the display.

        First event → stop spinner (Live mutual exclusion: the spinner's own Live must be closed first,
        before thinking print or body Live can open); thinking → dim italic immediate print;
        body → buffer + lazily open transient Live (buffer mutated in place, Live's
        get_renderable closure always sees the latest content).
        """
        if not self._first_event_seen:
            self._first_event_seen = True
            self._spinner.stop()

        if isinstance(event, ThinkingDelta):
            # v0.14 · F103/F104 — thinking text is never shown. Only when body Live has not yet opened
            # (thinking phase before body) plays the dedicated braille animation; thinking that arrives after body Live has opened
            # (interleaved thinking, R1 reasoning interleaved with content) is consumed but not displayed,
            # no breadcrumb — neither leaking chain-of-thought nor producing ghost artifacts from the old stop/reopen interstitial layer.
            if self._live is None and not self._thinking_anim_started:
                self._thinking_anim_started = True
                self._thinking_anim.start()

        elif isinstance(event, TextDelta):
            self._body_buffer.append(event.text)
            if self._console.is_terminal and self._live is None:
                # First body chunk (TTY): first close out the thinking phase (stop animation + leave 💭 breadcrumb),
                # then open the body Live, so the breadcrumb lands above the body.
                self._end_thinking_phase()
                self._live = self._open_live()
            # No explicit update needed: the Live's get_renderable closes
            # over the buffer (mutated in place) and the refresh thread
            # repaints at refresh_per_second.

    def finish(self, *, interrupted: bool) -> str:
        """v0.4 · C18 · F34 (task T50) — Wrap up: stop spinner/Live, commit final body to scrollback.

        When interrupted and body is non-empty, append a dim "⎿ Interrupted" marker (AC16: zero-body
        interrupts leave no trace). Returns the accumulated raw body text (Markdown source, thinking excluded).
        """
        # Close out the thinking phase (covers the "think-only, no body" TTY wrap-up: stop animation + leave breadcrumb);
        # idempotent no-op when body has already arrived (already closed out at the first body chunk in feed).
        self._end_thinking_phase()
        self._stop_displays()
        body_text = "".join(self._body_buffer)
        self._print_final_body(body_text)
        if interrupted and body_text:
            # Marker only when partial text exists (AC16): the partial body
            # stays in scrollback above this line; zero-text interrupts go
            # straight back to the input box without a trace.
            self._console.print(Text("⎿ Interrupted", style="dim"))
        return body_text

    # ------------------------------------------------------------------
    # Private: display plumbing
    # ------------------------------------------------------------------

    def _stop_displays(self) -> None:
        """Stop the spinner and body Live (idempotent).

        v0.4 · C18 · F34 (task T50) — The error path (exception propagating upward) also goes here:
        only clears display, does not print final body; consistent with render_stream's pre-extraction finally behavior.
        """
        self._spinner.stop()
        self._thinking_anim.stop()
        if self._live is not None:
            self._live.stop()  # transient=True erases the live region
            self._live = None

    def _end_thinking_phase(self) -> None:
        """v0.14 · F104 — Close out the thinking phase: stop animation, leave a 💭 breadcrumb on TTY.

        Idempotent: called once when the first body chunk arrives (breadcrumb lands above the body); calling again at finish is a
        no-op (covers the "think-only, no body" wrap-up). Only leaves a trace when the thinking animation was indeed started before the body
        (pure body stream, or thinking that arrives only after body Live has opened → no breadcrumb);
        non-TTY does not print a breadcrumb (final Markdown only, N57).
        """
        if not self._thinking_anim_started or self._thinking_phase_ended:
            return
        self._thinking_phase_ended = True
        self._thinking_anim.stop()
        if self._console.is_terminal:
            self._console.print(self._thinking_anim.render_breadcrumb())

    def _open_live(self) -> Live:
        """Open the transient Live used to stream body Markdown (TTY only).

        v0.2 · C5 · F17 (task T21): the renderable is built lazily via
        ``get_renderable`` — Markdown of the (in-place mutated) buffer plus
        the spinner's elapsed line, so the timer keeps ticking while the
        body streams. The spinner's own Live is already stopped by then;
        ``render_line()`` here is a pure render call, not a second Live.
        """
        body_buffer = self._body_buffer

        def _compose() -> Group:
            # v0.14 · F105 — Body uses the custom Markdown theme; the timer line stays at the bottom while the body streams.
            return Group(
                render_markdown("".join(body_buffer)),
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
        The timer line is never part of this final print (timer disappears after the final frame).
        """
        if not body_text:
            return
        self._console.print(render_markdown(body_text))


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
        self._thinking_anim = ThinkingAnimation(console, clock=clock)

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

        v0.2 · C5 · F17 (task T21) — returns :class:`RenderResult` and
        provides continuous activity feedback: the waiting spinner runs until the first
        event, and its elapsed line stays visible under the streaming
        Markdown.

        v0.2 · C6 · F18 (task T22) — Interruptible consumption: when *interrupt* is given,
        events are pulled through a :class:`_StreamPump` daemon thread so
        the main thread can notice the interrupt within ~100ms, even while
        blocked waiting for the first byte. On interrupt the partial body
        (if any) is finalized to scrollback followed by a dim ``⎿ Interrupted``
        marker; zero-text interrupts print no marker (AC16: interrupt before first character →
        goes straight back to the input box). ``KeyboardInterrupt`` during streaming is treated
        the same way in both modes (spec F18: Ctrl+C during streaming ≡ Esc) instead
        of crashing the REPL.

        The renderer owns the event loop: thinking deltas are printed
        immediately in dim italic; body deltas update a live Markdown view
        (TTY) or are buffered silently (non-TTY). After the stream ends the
        final rendered Markdown is printed once for scrollback (timer line
        excluded).

        v0.4 · C18 · F34 (task T50) — Thin pull-mode wrapper: this method retains only the event loop skeleton
        (_StreamPump/drain, ToolCallEvent collection, Done/raw_content capture,
        KeyboardInterrupt and interrupt flag determination, RenderResult assembly), delegating all
        display to :class:`StreamView`, pixel-identical to pre-extraction behavior.

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
                        # v0.3 · C12 · F27 (task T41) — collect silently: tool
                        # calls never print during streaming, never enter the
                        # body buffer, and never touch the Live frame. The
                        # surface display (render_tool_call) is driven by the
                        # REPL after the stream ends.
                        tool_calls.append(event)

                    elif isinstance(event, Done):
                        # Done.usage is deliberately dropped here — per-loop
                        # usage display is the agent loop's job (render_usage).
                        # v0.3 · C12 · F27 (task T41) — pass raw_content through
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
                # F18: Ctrl+C during streaming ≡ Esc — keep the partial buffer, never
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
        """v0.4 · C18 · F34 (task T50) — Issue a push-mode StreamView.

        Shares this Renderer's console and restartable spinner (WaitingSpinner each
        start() opens a fresh Live, consistent with render_stream's existing usage); the agent loop
        takes a new view each round to drive ``start → feed* → finish``.
        """
        return StreamView(self._console, self._spinner, self._thinking_anim)

    def render_usage(self, usage: Usage | None, rounds: int) -> None:
        """v0.4 · C18 · F34 (task T50) — Single-line dim token usage display.

        Format: ``tokens in 1234 · out 567 · 3 rounds total``; when ``usage`` is None
        nothing is printed (provider did not report usage → no blank line left).

        v0.5 · F40 · C26 (task T65) — When there is a cache hit, append cache info after the usage line:
        ``· cache read X`` (cache_read_input_tokens > 0) and/or
        ``· cache write Y`` (cache_creation_input_tokens > 0);
        when both fields are 0, output is verbatim identical to v0.4.
        """
        if usage is None:
            return
        base = (
            f"tokens in {usage.input_tokens} · out {usage.output_tokens}"
            f" · {rounds} rounds total"
        )
        cache_parts: list[str] = []
        if usage.cache_read_input_tokens > 0:
            cache_parts.append(f"cache read {usage.cache_read_input_tokens}")
        if usage.cache_creation_input_tokens > 0:
            cache_parts.append(f"cache write {usage.cache_creation_input_tokens}")
        suffix = (" · " + " · ".join(cache_parts)) if cache_parts else ""
        self._console.print(Text(base + suffix, style="dim"))

    # ------------------------------------------------------------------
    # Public: tool call / result display
    # ------------------------------------------------------------------

    def render_tool_call(self, call: ToolCallEvent) -> None:
        """Print a tool call line, e.g. ``⏺ read_file(path="src/foo.py")``.

        v0.3 · C12 · F27 (task T41) — displayed by the REPL once the stream
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

        v0.3 · C12 · F27 (task T41) — duck-types the executor's ``ToolOutcome``
        (reads ``name`` / ``content`` / ``is_error`` / ``denied`` via getattr,
        no import of wentian.tools). Three visually distinct states, each with
        a state-colored ``⎿`` marker so a glance down the column reads as a
        column of green/red/yellow dots:

        - denied → yellow ``  ⎿ Denied``
        - is_error → red marker + bold red ``Failed`` + red ``· {first line}``
        - otherwise → green marker + green ``OK`` + dim ``· {first line truncated to 80}``

        Only the first line of multi-line content is shown, truncated to ~80
        chars.
        """
        denied = bool(getattr(outcome, "denied", False))
        is_error = bool(getattr(outcome, "is_error", False))
        content = getattr(outcome, "content", "") or ""

        if denied:
            line = Text()
            line.append("  ⎿ ", style="yellow")
            line.append("Denied", style="yellow")
            self._console.print(line)
            return

        first_line = content.split("\n", 1)[0]
        snippet = self._truncate(first_line, 80)
        line = Text()
        if is_error:
            line.append("  ⎿ ", style="red")
            line.append("Failed", style="bold red")
            line.append(f" · {snippet}", style="red")
        else:
            line.append("  ⎿ ", style="green")
            line.append("OK", style="green")
            line.append(f" · {snippet}", style="dim")
        self._console.print(line)

    @staticmethod
    def _summarize_args(arguments: dict | None) -> str:
        """Build a ``(k=repr(v), …)`` argument summary for a tool call line.

        v0.3 · C12 · F27 (task T41) — each value is ``repr``-ed then truncated
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
