"""Renderer — displays thinking and body stream events in the terminal.

Thinking events are shown in dim italic plain text (never Markdown-rendered).
Body events are accumulated; the raw text is returned for session history.

T6 implementation: thinking vs body separation.
"""

from __future__ import annotations

from collections.abc import Iterator

from rich.console import Console
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
        """Print body text as plain text (T6 baseline; T7 upgrades to Markdown)."""
        if not body_text:
            return
        self._console.print(body_text)
