"""v0.6 · C31 · F43（任务 T71）

Rule engine for the permission system — friendly-name routing plus exact/glob
matching, layered nearest-hit-wins resolution.

A *rule* is declared in config as ``Friendly(pattern)`` (e.g. ``Bash(git *)``)
and carries one of two effects: ``ALLOW`` or ``DENY``. Friendly names
(Bash/Read/Write/Edit/Glob/Grep) map to the six builtin tool names via
:data:`FRIENDLY_TO_TOOL`. Matching:

* ``pattern is None`` matches *every* call of that tool.
* exact match: ``pattern == target``.
* glob match: ``*`` matches any run of characters. For **file paths**
  (``is_path=True``) ``**`` crosses directory boundaries; for **command
  strings** (``is_path=False``) ``**`` is collapsed to ``*`` — command strings
  carry no directory semantics (F43).

Resolution:

* :class:`RuleSet` is a single layer (one config file): within a layer ``deny``
  is consulted before ``allow`` (same-layer deny beats allow, F44).
* :class:`LayeredRules` stacks three layers ``local > project > user`` and
  returns the first non-``None`` verdict (nearest hit wins, F44).

分层铁律: pure leaf module — stdlib (``fnmatch`` / ``re`` / ``dataclasses``)
only, plus :mod:`wentian.permissions.decision`. No backend SDK, no terminal-UI
libraries.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field

from wentian.permissions.decision import Verdict

__all__ = [
    "FRIENDLY_TO_TOOL",
    "Rule",
    "RuleSet",
    "LayeredRules",
]


#: 友好名 → 内置工具名（面向用户的规则声明 ↔ 工具系统真实工具名）。
FRIENDLY_TO_TOOL: dict[str, str] = {
    "Bash": "run_command",
    "Read": "read_file",
    "Write": "write_file",
    "Edit": "edit_file",
    "Glob": "find_files",
    "Grep": "search_text",
}


@dataclass(frozen=True)
class Rule:
    """单条规则。

    ``pattern is None`` 表示匹配该工具的全部调用；否则按精确或 glob 匹配。
    ``effect`` 只有 ALLOW / DENY 两种。
    """

    friendly: str
    pattern: str | None
    effect: Verdict


def _matches(pattern: str | None, target: str, *, is_path: bool) -> bool:
    """判断 *target* 是否被 *pattern* 命中。

    ``pattern is None`` → 匹配一切。精确相等优先；否则按 glob：
    文件路径 ``**`` 跨目录，命令串 ``**`` 退化为 ``*``。
    """
    if pattern is None:
        return True
    if pattern == target:
        return True

    if is_path:
        return _path_glob_match(pattern, target)
    # 命令串：** 等价 *（无跨目录语义）
    collapsed = pattern.replace("**", "*")
    return fnmatch.fnmatchcase(target, collapsed)


def _path_glob_match(pattern: str, target: str) -> bool:
    """文件路径 glob：``*`` 不跨 ``/``、``**`` 跨目录。"""
    regex = _path_glob_to_regex(pattern)
    return re.fullmatch(regex, target) is not None


def _path_glob_to_regex(pattern: str) -> str:
    """把支持 ``**`` 跨目录的路径 glob 转成正则。

    规则：``**`` → 任意字符（含 ``/``）；``*`` → 任意非 ``/`` 字符；``?`` →
    单个非 ``/`` 字符；其余字符按字面转义。
    """
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                out.append(".*")  # ** 跨目录
                i += 2
            else:
                out.append("[^/]*")  # * 不跨目录
                i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(ch))
            i += 1
    return "".join(out)


@dataclass
class RuleSet:
    """单层规则集（对应一个配置文件）。

    同层内 deny 先于 allow 查询：同一 target 同时被 allow 与 deny 命中 → DENY。
    """

    allow: list[Rule] = field(default_factory=list)
    deny: list[Rule] = field(default_factory=list)

    def match(self, *, friendly: str, target: str, is_path: bool) -> Verdict | None:
        """在本层内裁决；命中返回 effect，未命中返回 ``None``。"""
        for rule in self.deny:
            if rule.friendly == friendly and _matches(
                rule.pattern, target, is_path=is_path
            ):
                return Verdict.DENY
        for rule in self.allow:
            if rule.friendly == friendly and _matches(
                rule.pattern, target, is_path=is_path
            ):
                return Verdict.ALLOW
        return None


@dataclass
class LayeredRules:
    """三层叠加：local > project > user，就近命中即止。"""

    user: RuleSet = field(default_factory=RuleSet)
    project: RuleSet = field(default_factory=RuleSet)
    local: RuleSet = field(default_factory=RuleSet)

    def match(self, *, friendly: str, target: str, is_path: bool) -> Verdict | None:
        """从最近层（local）到最远层（user）逐层裁决，第一个非 ``None`` 即返回。"""
        for layer in (self.local, self.project, self.user):
            verdict = layer.match(friendly=friendly, target=target, is_path=is_path)
            if verdict is not None:
                return verdict
        return None
