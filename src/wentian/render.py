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

    def render_stream(self, events: Iterator[StreamEvent]) -> RenderResult:
        """Consume *events* and render them to the console.

        v0.2 · C5 · F17（任务 T21）— returns :class:`RenderResult` and
        provides 持续活动反馈: the waiting spinner runs until the first
        event, and its elapsed line stays visible under the streaming
        Markdown.

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
            ``.interrupted`` is always False until T23.
        """
        body_buffer: list[str] = []
        thinking_started = False
        first_event_seen = False
        live: Live | None = None

        self._spinner.start()
        try:
            for event in events:
                if not first_event_seen:
                    # Live mutual exclusion: the spinner's own Live must be
                    # closed before a thinking print or the body Live opens.
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
                    # No explicit update needed: the Live's get_renderable
                    # closes over body_buffer (mutated in place) and the
                    # refresh thread repaints at refresh_per_second.

                elif isinstance(event, Done):
                    # Done.usage is deliberately dropped — usage display is
                    # out of v0.1 scope.
                    break
        finally:
            self._spinner.stop()
            if live is not None:
                live.stop()  # transient=True erases the live region

        body_text = "".join(body_buffer)
        self._print_final_body(body_text)
        return RenderResult(text=body_text)

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
