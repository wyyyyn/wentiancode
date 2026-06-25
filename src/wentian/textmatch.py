"""v0.12 · C93 · F80 (task T117) — four-mode string matching leaf.

Layering rule: top-level leaf module, stdlib only (re, fnmatch), zero business imports.
"""

from __future__ import annotations

import fnmatch
import re

__all__ = ["match_one"]

# Glob metacharacter set — if any one is present, route to fnmatch path.
_GLOB_CHARS = frozenset("*?[")


def _is_regex(pattern: str) -> bool:
    """Determine whether the pattern is a regex: starts and ends with / and length >= 2 (i.e., at least "//")."""
    return len(pattern) >= 2 and pattern[0] == "/" and pattern[-1] == "/"


def _has_glob(pattern: str) -> bool:
    """Determine whether the pattern contains glob metacharacters."""
    return any(c in _GLOB_CHARS for c in pattern)


def match_one(pattern: str, value: str) -> bool:
    """Four-mode single-string matching, in priority order:

    1. ``!`` prefix → negation: ``not match_one(pattern[1:], value)``
    2. ``/.../ `` wrapped (starts and ends with ``/``, len >= 2) → regex: ``re.search(inner, value)``;
       invalid regex (``re.error``) does not raise, returns ``False``.
    3. Contains glob metacharacters (``*`` ``?`` ``[``) → ``fnmatch.fnmatchcase(value, pattern)``
    4. Otherwise → exact: ``pattern == value``

    Empty pattern matches only empty string (takes the exact path).
    """
    # 1. Negation
    if pattern.startswith("!"):
        return not match_one(pattern[1:], value)

    # 2. Regex
    if _is_regex(pattern):
        inner = pattern[1:-1]
        try:
            return re.search(inner, value) is not None
        except re.error:
            return False

    # 3. Glob
    if _has_glob(pattern):
        return fnmatch.fnmatchcase(value, pattern)

    # 4. Exact
    return pattern == value
