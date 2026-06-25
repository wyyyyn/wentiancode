"""v0.10 · C85 · F70/F72 (task T108) — command spec: CommandType enum + CommandSpec dataclass.

Layering rule: pure leaf module, stdlib only (enum / dataclasses / collections.abc).
The CommandContext parameter type for handler uses TYPE_CHECKING forward reference, to avoid
circular dependency with context.py.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wentian.commands.context import CommandContext

__all__ = ["CommandType", "CommandSpec"]


class CommandType(Enum):
    """Command execution semantic classification."""

    LOCAL = "local"
    """Pure local: returns immediately after execution, does not extend conversation history."""

    UI_STATE = "ui_state"
    """Affects UI / session state (e.g., toggling permission mode, clearing screen)."""

    PROMPT = "prompt"
    """Sends a preset prompt into the conversation for AI to process in one round."""


@dataclass(frozen=True)
class CommandSpec:
    """Static spec description of a single slash command, immutable after construction.

    Parameters
    ----------
    name:
        Canonical name, no slash, lowercase (e.g., ``"help"``, ``"session"``).
    summary:
        One-line description shown in the /help listing.
    usage:
        Usage example string (e.g., ``"/session resume <id>"``).
    type:
        Execution semantic classification, see :class:`CommandType`.
    handler:
        Command handler function, signature ``(ctx: CommandContext, args: str) -> bool | None``.
        Returns truthy to exit the REPL; returns ``None`` / ``False`` to continue.
    aliases:
        Optional alias tuple (e.g., ``("quit", "q")``).
    arg_hint:
        Completion hint string (e.g., ``"<session-id>"``); empty string means no arguments.
    hidden:
        When ``True``, does not appear in the /help listing; defaults to ``False``.
    """

    name: str
    summary: str
    usage: str
    type: CommandType
    handler: Callable[[CommandContext, str], bool | None]
    aliases: tuple[str, ...] = field(default_factory=tuple)
    arg_hint: str = ""
    hidden: bool = False
