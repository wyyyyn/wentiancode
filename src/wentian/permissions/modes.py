"""v0.6 · C33 · F45 (task T73)

Mode fallback table — four permission modes × three tool categories, fallback verdict when no rule matches.

This is layer 4 of the five-layer decision pipeline (F46): when the blocklist / sandbox / rule
engine have not produced a final verdict, the current permission mode provides a fallback
verdict by tool category. Its range is **strictly {ALLOW, ASK}, never DENY** — DENY
can only come from the blocklist, sandbox, explicit deny rules, or human-in-the-loop rejection (F46).

The table hard-codes the spec F45 matrix as ``dict[Mode, dict[Category, Verdict]]``; adding a new
permission mode only requires extending the table (N18).

Layering rule: pure leaf module — depends only on stdlib and
:mod:`wentian.permissions.decision`, does not touch any SDK / terminal UI /
provider / agent / tools.
"""

from __future__ import annotations

from wentian.permissions.decision import Category, Mode, Verdict

__all__ = ["MODE_FALLBACK", "mode_fallback"]

_ALLOW = Verdict.ALLOW
_ASK = Verdict.ASK

#: spec F45 mode fallback matrix (range strictly {ALLOW, ASK}, never contains DENY).
MODE_FALLBACK: dict[Mode, dict[Category, Verdict]] = {
    Mode.DEFAULT: {
        Category.READ_ONLY: _ALLOW,
        Category.FILE_WRITE: _ASK,
        Category.COMMAND_EXEC: _ASK,
    },
    Mode.ACCEPT_EDITS: {
        Category.READ_ONLY: _ALLOW,
        Category.FILE_WRITE: _ALLOW,
        Category.COMMAND_EXEC: _ASK,
    },
    Mode.PLAN: {
        Category.READ_ONLY: _ALLOW,
        Category.FILE_WRITE: _ASK,
        Category.COMMAND_EXEC: _ASK,
    },
    Mode.BYPASS: {
        Category.READ_ONLY: _ALLOW,
        Category.FILE_WRITE: _ALLOW,
        Category.COMMAND_EXEC: _ALLOW,
    },
}


def mode_fallback(mode: Mode, category: Category) -> Verdict:
    """Return the fallback verdict for tools of ``category`` under ``mode`` (always ∈ {ALLOW, ASK})."""
    return MODE_FALLBACK[mode][category]
