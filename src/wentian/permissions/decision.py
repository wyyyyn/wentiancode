"""v0.6 · C29 · F41/N10 (task T69)

Permission decision types — the vocabulary shared by every layer of the
permission pipeline.

Three orthogonal axes describe a permission outcome:

* :class:`Mode` — the four runtime permission modes (Shift+Tab cycles them).
  The enum *values* double as the on-disk config strings and the status-line
  labels, so they must stay stable.
* :class:`Category` — the coarse classification of a tool (read-only,
  file-write, command-exec). Each layer of the pipeline only applies to some
  categories.
* :class:`Verdict` — the three terminal states a decision can carry.
* :class:`Source` — *which* layer produced the verdict, so refusals can be
  worded by origin when fed back to the model.

:class:`Decision` bundles a verdict with its source and a model-facing reason.
``MODE_CYCLE`` fixes the Shift+Tab order.

Layering rule: pure leaf module — stdlib ``enum`` / ``dataclasses`` only. No backend
SDK, no terminal-UI libraries, no cross-layer wentian imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = [
    "Mode",
    "Category",
    "Verdict",
    "Source",
    "Decision",
    "MODE_CYCLE",
]


class Mode(str, Enum):
    """Four permission modes; enum values serve as config file / status-bar labels."""

    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    PLAN = "plan"
    BYPASS = "bypassPermissions"


class Category(str, Enum):
    """Three tool categories — determines which layers a call passes through."""

    READ_ONLY = "read_only"
    FILE_WRITE = "file_write"
    COMMAND_EXEC = "command_exec"


class Verdict(str, Enum):
    """Three verdict states."""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class Source(str, Enum):
    """Deny/Allow source — used to differentiate reasons by origin when feeding back."""

    BLACKLIST = "blacklist"
    SANDBOX = "sandbox"
    RULE = "rule"
    MODE = "mode"
    HUMAN = "human"


@dataclass(frozen=True)
class Decision:
    """The result of a single permission decision.

    ``reason`` is model-facing; required for Deny (worded by source), may be
    empty for Allow/Ask.
    """

    verdict: Verdict
    source: Source
    reason: str = ""


#: Cycling order for Shift+Tab (default → acceptEdits → plan → bypassPermissions → …).
MODE_CYCLE: tuple[Mode, ...] = (
    Mode.DEFAULT,
    Mode.ACCEPT_EDITS,
    Mode.PLAN,
    Mode.BYPASS,
)
