"""v0.6 · C29 · F41/N10（任务 T69）

Dangerous-command blacklist — the highest-priority, *non-bypassable* layer of
the permission pipeline.

:func:`check_command` runs a command string against a fixed set of built-in
regexes covering well-known catastrophic patterns (recursive force-deletes of
root / the home directory, writing to block devices, formatting filesystems,
the classic fork bomb, redirecting onto disk devices, …). A match yields a
``Decision(DENY, BLACKLIST, reason)``; no match yields ``None`` so the next
layer of the pipeline can run.

**N10 (not bypassable):** there is *no* enable/disable flag, config parameter,
or mutable registry. The patterns are a module-level constant tuple and
``check_command`` takes only the command string. Not even
``bypassPermissions`` mode relaxes this layer.

**Heuristic, not exhaustive:** this is a best-effort safety net for obviously
catastrophic commands. It deliberately does *not* attempt to enumerate every
dangerous command (an impossible goal — commands can be obfuscated, aliased, or
constructed at runtime). False negatives are expected; the later pipeline
layers (sandbox, rules, mode gating) provide defence in depth.

分层铁律: pure leaf module — stdlib ``re`` only, plus the sibling decision
types. No backend SDK, no terminal-UI libraries, no cross-layer wentian
imports.
"""
from __future__ import annotations

import re

from wentian.permissions.decision import Decision, Source, Verdict

__all__ = ["check_command"]


# ---------------------------------------------------------------------------
# Built-in dangerous patterns.
#
# Each ``(regex, reason)`` pair is compiled below into the ``_DANGEROUS`` tuple
# of plain ``re.Pattern`` objects (reasons live in a parallel mapping). Patterns
# are intentionally tolerant of leading wrappers (``sudo``, ``env VAR=…`` etc.)
# via the shared ``_LEADIN`` fragment, and of flag ordering / repetition
# (``-rf`` / ``-fr`` / ``--recursive --force``). They are matched
# case-sensitively against the raw command string.
# ---------------------------------------------------------------------------

# Optional command prefixes to see *through* (sudo, doas, an env assignment, a
# leading subshell paren, a statement separator …) before the real verb.
_LEADIN = r"(?:^|[;&|]\s*|\(\s*)(?:\s*(?:sudo|doas|env|nice|nohup|time)\b\s+)*"

# ``rm`` that carries BOTH a recursive and a force flag somewhere in its args.
# Two lookaheads assert each flag exists without consuming, so flag order is
# irrelevant; ``\w*r\w*`` / ``\w*f\w*`` accept clustered forms like ``-rf``.
_RM_RF = (
    r"\brm\b"
    r"(?=(?:\s+\S+)*\s+(?:-\w*r\w*|--recursive|-R)\b)"
    r"(?=(?:\s+\S+)*\s+(?:-\w*f\w*|--force)\b)"
)
# A root target: ``/`` (optionally quoted, optionally a trailing ``*``) standing
# alone — not ``/some/path`` or ``build/``.
_TARGET_ROOT = r"(?:\s+\S+)*\s+['\"]?/['\"]?(?:\s*\*)?\s*(?:\s|$)"
# A home target: ``~`` / ``$HOME`` / ``${HOME}`` (optionally with a subpath).
_TARGET_HOME = r"(?:\s+\S+)*\s+['\"]?(?:~|\$HOME|\$\{HOME\})(?:/\S*)?['\"]?(?:\s|$)"

# Block-device family used by both ``dd of=`` and stray redirects.
_DEV = r"/dev/(?:sd|disk|nvme|hd|vd)\w*"

_SPECS: tuple[tuple[str, str], ...] = (
    (
        _LEADIN + _RM_RF + _TARGET_ROOT,
        "拒绝：递归强制删除根目录（rm -rf /）是不可逆的灾难性操作，已被内置黑名单硬拦截。",
    ),
    (
        _LEADIN + _RM_RF + _TARGET_HOME,
        "拒绝：递归强制删除家目录（rm -rf ~ / $HOME）会清空个人数据，已被内置黑名单硬拦截。",
    ),
    (
        _LEADIN + r"\bdd\b(?:\s+\S+)*\s+of=" + _DEV,
        "拒绝：dd 直接写入块设备（of=/dev/…）会摧毁磁盘数据，已被内置黑名单硬拦截。",
    ),
    (
        _LEADIN + r"\bmkfs(?:\.\w+)?\b",
        "拒绝：mkfs 会格式化文件系统、清空目标设备，已被内置黑名单硬拦截。",
    ),
    (
        r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",
        "拒绝：fork 炸弹会瞬间耗尽系统进程资源、使机器失去响应，已被内置黑名单硬拦截。",
    ),
    (
        r">>?\s*" + _DEV,
        "拒绝：重定向覆盖磁盘块设备（> /dev/…）会破坏磁盘数据，已被内置黑名单硬拦截。",
    ),
)

#: Compiled dangerous patterns (immutable tuple — no public registry to tamper
#: with). Reasons are looked up by index via ``_REASONS``.
_DANGEROUS: tuple[re.Pattern[str], ...] = tuple(re.compile(p) for p, _ in _SPECS)
_REASONS: tuple[str, ...] = tuple(reason for _, reason in _SPECS)


def check_command(command: str) -> Decision | None:
    """Match ``command`` against the built-in dangerous patterns.

    Returns a ``Decision(DENY, BLACKLIST, reason)`` on the first matching
    pattern, or ``None`` when nothing matches (so the pipeline continues to the
    next layer).

    There is intentionally no second parameter: the blacklist is hard-coded and
    cannot be disabled or reconfigured (N10).
    """
    for pattern, reason in zip(_DANGEROUS, _REASONS):
        if pattern.search(command):
            return Decision(verdict=Verdict.DENY, source=Source.BLACKLIST, reason=reason)
    return None
