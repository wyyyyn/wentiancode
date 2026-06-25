"""v0.6 · C34 · F46 (task T74)

Five-layer permission pipeline orchestration — the agent-orchestration-layer
glue that turns the four pure leaf modules (blacklist / sandbox / rules / mode
fallback) into a single short-circuiting decision for one tool call.

:meth:`PermissionPipeline.decide` runs, *before* a tool actually executes:

1. **Blacklist** (command-execution only, N10 non-disableable): ``check_command``
   hits → immediately ``Decision(DENY, BLACKLIST)`` short-circuits; non-command
   category **skips** this layer.
2. **Sandbox** (file categories read-only / file-write only): per-path
   ``check_path``, any escape from project root → immediately
   ``Decision(DENY, SANDBOX)`` short-circuits; command category **skips** this layer.
3. **Rules engine**: ``target = command`` (command category) or project-relative
   path (file category); ``settings.rules.match`` → ALLOW→``Decision(ALLOW, RULE)``,
   DENY→ ``Decision(DENY, RULE)``, None→continue.
4. **Mode fallback**: ``mode_fallback(mode, category)`` → ``Decision(ALLOW, MODE)``
   or ``Decision(ASK, MODE)`` (ASK is passed to layer-5 human-in-the-loop gate,
   handled by caller).

Any layer yielding Allow/Deny short-circuits; skipped layers are treated as
"not intercepted, continue to next layer" (F46/AC48).
Mode fallback layer range is strictly {ALLOW, ASK}—Deny can only come from
blacklist / sandbox / explicit deny rules / human-in-the-loop (F46).

**Security default (N16/AC55):** ``decide``'s ``category`` argument is determined
by the caller; but if the caller declares command-execution category but cannot
provide a parseable ``command`` (``None`` / blank), this layer **never silently
allows**—neither enters blacklist check (no string to check) nor rules check
(no target to match), but instead treats it as a side-effect tool and falls
through to mode fallback (``ASK`` or in bypass mode still falls back to ``ASK``
rather than ``ALLOW``).
``decide`` always returns a ``Decision`` (one of three states), never returns
``None`` / silently.

Layering rule: only import stdlib and :mod:`wentian.permissions.*` sibling
modules—never touch any backend SDK / terminal UI / provider / agent / tools.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wentian.permissions.blacklist import check_command
from wentian.permissions.decision import (
    Category,
    Decision,
    Mode,
    Source,
    Verdict,
)
from wentian.permissions.modes import mode_fallback
from wentian.permissions.sandbox import check_path
from wentian.permissions.settings import Settings

__all__ = ["PermissionPipeline"]

#: File-category tools (go through sandbox + path target rule matching).
_FILE_CATEGORIES = (Category.READ_ONLY, Category.FILE_WRITE)


@dataclass
class PermissionPipeline:
    """Five-layer decision pipeline (layers 1-4 short-circuit orchestrated here; layer 5 human-in-the-loop left to the gate)."""

    project_root: Path
    settings: Settings

    def decide(
        self,
        *,
        friendly: str,
        category: Category,
        mode: Mode,
        command: str | None,
        paths: tuple[str, ...],
    ) -> Decision:
        """Return an Allow / Deny / Ask decision for a single tool call (layer-by-layer short-circuit, see module docs)."""
        is_command = category is Category.COMMAND_EXEC
        has_command = bool(command and command.strip())

        # --- Layer 1: Blacklist (command-execution only; skip if no parseable command — falls through to security default) ---
        if is_command and has_command:
            hit = check_command(command)  # type: ignore[arg-type]
            if hit is not None:
                return hit

        # --- Layer 2: Sandbox (file categories only; any path escape short-circuits to Deny) ---
        if category in _FILE_CATEGORIES:
            for path in paths:
                hit = check_path(path, self.project_root)
                if hit is not None:
                    return hit

        # --- Layer 3: Rules engine ---
        #
        # Security default (N16): command-execution category but command is not parseable,
        # **skip rules check**—no trusted target to match; never let an allow-all rule
        # pass through an unparseable command; fall straight to fallback.
        if not (is_command and not has_command):
            rule_decision = self._match_rules(
                friendly=friendly,
                is_command=is_command,
                command=command,
                paths=paths,
            )
            if rule_decision is not None:
                return rule_decision

        # --- Layer 4: Mode fallback (range strictly {ALLOW, ASK}) ---
        #
        # Security default (N16): command-execution category but command is not parseable,
        # treat as side-effect tool—never silently ALLOW (even in bypass mode falls back
        # to ASK for human-in-the-loop).
        if is_command and not has_command:
            return Decision(
                verdict=Verdict.ASK,
                source=Source.MODE,
                reason="Cannot parse the command to be executed; treating as a side-effect tool per security default, manual confirmation required.",
            )

        verdict = mode_fallback(mode, category)
        return Decision(verdict=verdict, source=Source.MODE)

    # ------------------------------------------------------------------
    # Layer 3 internals: build target and make decision (command string / file-relative path).
    # ------------------------------------------------------------------
    def _match_rules(
        self,
        *,
        friendly: str,
        is_command: bool,
        command: str | None,
        paths: tuple[str, ...],
    ) -> Decision | None:
        """Rules layer decision: hit ALLOW/DENY → Decision; no hit → None."""
        if is_command:
            target = command or ""
            verdict = self.settings.rules.match(
                friendly=friendly, target=target, is_path=False
            )
            return self._rule_verdict_to_decision(verdict, target=target)

        # File category: use project-relative path as target for each path; deny takes
        # priority (same-layer RuleSet already guarantees deny before allow); return on
        # first path hit.
        allow_hit: Decision | None = None
        for path in paths:
            target = self._project_relative(path)
            verdict = self.settings.rules.match(
                friendly=friendly, target=target, is_path=True
            )
            if verdict is Verdict.DENY:
                return self._rule_verdict_to_decision(verdict, target=target)
            if verdict is Verdict.ALLOW and allow_hit is None:
                allow_hit = self._rule_verdict_to_decision(verdict, target=target)
        return allow_hit

    @staticmethod
    def _rule_verdict_to_decision(
        verdict: Verdict | None, *, target: str
    ) -> Decision | None:
        if verdict is Verdict.ALLOW:
            return Decision(verdict=Verdict.ALLOW, source=Source.RULE)
        if verdict is Verdict.DENY:
            return Decision(
                verdict=Verdict.DENY,
                source=Source.RULE,
                reason=f"Target {target!r} was blocked by an explicit deny rule.",
            )
        return None

    def _project_relative(self, path: str) -> str:
        """Normalize path to a POSIX path relative to the project root (used for rule glob matching).

        Escaped / non-relativizable paths return the raw absolute path string—at this
        point they have already been short-circuited to Deny by the sandbox layer and
        will not reach the rules layer; this is a robustness fallback only, never grants
        access.
        """
        target = Path(path)
        if not target.is_absolute():
            target = self.project_root / target
        root = self.project_root.resolve()
        resolved = target.resolve() if target.exists() else target
        try:
            return resolved.relative_to(root).as_posix()
        except ValueError:
            return str(resolved)
