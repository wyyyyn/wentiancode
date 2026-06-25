"""v0.10 · commands package entry point (tasks T108–T111)

Exports public symbols for the slash command system: CommandType, CommandSpec, ParsedCommand,
parse, CommandRegistry, CommandContext.

Layering rule: pure leaf package, stdlib only + typing / dataclasses / enum / collections.abc.
"""

from __future__ import annotations

from wentian.commands.context import CommandContext
from wentian.commands.parser import ParsedCommand, parse
from wentian.commands.registry import CommandRegistry
from wentian.commands.spec import CommandSpec, CommandType

__all__ = [
    "CommandType",
    "CommandSpec",
    "ParsedCommand",
    "parse",
    "CommandRegistry",
    "CommandContext",
]
