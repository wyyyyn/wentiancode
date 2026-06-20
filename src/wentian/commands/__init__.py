"""v0.10 · commands 包入口（任务 T108–T111）

导出斜杠命令系统的公共符号：CommandType、CommandSpec、ParsedCommand、
parse、CommandRegistry、CommandContext。

分层铁律：纯叶子包，仅 stdlib + typing / dataclasses / enum / collections.abc。
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
