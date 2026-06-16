"""v0.6 · C29 · F41/N10（任务 T69）

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

分层铁律: pure leaf module — stdlib ``enum`` / ``dataclasses`` only. No backend
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
    """四档权限模式；枚举值即配置文件 / 状态栏文案。"""

    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    PLAN = "plan"
    BYPASS = "bypassPermissions"


class Category(str, Enum):
    """工具三分类——决定一次调用要经过哪些层。"""

    READ_ONLY = "read_only"
    FILE_WRITE = "file_write"
    COMMAND_EXEC = "command_exec"


class Verdict(str, Enum):
    """判定三态。"""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class Source(str, Enum):
    """Deny/Allow 来源——用于回灌时按来源区分原因。"""

    BLACKLIST = "blacklist"
    SANDBOX = "sandbox"
    RULE = "rule"
    MODE = "mode"
    HUMAN = "human"


@dataclass(frozen=True)
class Decision:
    """一次权限判定的结果。

    ``reason`` 面向模型；Deny 时按来源措辞、必填，Allow/Ask 时可留空。
    """

    verdict: Verdict
    source: Source
    reason: str = ""


#: Shift+Tab 的循环顺序（default → acceptEdits → plan → bypassPermissions → …）。
MODE_CYCLE: tuple[Mode, ...] = (
    Mode.DEFAULT,
    Mode.ACCEPT_EDITS,
    Mode.PLAN,
    Mode.BYPASS,
)
