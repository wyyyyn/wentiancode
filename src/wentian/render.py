"""Renderer — displays thinking and body stream events in the terminal.

Thinking events are shown in dim italic plain text (never Markdown-rendered).
Body events are accumulated, rendered as Rich Markdown, and the raw source is
returned for session history.

TTY path (T7): a transient ``rich.live.Live`` is opened at the first TextDelta
and updated per delta with ``Markdown(buffer)`` — Live's internal refresh
thread caps repaints at ~10 fps, so no manual throttling is needed. After the
stream ends the Live is closed (transient erases it) and the final
``Markdown(full)`` is printed once so scrollback keeps the rendered output.

Non-TTY path (tests / pipes): Live is skipped entirely; the final rendered
Markdown is printed once at the end.
"""

from __future__ import annotations

from collections.abc import Iterator

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text

from wentian.providers.base import Done, StreamEvent, TextDelta, ThinkingDelta

__all__ = ["Renderer"]

_THINKING_PREFIX = "🤔 思考中…"


class Renderer:
    """Renders a stream of StreamEvents to a Rich Console.

    Parameters
    ----------
    console:
        A Rich Console instance. Inject ``Console(record=True)`` in tests.
    """

    def __init__(self, console: Console) -> None:
        self._console = console

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def render_stream(self, events: Iterator[StreamEvent]) -> str:
        """Consume *events* and render them to the console.

        The renderer owns the event loop: thinking deltas are printed
        immediately in dim italic; body deltas update a live Markdown view
        (TTY) or are buffered silently (non-TTY). After the stream ends the
        final rendered Markdown is printed once for scrollback.

        Returns
        -------
        str
            The accumulated raw body text (Markdown source). Thinking text is
            excluded. Returns "" when no body deltas were received.
        """
        body_buffer: list[str] = []
        thinking_started = False
        live: Live | None = None

        try:
            for event in events:
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
                    if self._console.is_terminal:
                        if live is None:
                            if thinking_started:
                                # Thinking chunks print with end="" — close
                                # the open line so the Live frame does not
                                # start mid-line.
                                self._console.print()
                            live = self._open_live()
                        live.update(Markdown("".join(body_buffer)))

                elif isinstance(event, Done):
                    # Done.usage is deliberately dropped — usage display is
                    # out of v0.1 scope.
                    break
        finally:
            if live is not None:
                live.stop()  # transient=True erases the live region

        body_text = "".join(body_buffer)
        self._print_final_body(body_text)
        return body_text

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
            live = self._open_live()
            live.update(Markdown("".join(body_buffer)))
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

    def _open_live(self) -> Live:
        """Open the transient Live used to stream body Markdown (TTY only)."""
        # Default vertical_overflow="ellipsis" truncates the live viewport for
        # very tall replies — accepted v0.1 tradeoff; the final scrollback
        # print is always complete (see spec/plan.md).
        live = Live(
            console=self._console,
            transient=True,
            refresh_per_second=10,
        )
        live.start()
        return live

    def _print_final_body(self, body_text: str) -> None:
        """Print the final rendered Markdown once for scrollback.

        Skipped for empty body (no TextDelta at all) — no empty block.
        """
        if not body_text:
            return
        self._console.print(Markdown(body_text))
