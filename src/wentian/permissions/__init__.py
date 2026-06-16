"""v0.6 · C29 · F41/N10（任务 T69）

Permission system — a pure, stdlib-only package that decides whether a tool
call is allowed, denied, or needs a human. This ``__init__`` re-exports the
decision vocabulary and the blacklist entry point so callers can ``from
wentian.permissions import check_command, Decision, Verdict, ...``.

No SDK / rich / prompt_toolkit / cross-layer wentian imports (分层铁律).
"""
from __future__ import annotations

from wentian.permissions.blacklist import check_command
from wentian.permissions.decision import (
    MODE_CYCLE,
    Category,
    Decision,
    Mode,
    Source,
    Verdict,
)

__all__ = [
    "Mode",
    "Category",
    "Verdict",
    "Source",
    "Decision",
    "MODE_CYCLE",
    "check_command",
]
