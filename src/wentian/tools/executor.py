"""v0.3 · C10 · F24/F26（任务 T36）

ToolExecutor — the single, robust entry point for running a tool call.

Two guarantees drive this module:

* **F24 (robustness):** ``execute()`` never lets an exception escape. Every
  failure mode — unknown tool, bad arguments, ``ToolError``, an unexpected
  crash inside ``run()``, or a hang — is converted into a model-facing
  :class:`ToolOutcome` with ``is_error=True``.
* **F26 (confirmation gate):** tools that declare ``requires_confirmation``
  must be approved by the host (via the injected ``confirm`` callable) before
  their side effects run. A refusal yields ``denied=True`` and ``run()`` is
  never invoked.

Stdlib-only: no third-party imports (spec N6/N7). The only cross-layer import
is from :mod:`wentian.tools` itself.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

from wentian.tools.base import Tool, ToolError
from wentian.tools.registry import ToolRegistry

__all__ = ["ToolOutcome", "ToolExecutor"]


@dataclass(frozen=True)
class ToolOutcome:
    """The result of executing a single tool call.

    Attributes
    ----------
    call_id:
        Echoes the call id supplied by the caller, so the outcome can be
        matched back to the originating tool-call event.
    name:
        The tool name that was requested (even if no such tool exists).
    content:
        Model-facing text — either the tool's result or a plain-language
        description of why it failed.
    is_error:
        True when *content* describes a failure rather than a result.
    denied:
        True when the failure was specifically a user refusal at the
        confirmation gate. Always a subset of ``is_error``; the UI uses it to
        distinguish "the user said no" from "the tool blew up".
    """

    call_id: str
    name: str
    content: str
    is_error: bool
    denied: bool = False


# ---------------------------------------------------------------------------
# Error-message helpers (model-facing, plain language)
# ---------------------------------------------------------------------------

def _unknown_tool_message(name: str, available: list[str]) -> str:
    if available:
        listed = ", ".join(available)
        return (
            f"Tool {name!r} is not registered. "
            f"Available tools: {listed}."
        )
    return f"Tool {name!r} is not registered. No tools are available."


def _bad_arguments_message(name: str) -> str:
    return (
        f"Tool {name!r} received no arguments (argument parsing failed); "
        "a JSON object of arguments is required."
    )


def _denied_message(name: str) -> str:
    return f"User denied execution of tool {name!r} (拒绝执行)."


def _timeout_message(name: str, timeout_s: float) -> str:
    return (
        f"Tool {name!r} timed out after {timeout_s:g}s and was abandoned."
    )


def _crash_message(name: str, exc: BaseException) -> str:
    return (
        f"Tool {name!r} raised an unexpected {type(exc).__name__}: {exc}"
    )


def _describe_call(name: str, arguments: dict) -> str:
    """Build a human-readable approval prompt naming the tool and its args."""
    if arguments:
        parts = ", ".join(f"{k}={v!r}" for k, v in arguments.items())
        return f"Run tool {name!r} with: {parts}?"
    return f"Run tool {name!r} (no arguments)?"


class ToolExecutor:
    """Runs tool calls with timeout enforcement and a confirmation gate.

    Parameters
    ----------
    registry:
        Source of truth for resolving a tool name to a :class:`Tool`.
    confirm:
        Callable invoked (with a human-readable description) only for tools
        whose ``requires_confirmation`` is True. Returning False denies
        execution.
    """

    def __init__(self, registry: ToolRegistry, *, confirm: Callable[[str], bool]) -> None:
        self._registry = registry
        self._confirm = confirm

    def execute(self, call_id: str, name: str, arguments: dict | None) -> ToolOutcome:
        """Execute the named tool call, never raising.

        Order of checks: unknown name → arguments None → confirmation gate →
        timed run. Any failure is returned as a ``ToolOutcome`` error.
        """
        # 1. Resolve the tool.
        tool = self._registry.get(name)
        if tool is None:
            return ToolOutcome(
                call_id=call_id,
                name=name,
                content=_unknown_tool_message(name, self._registry.names()),
                is_error=True,
            )

        # 2. Validate arguments shape.
        if arguments is None:
            return ToolOutcome(
                call_id=call_id,
                name=name,
                content=_bad_arguments_message(name),
                is_error=True,
            )

        # 3. Confirmation gate (side-effect tools only).
        if tool.requires_confirmation:
            approved = False
            try:
                approved = bool(self._confirm(_describe_call(name, arguments)))
            except Exception:  # noqa: BLE001 — a faulty confirm must not crash us.
                approved = False
            if not approved:
                return ToolOutcome(
                    call_id=call_id,
                    name=name,
                    content=_denied_message(name),
                    is_error=True,
                    denied=True,
                )

        # 4. Run with a timeout enforced by a daemon worker thread.
        return self._run_with_timeout(call_id, name, tool, arguments)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_with_timeout(
        self, call_id: str, name: str, tool: Tool, arguments: dict
    ) -> ToolOutcome:
        # Holder captures exactly one of (result, exception) from the worker.
        holder: dict[str, object] = {}

        def _worker() -> None:
            try:
                holder["result"] = tool.run(arguments)
            except BaseException as exc:  # noqa: BLE001 — capture everything.
                holder["error"] = exc

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        thread.join(tool.timeout_s)

        if thread.is_alive():
            # Worker is still running; abandon it (daemon → dies with process).
            return ToolOutcome(
                call_id=call_id,
                name=name,
                content=_timeout_message(name, tool.timeout_s),
                is_error=True,
            )

        if "error" in holder:
            exc = holder["error"]
            if isinstance(exc, ToolError):
                return ToolOutcome(
                    call_id=call_id,
                    name=name,
                    content=str(exc),
                    is_error=True,
                )
            return ToolOutcome(
                call_id=call_id,
                name=name,
                content=_crash_message(name, exc),  # type: ignore[arg-type]
                is_error=True,
            )

        result = holder.get("result", "")
        return ToolOutcome(
            call_id=call_id,
            name=name,
            content="" if result is None else str(result),
            is_error=False,
        )
