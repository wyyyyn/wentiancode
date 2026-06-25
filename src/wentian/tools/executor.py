"""v0.3 · C10 · F24 (task T36)
v0.6 · C35 · F43/F45 (task T75) — the F26 confirmation gate is removed; gating
moves up to the AgentLoop's five-layer permission pipeline. The executor is now
a single-responsibility unit: parse → timed run.

ToolExecutor — the single, robust entry point for running a tool call.

One guarantee drives this module:

* **F24 (robustness):** ``execute()`` never lets an exception escape. Every
  failure mode — unknown tool, bad arguments, ``ToolError``, an unexpected
  crash inside ``run()``, or a hang — is converted into a model-facing
  :class:`ToolOutcome` with ``is_error=True``.

Permission gating (F26 in v0.3) is no longer this module's job: the five-layer
pipeline (F41-F49) decides Allow/Deny/Ask up in the AgentLoop *before* a call
reaches the executor. ``ToolOutcome.denied`` is retained as a field but is now
produced by the loop's human-in-the-loop Deny, not here.

Stdlib-only: no third-party imports (spec N6/N7). The only cross-layer import
is from :mod:`wentian.tools` itself.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

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
        True when the failure was specifically a permission refusal (a Deny
        from the permission pipeline / human-in-the-loop). Always a subset of
        ``is_error``; the UI uses it to distinguish "permission said no" from
        "the tool blew up". v0.6 · C35: no longer produced by the executor —
        the AgentLoop sets it when a Deny is fed back to the model.
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
        return f"Tool {name!r} is not registered. Available tools: {listed}."
    return f"Tool {name!r} is not registered. No tools are available."


def _bad_arguments_message(name: str) -> str:
    return (
        f"Tool {name!r} received no arguments (argument parsing failed); "
        "a JSON object of arguments is required."
    )


def _timeout_message(name: str, timeout_s: float) -> str:
    return f"Tool {name!r} timed out after {timeout_s:g}s and was abandoned."


def _crash_message(name: str, exc: BaseException) -> str:
    return f"Tool {name!r} raised an unexpected {type(exc).__name__}: {exc}"


class ToolExecutor:
    """Runs tool calls with timeout enforcement.

    v0.6 · C35 · F43/F45 (task T75) — the v0.3 confirmation gate (F26) is gone;
    permission decisions happen in the AgentLoop before a call reaches here.

    Parameters
    ----------
    registry:
        Source of truth for resolving a tool name to a :class:`Tool`.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def execute(self, call_id: str, name: str, arguments: dict | None) -> ToolOutcome:
        """Execute the named tool call, never raising.

        Order of checks: unknown name → arguments None → timed run. Any failure
        is returned as a ``ToolOutcome`` error.
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

        # 3. Run with a timeout enforced by a daemon worker thread.
        #    (v0.6 · C35 · F43/F45 · T75 — no confirmation gate; permission
        #    decisions are made upstream in the AgentLoop pipeline.)
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
