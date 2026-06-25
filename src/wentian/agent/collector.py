"""v0.4 · C14 · F31 (task T49)

RoundCollector: dual-channel collector for single-round streaming replies.

v0.4 splits the dual responsibilities of the v0.3 renderer's
"consume-and-accumulate" pattern: the collector at the agent layer
is responsible for accumulation (text / tool_calls / usage / raw_content),
while passing events that need real-time display up to the renderer unchanged
(F31 dual-channel).

Only imports stdlib and ``wentian.providers.base`` / ``wentian.agent.events`` —
never imports ``wentian.tools``, rich, or prompt_toolkit.
"""

from __future__ import annotations

from wentian.agent.events import RoundResult
from wentian.providers.base import (
    Done,
    StreamEvent,
    TextDelta,
    ThinkingDelta,
    ToolCallEvent,
    Usage,
)

__all__ = ["RoundCollector"]


class RoundCollector:
    """Dual-channel collector: feed() returns events that need real-time display
    unchanged (returns None for others), while fully accumulating the current
    round's reply for loop decision-making (F31)."""

    def __init__(self) -> None:
        self._text_parts: list[str] = []
        self._tool_calls: list[ToolCallEvent] = []
        self._usage: Usage | None = None
        self._raw_content: list[dict] | None = None
        self._done_seen: bool = False

    def feed(self, event: StreamEvent) -> ThinkingDelta | TextDelta | None:
        """Consumes one streaming event; returns the event if it needs real-time display, otherwise returns None."""
        if isinstance(event, TextDelta):
            # Append to text buffer and take the real-time display path.
            self._text_parts.append(event.text)
            return event
        if isinstance(event, ThinkingDelta):
            # Pass through for display, not counted in text.
            return event
        if isinstance(event, ToolCallEvent):
            # Silently collect in order (same rule as v0.3: tool calls are not rendered in the stream).
            self._tool_calls.append(event)
            return None
        if isinstance(event, Done):
            self._usage = event.usage
            self._raw_content = event.raw_content
            self._done_seen = True
            return None
        return None  # pragma: no cover - StreamEvent union already covers all types

    def result(self, *, interrupted: bool) -> RoundResult:
        """Summarize the current round's result.

        RoundResult has no interrupted field; done_seen=False indicates
        interruption or abnormal early termination. When interrupted=True,
        done_seen is always False (interruption takes priority).
        """
        return RoundResult(
            text="".join(self._text_parts),
            tool_calls=tuple(self._tool_calls),
            raw_content=self._raw_content,
            usage=self._usage,
            done_seen=self._done_seen and not interrupted,
        )
