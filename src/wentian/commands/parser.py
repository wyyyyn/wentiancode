"""v0.10 · C88 · F71 (task T109) — slash command line parser.

Layering rule: pure leaf module, stdlib only (dataclasses).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ParsedCommand", "parse"]


@dataclass(frozen=True)
class ParsedCommand:
    """Parsed result — normalized command name and raw argument string.

    Parameters
    ----------
    name:
        Canonical name (lowercase), no leading slash.
    args:
        Argument string after the command name (stripped); empty string when there are no arguments.
    """

    name: str
    args: str


def parse(line: str) -> ParsedCommand | None:
    """Parse a line beginning with ``/``, returning :class:`ParsedCommand` or ``None``.

    The caller guarantees *line* is already stripped and starts with ``/``.
    A bare slash (``/``) or a slash immediately followed by whitespace (``/   ``, ``/ foo``) returns ``None``.

    Parameters
    ----------
    line:
        The stripped user input line, e.g. ``"/help"`` or ``"/session resume abc"``.

    Returns
    -------
    ParsedCommand | None
        Returns ParsedCommand on success; returns None when the command name is empty.
    """
    body = line[1:]  # strip the leading /
    head, _, rest = body.partition(" ")
    head = head.strip()
    if not head:
        # bare slash / or "/   " or "/ foo" (space immediately after slash makes head empty)
        return None
    return ParsedCommand(name=head.lower(), args=rest.strip())
