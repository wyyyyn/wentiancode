"""v0.12 · C97 · F81/F82/F83 (task T121) — action executor.
v0.13 · C117 · F102 (task T146) — SubAgentAction wired into HookEngine (launches real background sub-agent).

Four action executor functions, each with **failure softening**: catches all exceptions / timeouts
→ returns structured result or None, never bubbles up to the caller.

Layering rule (N41/N43):
  stdlib only (subprocess / urllib.request / urllib.error /
  json / os / logging / dataclasses) + same-package spec module + agents.spec (pure stdlib leaf).
  Zero rich / prompt_toolkit / provider / tools / repl / cli dependencies.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass

from wentian.agents.spec import AgentDef, AgentType
from wentian.hooks.spec import HttpAction, PromptAction, ShellAction, SubAgentAction

__all__ = [
    "ShellResult",
    "SafeDict",
    "run_shell",
    "inject_prompt",
    "call_http",
    "run_subagent_action",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class ShellResult:
    """Structured result for run_shell."""

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


# ---------------------------------------------------------------------------
# Utility classes
# ---------------------------------------------------------------------------


class SafeDict(dict):
    """Used with format_map — preserves literal {key} for missing keys instead of raising KeyError."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _build_env(context: dict) -> dict:
    """Build env: os.environ + WENTIAN_HOOK_<KEY> for simple scalar values."""
    extra = {
        f"WENTIAN_HOOK_{k.upper()}": str(v)
        for k, v in context.items()
        if isinstance(v, (str, int, float, bool))
    }
    return {**os.environ, **extra}


# ---------------------------------------------------------------------------
# Four action executors
# ---------------------------------------------------------------------------


def run_shell(action: ShellAction, context: dict) -> ShellResult | None:
    """Execute a shell command.

    - stdin is injected with ``json.dumps(context)``
    - env injects all simple scalar values as WENTIAN_HOOK_<KEY>
    - timeout → returns ShellResult with timed_out=True (no exception raised)
    - other exceptions → returns None (softened)
    """
    try:
        proc = subprocess.run(
            action.command,
            shell=True,
            input=json.dumps(context),
            env=_build_env(context),
            timeout=action.timeout,
            capture_output=True,
            text=True,
        )
        return ShellResult(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            timed_out=False,
        )
    except subprocess.TimeoutExpired:
        return ShellResult(exit_code=-1, stdout="", stderr="", timed_out=True)
    except Exception as exc:  # noqa: BLE001
        logger.debug("run_shell: caught exception (softened): %s", exc)
        return None


def inject_prompt(action: PromptAction, context: dict) -> str:
    """Replace {field} placeholders in action.text with values from context.

    Missing keys are preserved as literals (SafeDict semantics); internal exceptions → returns original text (softened).
    """
    try:
        return action.text.format_map(SafeDict(context))
    except Exception as exc:  # noqa: BLE001
        logger.debug("inject_prompt: caught exception (softened): %s", exc)
        return action.text


def call_http(action: HttpAction, context: dict) -> int | None:
    """Send an HTTP request to action.url with body json.dumps(context).

    Returns the HTTP status code; network / URL exceptions → returns None (softened).
    """
    try:
        data = json.dumps(context).encode("utf-8")
        req = urllib.request.Request(
            action.url,
            data=data,
            method=action.method,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=action.timeout) as resp:
            return resp.status
    except Exception as exc:  # noqa: BLE001
        logger.debug("call_http: caught exception (softened): %s", exc)
        return None


def run_subagent_action(
    action: SubAgentAction,
    context: dict,  # noqa: ARG001
    *,
    manager: object | None = None,
) -> str:
    """Sub-agent action executor (v0.13 · T146 · F102).

    - manager=None → logs "subagent not enabled (manager not configured)", returns placeholder result (v0.12 backward-compatible, no exception).
    - manager configured → constructs AgentDef, calls manager.submit(fire-and-forget), returns result string containing id=<task_id>.
    - All wrapped in try/except → logs + returns "failed" result string, never bubbles up to the caller (N54).
    """
    try:
        if manager is None:
            logger.info(
                "run_subagent_action: subagent not enabled (manager not configured); prompt=%r",
                action.prompt,
            )
            return "subagent_result:status=skipped,reason=manager_not_configured"

        agent_def = AgentDef(
            name="hook-subagent",
            description="hook-triggered sub-agent",
            body="",
        )
        task_id: str = manager.submit(  # type: ignore[union-attr]
            agent_def,
            action.prompt,
            background=True,
            agent_type=AgentType.DEFINITION,
        )
        logger.info(
            "run_subagent_action: background sub-agent submitted; id=%s prompt=%r",
            task_id,
            action.prompt,
        )
        return f"subagent_result:status=submitted,id={task_id}"

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "run_subagent_action: failed (softened): %s; prompt=%r",
            exc,
            action.prompt,
        )
        return f"subagent_result:status=failed,error={exc!r}"
