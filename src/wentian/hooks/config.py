"""v0.12 · C96 · F77/F82（任务 T120）— Hook 配置加载 + 集中校验。

`parse_hooks(raw) -> list[HookRule]` 将 ``hooks:`` 原始列表逐条解析为
:class:`~wentian.hooks.spec.HookRule`，集中校验所有违规项并 raise
:class:`HookConfigError`（含条目下标与字段定位信息）。
`raw` 缺失 / 非 list ⇒ ``[]``（安全降级）。

分层铁律（N41）：仅 import 同包 ``spec`` + stdlib；
零 import ``wentian.config``（避免环）、rich、prompt_toolkit、后端 SDK、
agent / repl / providers / tools。
"""

from __future__ import annotations

from typing import Any

from wentian.hooks.spec import (
    INTERCEPT_EVENTS,
    Action,
    Clause,
    Condition,
    HookEvent,
    HookRule,
    HttpAction,
    Match,
    PromptAction,
    ShellAction,
    SubAgentAction,
)

__all__ = ["HookConfigError", "parse_hooks"]

# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

_VALID_ACTION_TYPES = frozenset({"shell", "prompt", "http", "subagent"})
_VALID_MATCH_VALUES = frozenset({"all", "any"})


class HookConfigError(Exception):
    """Raised for any hooks configuration validation failure.

    Messages always include the rule index (``hooks[N]``) and the offending
    field name so callers can locate the problem quickly.

    Defined locally (not imported from ``wentian.config``) to avoid a cyclic
    import: ``wentian.config`` will import ``wentian.hooks.config`` in v0.12
    wave-3 — so this module must not import back from it.
    """


# ---------------------------------------------------------------------------
# Helpers — action parsing
# ---------------------------------------------------------------------------


def _parse_action(raw_action: Any, prefix: str) -> Action:
    """Parse one ``action:`` dict into the appropriate Action dataclass.

    Raises :class:`HookConfigError` with *prefix* prepended to the message
    on any validation failure.
    """
    if not isinstance(raw_action, dict):
        raise HookConfigError(
            f"{prefix}: 'action' must be a mapping, got {type(raw_action).__name__!r}"
        )

    action_type = raw_action.get("type")
    if not action_type:
        raise HookConfigError(
            f"{prefix}: 'action.type' is required; "
            f"valid values: {sorted(_VALID_ACTION_TYPES)}"
        )
    if action_type not in _VALID_ACTION_TYPES:
        raise HookConfigError(
            f"{prefix}: invalid action type {action_type!r}; "
            f"valid values: {sorted(_VALID_ACTION_TYPES)}"
        )

    if action_type == "shell":
        return _parse_shell_action(raw_action, prefix)
    if action_type == "prompt":
        return _parse_prompt_action(raw_action, prefix)
    if action_type == "http":
        return _parse_http_action(raw_action, prefix)
    # subagent
    return SubAgentAction(prompt=str(raw_action.get("prompt", "")))


def _require_field(d: dict, field: str, prefix: str) -> Any:
    """Return ``d[field]`` or raise :class:`HookConfigError`."""
    val = d.get(field)
    if not val and val != 0:
        raise HookConfigError(
            f"{prefix}: 'action.{field}' is required for this action type"
        )
    return val


def _parse_timeout(raw_action: dict, prefix: str) -> int | None:
    """Parse and validate optional ``timeout`` field."""
    timeout = raw_action.get("timeout")
    if timeout is None:
        return None
    if not isinstance(timeout, int) or timeout <= 0:
        raise HookConfigError(
            f"{prefix}: 'action.timeout' must be a positive integer, got {timeout!r}"
        )
    return timeout


def _parse_shell_action(raw_action: dict, prefix: str) -> ShellAction:
    command = _require_field(raw_action, "command", prefix)
    timeout = _parse_timeout(raw_action, prefix)
    return ShellAction(command=str(command), timeout=timeout)


def _parse_prompt_action(raw_action: dict, prefix: str) -> PromptAction:
    text = _require_field(raw_action, "text", prefix)
    return PromptAction(text=str(text))


def _parse_http_action(raw_action: dict, prefix: str) -> HttpAction:
    url = _require_field(raw_action, "url", prefix)
    method = str(raw_action.get("method", "POST"))
    timeout = _parse_timeout(raw_action, prefix)
    return HttpAction(url=str(url), method=method, timeout=timeout)


# ---------------------------------------------------------------------------
# Helpers — condition parsing
# ---------------------------------------------------------------------------


def _parse_condition(raw_if: Any, prefix: str) -> Condition:
    """Parse one ``if:`` dict into a :class:`Condition`."""
    if not isinstance(raw_if, dict):
        raise HookConfigError(
            f"{prefix}: 'if' must be a mapping, got {type(raw_if).__name__!r}"
        )

    raw_match = raw_if.get("match", "all")
    if raw_match not in _VALID_MATCH_VALUES:
        raise HookConfigError(
            f"{prefix}: 'if.match' must be 'all' or 'any', got {raw_match!r}"
        )
    match = Match.ALL if raw_match == "all" else Match.ANY

    raw_clauses = raw_if.get("clauses") or []
    clauses = tuple(
        Clause(field=str(c["field"]), pattern=str(c["pattern"]))
        for c in raw_clauses
        if isinstance(c, dict)
    )
    return Condition(match=match, clauses=clauses)


# ---------------------------------------------------------------------------
# Helpers — rule parsing
# ---------------------------------------------------------------------------

# Map from canonical string value (e.g. "PreToolUse") to HookEvent member.
# Built once at module load; HookEvent is a closed enum so this is stable.
_EVENT_BY_VALUE: dict[str, HookEvent] = {e.value: e for e in HookEvent}


def _parse_event(raw_event: Any, prefix: str) -> HookEvent:
    """Resolve a string event name to :class:`HookEvent`."""
    if not raw_event:
        raise HookConfigError(f"{prefix}: 'event' is required")
    event = _EVENT_BY_VALUE.get(str(raw_event))
    if event is None:
        raise HookConfigError(
            f"{prefix}: invalid event {raw_event!r}; "
            f"valid values: {sorted(_EVENT_BY_VALUE)}"
        )
    return event


def _parse_rule(item: Any, prefix: str) -> HookRule:
    """Parse a single rule dict; raise :class:`HookConfigError` on any problem."""
    if not isinstance(item, dict):
        raise HookConfigError(
            f"{prefix}: each rule must be a mapping, got {type(item).__name__!r}"
        )

    # --- event ---
    event = _parse_event(item.get("event"), prefix)

    # --- action ---
    if "action" not in item:
        raise HookConfigError(f"{prefix}: 'action' is required")
    action = _parse_action(item["action"], prefix)

    # --- condition (optional) ---
    condition: Condition | None = None
    if "if" in item:
        condition = _parse_condition(item["if"], prefix)

    # --- once / background ---
    once = bool(item.get("once", False))
    background = bool(item.get("background", False))

    # --- cross-field constraint: intercept event forbids background ---
    if background and event in INTERCEPT_EVENTS:
        raise HookConfigError(
            f"{prefix}: 'background: true' is forbidden for intercept event "
            f"{event.value!r} (intercept hooks must run synchronously)"
        )

    return HookRule(
        event=event,
        action=action,
        condition=condition,
        once=once,
        background=background,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_hooks(raw: Any) -> list[HookRule]:
    """Parse a top-level ``hooks:`` value into a list of :class:`HookRule`.

    Parameters
    ----------
    raw:
        The raw value from ``config["hooks"]``.  If it is ``None`` or not a
        ``list``, an empty list is returned (safe degradation — no hooks
        configured).

    Returns
    -------
    list[HookRule]
        Validated rule objects in declaration order.

    Raises
    ------
    HookConfigError
        If any rule contains an invalid event name, action type, missing
        required field, or violates a cross-field constraint (e.g.
        ``PreToolUse`` with ``background: true``).
    """
    if not isinstance(raw, list):
        return []

    rules: list[HookRule] = []
    for i, item in enumerate(raw):
        rules.append(_parse_rule(item, f"hooks[{i}]"))
    return rules
