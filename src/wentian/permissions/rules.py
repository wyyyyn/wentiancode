"""v0.6 · C31 · F43 (task T71)

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

Layering rule: pure leaf module — stdlib (``fnmatch`` / ``re`` / ``dataclasses``)
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


#: Friendly name → built-in tool name (user-facing rule declaration ↔ actual tool system tool name).
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
    """A single rule.

    ``pattern is None`` means match all calls to that tool; otherwise match by
    exact or glob. ``effect`` has only two values: ALLOW / DENY.
    """

    friendly: str
    pattern: str | None
    effect: Verdict


def _matches(pattern: str | None, target: str, *, is_path: bool) -> bool:
    """Determine whether *target* is matched by *pattern*.

    ``pattern is None`` → matches everything. Exact equality takes priority;
    otherwise match by glob: for file paths ``**`` crosses directories, for
    command strings ``**`` degrades to ``*``.
    """
    if pattern is None:
        return True
    if pattern == target:
        return True

    if is_path:
        return _path_glob_match(pattern, target)
    # Command string: ** is equivalent to * (no cross-directory semantics)
    collapsed = pattern.replace("**", "*")
    return fnmatch.fnmatchcase(target, collapsed)


def _path_glob_match(pattern: str, target: str) -> bool:
    """File path glob: ``*`` does not cross ``/``, ``**`` crosses directories."""
    regex = _path_glob_to_regex(pattern)
    return re.fullmatch(regex, target) is not None


def _path_glob_to_regex(pattern: str) -> str:
    """Convert a path glob with ``**`` cross-directory support to a regex.

    Rules: ``**`` → any character (including ``/``); ``*`` → any non-``/``
    character; ``?`` → a single non-``/`` character; all other characters are
    escaped literally.
    """
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                out.append(".*")  # ** crosses directories
                i += 2
            else:
                out.append("[^/]*")  # * does not cross directories
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
    """Single-layer rule set (corresponding to one config file).

    Within the same layer, deny is consulted before allow: if the same target
    is matched by both allow and deny → DENY.
    """

    allow: list[Rule] = field(default_factory=list)
    deny: list[Rule] = field(default_factory=list)

    def match(self, *, friendly: str, target: str, is_path: bool) -> Verdict | None:
        """Adjudicate within this layer; returns effect on match, ``None`` if no match."""
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
    """Three-layer stack: local > project > user; returns the nearest match immediately."""

    user: RuleSet = field(default_factory=RuleSet)
    project: RuleSet = field(default_factory=RuleSet)
    local: RuleSet = field(default_factory=RuleSet)

    def match(self, *, friendly: str, target: str, is_path: bool) -> Verdict | None:
        """Adjudicate layer by layer from nearest (local) to furthest (user); return the first non-``None`` result."""
        for layer in (self.local, self.project, self.user):
            verdict = layer.match(friendly=friendly, target=target, is_path=is_path)
            if verdict is not None:
                return verdict
        return None
