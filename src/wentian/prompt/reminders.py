"""Dynamic reminder assembly — EnvInfo / render_env_reminder / render_switch_reminder /
build_request_decorator.

v0.5 · C22 (task T60)

This module is a pure function layer:
- No backend SDK dependencies (no anthropic / openai)
- No rich / prompt_toolkit dependencies
- stdlib only + wentian.providers.base (Message type)

Semantics: before each LLM request is sent, the closure returned by
build_request_decorator injects dynamic content (environment info + session switch
reminders) into the message stream via <system-reminder> tags; the injected result is
valid only for the current request and **is never persisted to session history**.

Key constraint: the decorator never mutates the input list or the content field of any
Message dict within it — the structural guarantee that reminders are never persisted.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wentian.providers.base import Message

__all__ = [
    "EnvInfo",
    "render_env_reminder",
    "render_switch_reminder",
    "render_skill_body_reminder",
    "build_request_decorator",
]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EnvInfo:
    """Environment snapshot for the current request, used to generate the environment
    info reminder block.

    Attributes:
        cwd:        Current working directory.
        os:         Operating system identifier string (e.g. ``"Darwin 25.0"``).
        date:       Date string (e.g. ``"2026-06-15"``).
        git_branch: Current Git branch name; ``None`` if not a git repository.
    """

    cwd: Path
    os: str
    date: str
    git_branch: str | None


# ---------------------------------------------------------------------------
# Render functions
# ---------------------------------------------------------------------------


def render_env_reminder(env: EnvInfo) -> str:
    """Returns an environment info block containing <system-reminder> tags.

    Contains four items: working directory / operating system / date / Git branch.
    When ``git_branch`` is ``None``, displays "(not a git repository)".
    """
    branch_str = env.git_branch if env.git_branch is not None else "(not a git repository)"
    body = (
        f"Working directory: {env.cwd}\n"
        f"Operating system: {env.os}\n"
        f"Date: {env.date}\n"
        f"Git branch: {branch_str}"
    )
    return f"<system-reminder>\n{body}\n</system-reminder>"


# Fixed text for full plan mode reminder
_PLAN_MODE_FULL = (
    "<system-reminder>\n"
    "Currently in plan mode (Plan Mode).\n"
    "Rules: read-only environment and codebase inspection; provide a clear step-by-step "
    "execution plan then stop, waiting for user confirmation;"
    "Do not make any changes (do not write files, do not execute side-effect commands).\n"
    "User will type /do to switch to execution mode before starting implementation.\n"
    "</system-reminder>"
)

# Compact plan mode reminder (used in subsequent rounds)
_PLAN_MODE_BRIEF = (
    "<system-reminder>(still in plan mode: read-only inspection, no changes yet)</system-reminder>"
)


def render_switch_reminder(
    *,
    plan_mode: bool,
    round_index: int,
    repeat_every: int = 5,
) -> str | None:
    """Session switch reminder (currently only plan mode).

    - ``plan_mode=False`` → ``None`` (no reminder injected).
    - ``plan_mode=True``:

      - ``round_index == 1`` or ``(round_index - 1) % repeat_every == 0``
        → returns the **full** reminder (including <system-reminder> tags);
      - otherwise → returns a **compact** one-line reminder (also wrapped in
        <system-reminder> tags).

    Args:
        plan_mode:    Whether in plan mode.
        round_index:  Current round index (starting from 1).
        repeat_every: Repeat interval for full reminders, default 5 rounds.

    Returns:
        Reminder string, or ``None``.
    """
    if not plan_mode:
        return None

    is_full = (round_index == 1) or ((round_index - 1) % repeat_every == 0)
    return _PLAN_MODE_FULL if is_full else _PLAN_MODE_BRIEF


def render_skill_body_reminder(name: str, body: str) -> str:
    """Wraps the body of a single activated skill into a <system-reminder> block.

    Like::

        <system-reminder>
        # Activated Skill: <name>
        <body>
        </system-reminder>
    """
    return f"<system-reminder>\n# Activated Skill: {name}\n{body}\n</system-reminder>"


# ---------------------------------------------------------------------------
# Request decorator factory
# ---------------------------------------------------------------------------


def build_request_decorator(
    *,
    env: EnvInfo,
    plan_mode: bool,
    repeat_every: int = 5,
    active_skill_bodies: Callable[[], list[tuple[str, str]]] | None = None,
) -> Callable[[list[Message], int], list[Message]]:
    """Returns the request-time assembly closure ``decorator(messages, round_index) -> list[Message]``.

    Closure behavior:
    1. Copy ``messages`` (without modifying the input list or the content of any dict
       within it).
    2. In the copy, **prepend** the env reminder to the content of the **first**
       ``role == 'user'`` message.
    3. In the copy, **append** the switch reminder to the content of the **last**
       ``role == 'user'`` message (when ``render_switch_reminder`` returns non-``None``).
    4. If ``active_skill_bodies`` is provided, call it **at runtime** to get the
       activated skill list, and append a ``<system-reminder>`` block for each
       ``(name, body)`` to the **last** user message (after the switch reminder).
    5. Return the new list.

    In a single-round scenario, the first user and last user message are the same — it
    is simultaneously prepended with env, appended with switch, and appended with skill
    bodies (order: env → original content → switch → skills).

    When there are no user messages, safely returns the copy without raising an error.

    Args:
        env:          Environment info snapshot.
        plan_mode:    Whether in plan mode.
        repeat_every: Repeat interval passed to ``render_switch_reminder``.
        active_skill_bodies: Optional zero-argument callback returning
            ``[(name, rendered_body), ...]``.
            **Called at runtime on every closure invocation** (not a snapshot) — skills
            activated mid-round take effect from the next round. When ``None`` or
            returns ``[]``, behavior is byte-for-byte identical to not passing the
            argument.

    Returns:
        Closure ``(messages, round_index) -> list[Message]``.
    """
    env_reminder = render_env_reminder(env)

    def decorator(messages: list[Message], round_index: int) -> list[Message]:
        # Find the index of the first and last user messages
        first_user_idx: int | None = None
        last_user_idx: int | None = None
        for i, msg in enumerate(messages):
            if msg.get("role") == "user":
                if first_user_idx is None:
                    first_user_idx = i
                last_user_idx = i

        switch_reminder = render_switch_reminder(
            plan_mode=plan_mode,
            round_index=round_index,
            repeat_every=repeat_every,
        )

        # Read activated skill list at runtime (not a snapshot), render into blocks to append
        skill_reminders: list[str] = []
        if active_skill_bodies is not None:
            skill_reminders = [
                render_skill_body_reminder(name, body)
                for name, body in active_skill_bodies()
            ]

        # All content to append to the last user message (switch first, skills after)
        suffixes: list[str] = []
        if switch_reminder is not None:
            suffixes.append(switch_reminder)
        suffixes.extend(skill_reminders)

        # Shallow-copy the list; only deep-copy the messages whose content needs to be modified
        result: list[Message] = list(messages)

        if first_user_idx is not None:
            # last_user_idx is guaranteed set whenever first_user_idx is set
            assert last_user_idx is not None  # narrow type for checker

            # Indices of messages whose content needs to be modified (same in single-round, set deduplicates automatically)
            indices_to_copy: set[int] = {first_user_idx}
            if suffixes:
                indices_to_copy.add(last_user_idx)

            # copy.copy (shallow copy) is sufficient: only changes content string, not nested structures
            copied: dict[int, Message] = {
                idx: copy.copy(messages[idx]) for idx in indices_to_copy
            }

            # Prepend env reminder to the first user message
            copied[first_user_idx]["content"] = (  # type: ignore[index]
                env_reminder + "\n" + copied[first_user_idx]["content"]  # type: ignore[index]
            )

            # Append switch / skill reminders to the last user message (last is already in set when suffixes exist)
            if suffixes:
                copied[last_user_idx]["content"] = "\n".join(  # type: ignore[index]
                    [copied[last_user_idx]["content"], *suffixes]  # type: ignore[index]
                )

            # Write back to result list
            for idx, msg in copied.items():
                result[idx] = msg

        return result

    return decorator
