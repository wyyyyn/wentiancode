"""v0.4 · C14 · F30 (task T47)

Agent Loop event contract: StopReason, AgentEvent union, and RoundResult.

This module is the sole event contract between the agent layer and the upper
layer (REPL/render); other v0.4 agent modules (stream bridging, round
execution, loop scheduling) depend only on the types defined here.

Only imports stdlib and ``wentian.providers.base`` — the agent layer never
imports ``wentian.tools`` (tool results are passed via duck typing through
``ToolResultReady.outcome``).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from wentian.providers.base import TextDelta, ThinkingDelta, ToolCallEvent, Usage

__all__ = [
    "StopReason",
    "RoundStart",
    "UsageUpdate",
    "StreamEnd",
    "ToolCallStarted",
    "ToolResultReady",
    "RoundEnd",
    "AgentDone",
    "AgentEvent",
    "RoundResult",
]


# ---------------------------------------------------------------------------
# Stop reason
# ---------------------------------------------------------------------------


class StopReason(enum.Enum):
    """Agent loop termination reason (AgentDone.stop_reason)."""

    COMPLETED = "completed"
    MAX_ROUNDS = "max_rounds"
    USER_CANCELLED = "user_cancelled"
    UNKNOWN_TOOL_LOOP = "unknown_tool_loop"
    STREAM_ERROR = "stream_error"


# ---------------------------------------------------------------------------
# Agent events
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RoundStart:
    """Start of a round; ``index`` is the 1-based round number."""

    index: int


@dataclass(frozen=True, slots=True)
class UsageUpdate:
    """Token usage update: current round usage and cumulative total across rounds."""

    round_usage: Usage
    total: Usage


@dataclass(frozen=True, slots=True)
class StreamEnd:
    """Model stream for this round has ended; ``interrupted`` indicates whether it was interrupted by the user."""

    index: int
    text: str
    interrupted: bool


@dataclass(frozen=True, slots=True)
class ToolCallStarted:
    """A tool call requested by the model has started executing."""

    call: ToolCallEvent


@dataclass(frozen=True, slots=True)
class ToolResultReady:
    """A tool call has finished executing.

    ``outcome`` is a duck-typed ToolOutcome-shaped object — the agent layer
    never imports ``wentian.tools``, so it is declared as ``object`` here.
    """

    outcome: object


@dataclass(frozen=True, slots=True)
class RoundEnd:
    """End of a round; ``tool_results`` is the number of tool results produced in this round."""

    index: int
    tool_results: int


@dataclass(frozen=True, slots=True)
class AgentDone:
    """The agent loop has fully ended; always the last event in the event stream."""

    stop_reason: StopReason
    text: str
    rounds: int
    usage: Usage | None
    error: str | None = None


# ThinkingDelta / TextDelta reuse the definitions from providers.base directly; not redefined here.
AgentEvent = (
    RoundStart
    | ThinkingDelta
    | TextDelta
    | UsageUpdate
    | StreamEnd
    | ToolCallStarted
    | ToolResultReady
    | RoundEnd
    | AgentDone
)


# ---------------------------------------------------------------------------
# Single-round result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoundResult:
    """Summary result of a single streaming round (passed between rounds by the loop scheduler).

    ``done_seen`` indicates whether the stream for this round received a Done
    event (False means the stream was interrupted or ended early due to an
    exception).
    """

    text: str
    tool_calls: tuple[ToolCallEvent, ...]
    raw_content: list | None
    usage: Usage | None
    done_seen: bool
