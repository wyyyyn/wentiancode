"""v0.11 · C100 · F84 (task T126) — Skill system data model (leaf module).

This module is a pure leaf of the Skill system: depends only on stdlib
(``dataclasses`` / ``enum`` / ``typing``), zero business imports — does not
import rich / prompt_toolkit / any backend SDK, and does not reverse-depend on
wentian.agent / wentian.repl / wentian.providers / wentian.commands.

Defines two symbol types:

- ``SkillMode``: Skill runtime mode (shared current conversation / isolated sub-conversation).
- ``Skill``: immutable data model for a single Skill (frozen dataclass).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SkillMode(Enum):
    """Skill runtime mode."""

    SHARED = "shared"
    """Share current conversation: activate into set, inject body each turn, narrow whitelist, results stay in main history (default)."""

    ISOLATED = "isolated"
    """Isolated sub-conversation: worker thread runs nested AgentLoop, last assistant body flows back as tool result."""


@dataclass(frozen=True)
class Skill:
    """Immutable data model for a single Skill."""

    name: str
    """Unique identifier, no slash, lowercase (source of slash command name)."""

    description: str
    """One sentence, shown in the 'Available Skills' menu + /help."""

    body: str
    """Markdown body (SOP; contains unreplaced $ARGUMENTS/$1 placeholders)."""

    mode: SkillMode = SkillMode.SHARED
    """Runtime mode, defaults to SHARED."""

    allowed_tools: tuple[str, ...] | None = None
    """None => do not narrow; empty tuple is also treated as no narrowing (handled by upper-layer rules)."""

    history: int = 0
    """Isolated only: how many main history entries to carry into the sub-conversation."""

    model: str | None = None
    """Model override (defaults to reusing current)."""

    source: str = "builtin"
    """Source layer: "project" / "user" / "builtin"."""
