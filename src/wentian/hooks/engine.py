"""v0.12 · C98 · F78/F79/F82/F83 (task T122) — Hook engine.

Rule indexing by event, unified firing and interception, execution control,
failure softening, injection accumulation, hook logging.

Public interface:
  HookEngine(rules, *, logger=None)
    .fire(event, context)           — non-intercepting event entry point
    .pretool(context) -> str|None   — interception entry point (synchronous)
    .drain_injections() -> str      — drain and clear _pending buffer
    .close()                        — brief join of background threads

Layering rule (N41):
  Import stdlib only (logging / threading) + same-package spec / conditions / actions.
  Zero agent / repl / providers / tools / rich / prompt_toolkit dependencies.
  Event context is a plain dict, constructed by the assembly layer before being fed in.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Optional

import wentian.hooks.actions as _actions
from wentian.hooks.conditions import evaluate
from wentian.hooks.spec import (
    HookEvent,
    HookRule,
    HttpAction,
    PromptAction,
    ShellAction,
    SubAgentAction,
)

__all__ = ["HookEngine"]

_DEFAULT_CLOSE_TIMEOUT = 1.0  # seconds per thread join on close()


class HookEngine:
    """Unified hook firing and interception engine.

    Constructor: ``HookEngine(rules, *, logger=None)``

    - ``_by_event``: rules grouped by HookEvent, preserving declaration order.
    - ``_pending``: injection text buffer produced by PromptAction (consumed by drain_injections).
    - ``_fired_once``: set of ``id(rule)`` for rules with ``once=True`` that have already fired.
    - ``_threads``: list tracking background daemon threads, for close() join.
    """

    def __init__(
        self,
        rules: list[HookRule],
        *,
        logger: Optional[logging.Logger] = None,
        agents_manager: object | None = None,
    ) -> None:
        self._rules = rules
        self._logger = logger or logging.getLogger("wentian.hooks")
        self._agents_manager = agents_manager

        # Group by event, preserving declaration order
        self._by_event: dict[HookEvent, list[HookRule]] = defaultdict(list)
        for rule in rules:
            self._by_event[rule.event].append(rule)

        self._pending: list[str] = []
        self._fired_once: set[int] = set()
        self._threads: list[threading.Thread] = []
        self._threads_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def fire(self, event: HookEvent, context: dict) -> None:  # type: ignore[type-arg]
        """Non-intercepting event entry point.

        For each rule matching this event:
          - condition not matched → skip
          - once=True and already fired → skip
          - background=True → dispatch to daemon thread fire-and-forget
          - otherwise execute synchronously
        Fully wrapped in try/except → logs, never raises to caller.
        """
        for rule in self._by_event.get(event, []):
            if not self._should_fire(rule, context):
                continue
            if rule.background:
                self._fire_in_background(rule, context)
            else:
                self._execute_action_soft(rule, context)

    def pretool(self, context: dict) -> Optional[str]:  # type: ignore[type-arg]
        """Interception entry point (synchronous).

        Iterate PreToolUse rules in declaration order:
          - condition not matched → skip
          - ShellAction: run_shell
            - exit_code == 2 → return denial reason (stderr preferred, fall back to stdout), short-circuit
            - exit_code == 0 → continue (no interception)
            - other (timed_out / non-0 non-2 / exception / None) → fail-open (log and continue)
          - non-ShellAction → execute side effects / injection (prompt into _pending), no interception
        Fully wrapped in try/except → fail-open; no rule blocks → None.
        """
        for rule in self._by_event.get(HookEvent.PRE_TOOL_USE, []):
            if not evaluate(rule.condition, context):
                continue
            try:
                result = self._dispatch_action_for_pretool(rule, context)
                if result is not None:
                    # result is a deny reason string
                    return result
            except Exception as exc:  # noqa: BLE001
                self._logger.warning(
                    "pretool: action raised exception (fail-open): %s", exc
                )
        return None

    def drain_injections(self) -> str:
        """Drain and clear _pending buffer, for request_decorator to inject into <system-reminder>.

        Returns ``"\\n".join(_pending)``; returns ``""`` if buffer is empty.
        """
        if not self._pending:
            return ""
        result = "\n".join(self._pending)
        self._pending.clear()
        return result

    def close(self) -> None:
        """Brief join of background threads (each waits at most _DEFAULT_CLOSE_TIMEOUT seconds), non-blocking on exit."""
        with self._threads_lock:
            threads = list(self._threads)
        for t in threads:
            try:
                t.join(timeout=_DEFAULT_CLOSE_TIMEOUT)
            except Exception as exc:  # noqa: BLE001
                self._logger.debug("close: thread join error (ignored): %s", exc)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _should_fire(self, rule: HookRule, context: dict) -> bool:  # type: ignore[type-arg]
        """Check condition match + once not yet fired."""
        if not evaluate(rule.condition, context):
            return False
        if rule.once and id(rule) in self._fired_once:
            return False
        return True

    def _mark_once(self, rule: HookRule) -> None:
        """If rule.once=True, mark as fired."""
        if rule.once:
            self._fired_once.add(id(rule))

    def _execute_action_soft(
        self,
        rule: HookRule,
        context: dict,  # type: ignore[type-arg]
    ) -> None:
        """Execute rule.action synchronously, fully softened (try/except → log, no raise)."""
        try:
            self._dispatch_action(rule.action, context)
            self._mark_once(rule)
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("fire: action raised exception (softened): %s", exc)

    def _fire_in_background(
        self,
        rule: HookRule,
        context: dict,  # type: ignore[type-arg]
    ) -> None:
        """Dispatch action to a daemon thread (fire-and-forget), tracking the thread for close() join."""

        def _run() -> None:
            try:
                self._dispatch_action(rule.action, context)
                self._mark_once(rule)
            except Exception as exc:  # noqa: BLE001
                self._logger.warning(
                    "fire[background]: action raised exception (softened): %s", exc
                )

        t = threading.Thread(target=_run, daemon=True)
        with self._threads_lock:
            self._threads.append(t)
        t.start()

    def _dispatch_action(
        self,
        action: object,
        context: dict,  # type: ignore[type-arg]
    ) -> None:
        """Dispatch by action type, handling PromptAction output (append to _pending)."""
        if isinstance(action, ShellAction):
            _actions.run_shell(action, context)
        elif isinstance(action, PromptAction):
            text = _actions.inject_prompt(action, context)
            self._pending.append(text)
        elif isinstance(action, HttpAction):
            _actions.call_http(action, context)
        elif isinstance(action, SubAgentAction):
            _actions.run_subagent_action(action, context, manager=self._agents_manager)
        else:
            self._logger.warning("dispatch: unknown action type %s", type(action))

    def _dispatch_action_for_pretool(
        self,
        rule: HookRule,
        context: dict,  # type: ignore[type-arg]
    ) -> Optional[str]:
        """Execute rule.action in the pretool context.

        - ShellAction: return denial reason string (exit 2) or None (pass-through / fail-open).
        - Other actions: execute side effects (prompt → _pending), return None (no interception).
        """
        action = rule.action
        if isinstance(action, ShellAction):
            return self._run_shell_for_pretool(action, context)  # type: ignore[arg-type]
        else:
            # Non-shell actions: side-effect only, cannot block
            self._dispatch_action(action, context)
            return None

    def _run_shell_for_pretool(
        self,
        action: ShellAction,
        context: dict,  # type: ignore[type-arg]
    ) -> Optional[str]:
        """Execute ShellAction and decide whether to intercept based on exit code.

        - exit_code == 2 → return denial reason (stderr preferred, fall back to stdout)
        - exit_code == 0 → None (no interception, continue)
        - timed_out / None (run_shell failed) / other exit_code → fail-open (None) + log
        """
        result = _actions.run_shell(action, context)

        if result is None:
            self._logger.warning(
                "pretool: run_shell returned None (fail-open): command=%r",
                action.command,
            )
            return None

        if result.timed_out:
            self._logger.warning(
                "pretool: shell timed out (fail-open): command=%r",
                action.command,
            )
            return None

        if result.exit_code == 2:
            # Deny: stderr preferred; fall back to stdout; strip whitespace
            reason = (result.stderr or result.stdout).strip()
            return reason if reason else "blocked by hook"

        if result.exit_code == 0:
            return None  # pass-through

        # Any other exit code (e.g. 1, 127) → fail-open
        self._logger.warning(
            "pretool: unexpected exit_code=%d (fail-open): command=%r",
            result.exit_code,
            action.command,
        )
        return None
