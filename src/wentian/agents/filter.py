"""v0.13 · C111 · F97/N52 — three-layer filtering + nesting protection.

Pure-function leaf that computes the effective tool set for a delegated
sub-agent.  Three concentric filters are applied **in order**:

1. **Global forbid** — strip tools that must never reach a sub-agent
   (default: ``{"Agent"}``, preventing unbounded nesting).
2. **Role allow/deny** — intersection with a whitelist (if given), then
   subtraction of a deny list.
3. **Background filter** — when the agent runs unattended, keep only
   ``READ_ONLY`` tools and those explicitly opt-in via ``background_allow``.
   A tool whose category is unknown **and** not in ``background_allow`` is
   filtered out (safe default: never expose a possibly-side-effecting tool to
   an unattended background agent).

Layering rule: pure leaf module — stdlib only + ``wentian.permissions.decision.Category``.
No imports from repl / cli / agent / providers / tools.
"""

from __future__ import annotations

from wentian.permissions.decision import Category

__all__ = ["resolve_allowed_tools"]


def resolve_allowed_tools(
    all_tools: set[str] | frozenset[str],
    role_allow: tuple[str, ...] | None,
    role_deny: tuple[str, ...],
    *,
    background: bool = False,
    background_allow: tuple[str, ...] = (),
    tool_categories: dict[str, Category] | None = None,
    globally_forbidden: frozenset[str] = frozenset({"Agent"}),
) -> frozenset[str]:
    """Compute the effective tool set for a sub-agent.

    Parameters
    ----------
    all_tools:
        Full universe of tool names available in the current session.
    role_allow:
        Whitelist for this agent role.  ``None`` means *no narrowing* (all
        tools pass through to the next layer).  An empty tuple means *nothing
        is allowed* after this layer.
    role_deny:
        Tools explicitly blocked for this role (applied after *role_allow*).
    background:
        When ``True``, apply the background-safety filter (layer 3).
    background_allow:
        Tools that are explicitly permitted for background execution even if
        they are not ``READ_ONLY``.
    tool_categories:
        Mapping of tool name → :class:`~wentian.permissions.decision.Category`.
        Used by the background filter; ``None`` means no category info is
        available (every non-``background_allow`` tool is filtered out).
    globally_forbidden:
        Names that are **always** removed first, regardless of any whitelist.
        Defaults to ``frozenset({"Agent"})`` to prevent unbounded nesting.

    Returns
    -------
    frozenset[str]
        The resolved, immutable set of tool names the sub-agent may use.

    Notes
    -----
    Set-operation order (exact, do not reorder):

    1. ``base = set(all_tools) - globally_forbidden``
    2. if ``role_allow is not None``: ``base &= set(role_allow)``
    3. ``base -= set(role_deny)``
    4. if ``background``: keep a tool iff it is in ``background_allow`` OR
       its category in ``tool_categories`` is ``Category.READ_ONLY``.
    """
    # Step 1 — global forbid (always first; cannot be rescued by whitelist)
    base: set[str] = set(all_tools) - globally_forbidden

    # Step 2 — role whitelist (None = no narrowing)
    if role_allow is not None:
        base &= set(role_allow)

    # Step 3 — role deny list
    base -= set(role_deny)

    # Step 4 — background safety filter
    if background:
        bg_allow_set: frozenset[str] = frozenset(background_allow)
        categories: dict[str, Category] = tool_categories or {}

        def _keep(tool: str) -> bool:
            if tool in bg_allow_set:
                return True
            cat = categories.get(tool)
            return cat is Category.READ_ONLY

        base = {t for t in base if _keep(t)}

    return frozenset(base)
