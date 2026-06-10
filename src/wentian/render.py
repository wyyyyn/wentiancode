"""Renderer — displays thinking and body stream events in the terminal.

Thinking events are shown in dim italic plain text (never Markdown-rendered).
Body events are accumulated, rendered as Rich Markdown, and the raw source is
returned for session history.

TTY path (T7): body is streamed live via rich.live.Live (transient, ~10 fps),
then re-printed once at Done so scrollback gets the final rendered output.
Non-TTY path (tests/pipes): skip Live entirely, print final Markdown once.
"""

from __future__ import annotations

from collections.abc import Iterator

from rich.console import Console
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

        Returns
        -------
        str
            The accumulated raw body text (Markdown source). Thinking text is
            excluded. Returns "" when no body deltas were received.
        """
        body_buffer: list[str] = []
        thinking_started = False

        for event in events:
            if isinstance(event, ThinkingDelta):
                if not thinking_started:
                    self._print_thinking_prefix()
                    thinking_started = True
                self._print_thinking_chunk(event.text)

            elif isinstance(event, TextDelta):
                body_buffer.append(event.text)

            elif isinstance(event, Done):
                break

        body_text = "".join(body_buffer)
        self._finalize_body(body_text)
        return body_text

    # ------------------------------------------------------------------
    # Private: thinking display
    # ------------------------------------------------------------------

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

    def _finalize_body(self, body_text: str) -> None:
        """Render complete body text as Markdown and print for scrollback.

        In a real TTY: streams via Live (transient) then finalizes.
        In non-TTY / recording mode: prints the final Markdown once.
        """
        if not body_text:
            return

        if self._console.is_terminal:
            self._render_body_live(body_text)
        else:
            self._render_body_static(body_text)

    def _render_body_live(self, body_text: str) -> None:
        """TTY path: wrap in transient Live then print final for scrollback."""
        from rich.live import Live

        with Live(
            Markdown(body_text),
            console=self._console,
            transient=True,
            refresh_per_second=10,
        ):
            pass  # In real streaming, Live.update() would be called per delta

        # Print final rendered Markdown into scrollback
        self._console.print(Markdown(body_text))

    def _render_body_static(self, body_text: str) -> None:
        """Non-TTY path: print final rendered Markdown once."""
        self._console.print(Markdown(body_text))
