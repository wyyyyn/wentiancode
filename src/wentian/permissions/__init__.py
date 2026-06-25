"""v0.6 · C29 · F41/N10 (task T69)

Permission system — a pure, stdlib-only package that decides whether a tool
call is allowed, denied, or needs a human. This ``__init__`` re-exports the
decision vocabulary and the blacklist entry point so callers can ``from
wentian.permissions import check_command, Decision, Verdict, ...``.

No SDK / rich / prompt_toolkit / cross-layer wentian imports (layering rule).
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
from wentian.permissions.modes import mode_fallback
from wentian.permissions.pipeline import PermissionPipeline
from wentian.permissions.rules import LayeredRules, Rule, RuleSet
from wentian.permissions.sandbox import check_path
from wentian.permissions.settings import Settings, load_settings

__all__ = [
    "Mode",
    "Category",
    "Verdict",
    "Source",
    "Decision",
    "MODE_CYCLE",
    "check_command",
    "check_path",
    "Rule",
    "RuleSet",
    "LayeredRules",
    "load_settings",
    "Settings",
    "mode_fallback",
    "PermissionPipeline",
]
