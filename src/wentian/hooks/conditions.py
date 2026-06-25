"""v0.12 · C95 · F80 (task T119) — condition evaluation leaf.

Public interface: evaluate(condition, context) -> bool

Layering rule (N41): leaf — only import wentian.textmatch + wentian.hooks.spec (+ stdlib).
Zero rich / prompt_toolkit / backend SDK / agent / repl / providers / tools imports.
"""

from __future__ import annotations

from wentian.hooks.spec import Condition, Match
from wentian.textmatch import match_one

__all__ = ["evaluate"]


def evaluate(condition: Condition | None, context: dict) -> bool:  # type: ignore[type-arg]
    """Evaluate condition, return whether it matches.

    Rules:
    - ``condition is None`` or ``condition.clauses`` is empty → ``True`` (always triggers unconditionally).
    - Otherwise for each ``Clause`` take ``context.get(field, "")`` (missing fields treated as empty string),
      call ``match_one`` with ``pattern`` to evaluate;
      reduce by ``condition.match``:
        - ``Match.ALL`` → ``True`` only if all clauses match
        - ``Match.ANY`` → ``True`` if any clause matches
    """
    if condition is None or not condition.clauses:
        return True

    results = (
        match_one(clause.pattern, str(context.get(clause.field, "")))
        for clause in condition.clauses
    )

    if condition.match is Match.ALL:
        return all(results)
    else:  # Match.ANY
        return any(results)
