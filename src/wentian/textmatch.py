"""v0.12 · C93 · F80（任务 T117）— 四模式字符串匹配叶子。

分层铁律：顶层叶子模块，仅 stdlib（re、fnmatch），零业务 import。
"""

from __future__ import annotations

import fnmatch
import re

__all__ = ["match_one"]

# Glob 元字符集合——含任意一个则走 fnmatch 路径。
_GLOB_CHARS = frozenset("*?[")


def _is_regex(pattern: str) -> bool:
    """判定是否为正则模式：首尾均为 / 且长度 >= 2（即至少 "//"）。"""
    return len(pattern) >= 2 and pattern[0] == "/" and pattern[-1] == "/"


def _has_glob(pattern: str) -> bool:
    """判定是否含 glob 元字符。"""
    return any(c in _GLOB_CHARS for c in pattern)


def match_one(pattern: str, value: str) -> bool:
    """四模式单串匹配，按优先级顺序：

    1. ``!`` 前缀 → 反向：``not match_one(pattern[1:], value)``
    2. ``/.../ `` 包裹（首尾 ``/``，len >= 2）→ 正则：``re.search(inner, value)``；
       非法正则（``re.error``）不抛，返回 ``False``。
    3. 含 glob 元字符（``*`` ``?`` ``[``）→ ``fnmatch.fnmatchcase(value, pattern)``
    4. 否则 → 精确：``pattern == value``

    空 pattern 仅匹配空串（走精确路径）。
    """
    # 1. 反向（negation）
    if pattern.startswith("!"):
        return not match_one(pattern[1:], value)

    # 2. 正则（regex）
    if _is_regex(pattern):
        inner = pattern[1:-1]
        try:
            return re.search(inner, value) is not None
        except re.error:
            return False

    # 3. Glob
    if _has_glob(pattern):
        return fnmatch.fnmatchcase(value, pattern)

    # 4. 精确
    return pattern == value
