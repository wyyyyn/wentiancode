"""v0.12 · C94 · F77/F78/F81/F82 (task T118) — Hook rule model.

Pure leaf module: HookEvent enum(10) + INTERCEPT_EVENTS + Match/Clause/Condition +
four Action dataclasses + Action union alias + HookRule.

Layering rule (N41): stdlib only (dataclasses / enum / typing); zero runtime business imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Union

__all__ = [
    "HookEvent",
    "INTERCEPT_EVENTS",
    "Match",
    "Clause",
    "Condition",
    "ShellAction",
    "PromptAction",
    "HttpAction",
    "SubAgentAction",
    "Action",
    "HookRule",
]


# ---------------------------------------------------------------------------
# Event enum — ten lifecycle events
# ---------------------------------------------------------------------------


class HookEvent(Enum):
    SESSION_START = "SessionStart"  # session-level
    SESSION_END = "SessionEnd"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"  # message-level
    STOP = "Stop"
    ROUND_START = "RoundStart"  # round-level
    ROUND_END = "RoundEnd"
    PRE_TOOL_USE = "PreToolUse"  # tool-level (sole interceptable event)
    POST_TOOL_USE = "PostToolUse"
    PRE_COMPACT = "PreCompact"  # system-level
    NOTIFICATION = "Notification"


# Interceptable event set: background=True is forbidden (F82)
INTERCEPT_EVENTS: frozenset[HookEvent] = frozenset({HookEvent.PRE_TOOL_USE})


# ---------------------------------------------------------------------------
# Condition model
# ---------------------------------------------------------------------------


class Match(Enum):
    ALL = "all"  # all clauses must match
    ANY = "any"  # any single clause match is sufficient


@dataclass(frozen=True)
class Clause:
    """Single condition clause: evaluates match_one on ctx[field] against pattern."""

    field: str
    pattern: str


@dataclass(frozen=True)
class Condition:
    """Condition set; when clauses is empty always evaluates to true (unconditional trigger)."""

    match: Match = Match.ALL
    clauses: tuple[Clause, ...] = ()


# ---------------------------------------------------------------------------
# Action dataclasses — four execution action types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ShellAction:
    """Execute a shell command; context JSON is injected via stdin."""

    command: str
    timeout: int | None = None


@dataclass(frozen=True)
class PromptAction:
    """Inject system-reminder text into the next request; supports {field} placeholders."""

    text: str


@dataclass(frozen=True)
class HttpAction:
    """Send an HTTP request to url with context JSON as the body."""

    url: str
    method: str = "POST"
    timeout: int | None = None


@dataclass(frozen=True)
class SubAgentAction:
    """Sub-agent action (placeholder in this version, does not execute an actual sub-agent)."""

    prompt: str = ""


# Action union alias (usable in both type annotations and at runtime)
Action = Union[ShellAction, PromptAction, HttpAction, SubAgentAction]


# ---------------------------------------------------------------------------
# HookRule — three rule components + execution control
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HookRule:
    """A single hook rule: event + action + optional condition + execution control."""

    event: HookEvent
    action: Action  # type: ignore[type-arg]
    condition: Condition | None = None  # None → unconditional (always triggers)
    once: bool = False  # True → triggers only once per session
    background: bool = False  # True → async daemon thread; disabled on intercept events
