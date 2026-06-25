"""v0.13 · C112 · F94/F95/F96/F100 (task T141) — Sub-Agent executor run_subagent.

The **execution heart** of Sub-Agent delegation: assembles a **fully isolated**
:class:`~wentian.agent.loop.AgentLoop`, runs it to completion in a dedicated worker
thread, collecting the final assistant text + usage + stop reason into
:class:`SubAgentResult`.

This module lives in the **agents orchestration layer** (not beside the pure-leaf
``filter`` / ``spec``) — it **may** import ``wentian.agent`` / ``wentian.providers`` /
``wentian.permission_gate``, because the assembly of nested ``AgentLoop`` is
intentionally pushed down here. It does **not import** ``wentian.repl`` /
``wentian.cli`` (to avoid circular imports + maintain concurrent-write discipline);
the main conversation's provider config / tool registry / executor / permission
pipeline are all obtained via **injected parameters**.

Structure mirrors v0.11 ``skill_activator._run_isolated``: worker ``threading.Thread`` +
``asyncio.run(self._drive(...))`` avoids nested event loop conflicts with the caller's
thread; ``_build_loop`` leaves a ``loop_factory`` injection seam for testing; ``_drive``
asynchronously consumes ``agent.run(...)`` and captures the final ``AgentDone`` to get
``.text``.

Isolation rule (N53): **every call** creates a new provider instance, new :class:`Mode`,
new permission gate, new messages — never share mutable state across calls. Each
sub-agent gets its own gate, never reusing the main REPL's.

Error softening (N54): the entire drive is wrapped in try/except, and checks the
``AgentDone`` stop reason; MAX_ROUNDS / STREAM_ERROR / UNKNOWN_TOOL_LOOP / any
exception → converted to structured ``SubAgentResult``, **never letting exceptions
escape run_subagent**.

Nesting protection: ``resolve_allowed_tools``'s global deny strips ``"Agent"`` by
default; plus a depth guard (``depth >= 1`` refuses to spawn again) — double
protection.
"""

from __future__ import annotations

import asyncio
import dataclasses
import threading
from collections.abc import Callable
from dataclasses import dataclass

from wentian.agent.events import AgentDone, StopReason
from wentian.agent.loop import AgentLoop
from wentian.agents.filter import resolve_allowed_tools
from wentian.agents.spec import AgentDef
from wentian.config import ProviderConfig
from wentian.permissions.decision import Mode
from wentian.providers.base import Message, Usage

__all__ = ["SubAgentResult", "run_subagent"]

#: Sentinel that ``inherit`` alias maps to — keeps the parent conversation's model unchanged.
_INHERIT_SENTINEL = "__inherit__"

#: Sub-Agent maximum recursion depth: >=1 refuses to spawn again (double protection with global Agent deny).
_MAX_DEPTH = 1


@dataclass
class SubAgentResult:
    """Result of a single sub-agent delegation (text + token usage + stop reason string).

    ``stop_reason`` takes the ``name`` of :class:`~wentian.agent.events.StopReason`
    (``"COMPLETED"`` / ``"MAX_ROUNDS"`` / …), or ``"ERROR"`` for pre-assembly failures.
    """

    text: str
    usage: dict
    stop_reason: str


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_subagent(
    agent_def: AgentDef,
    prompt: str,
    *,
    base_provider_cfg: ProviderConfig,
    provider_factory: Callable[[ProviderConfig], object],
    registry: object | None,
    executor: object | None,
    pipeline: object | None,
    settings: object | None,
    model_aliases: dict[str, str],
    parent_messages: list[Message] | None = None,
    depth: int = 0,
    loop_factory: Callable[..., object] | None = None,
    background: bool = False,
    background_allow: tuple[str, ...] = (),
) -> SubAgentResult:
    """Assemble an isolated AgentLoop to run a sub-agent to completion and return a structured result.

    Flow (brief T141):

    1. **Model alias resolution**: ``inherit`` → keep ``base_provider_cfg.model``; maps to
       a concrete model → use it; not found in alias table → return early with ``ERROR``
       (**never start an empty loop**).
    2. **New provider instance**: ``dataclasses.replace(base_cfg, model=mapped)`` →
       ``provider_factory(cfg)`` (factory allows tests to inject a fake provider).
    3. **Isolated permissions**: new :class:`Mode` (from ``agent_def.permission_mode`` or
       safe default :data:`Mode.DEFAULT`) + new ``build_permission_gate`` — each sub-agent
       gets its own gate.
    4. **Allowed set**: ``resolve_allowed_tools(...)``, globally denying ``"Agent"``
       (nesting protection). definition → initial
       ``[{"role":"user","content":prompt}]``, system = ``agent_def.body``; fork →
       ``parent_messages + [user prompt]``.
    5. **Depth guard**: ``depth >= 1`` → refuse to spawn, return structured ``ERROR``.
    6. ``asyncio.run(_drive(...))`` inside worker thread runs to completion, captures
       ``AgentDone``.
    7. **Error softening**: exceptions + MAX_ROUNDS/STREAM_ERROR/UNKNOWN_TOOL_LOOP →
       structured result, never letting exceptions escape.
    """
    # --- 5. Depth guard (double protection with global Agent deny, checked first, never spawn) ---
    # Note: this is intentionally double-protected with resolve_allowed_tools's global deny of
    # "Agent" — sub-agents via normal execution paths will never see the Agent tool (already
    # stripped by global deny), so depth>=1 is only reachable in direct-call / test scenarios;
    # do not assume this branch is a production hot path in future refactors.
    if depth >= _MAX_DEPTH:
        return SubAgentResult(
            text=(
                f"Sub-agent `{agent_def.name}` has reached the maximum delegation depth"
                f" (depth={depth}), refusing to spawn again to prevent unbounded nesting."
            ),
            usage={},
            stop_reason="ERROR",
        )

    # --- 1. Model alias resolution (unresolvable → return error early, never start an empty loop) ---
    mapped, alias_error = _resolve_model(
        agent_def.model, base_provider_cfg, model_aliases
    )
    if alias_error is not None:
        return SubAgentResult(text=alias_error, usage={}, stop_reason="ERROR")

    # --- 2. New provider instance (dataclasses.replace + injection factory) ---
    cfg = dataclasses.replace(base_provider_cfg, model=mapped)
    provider = provider_factory(cfg)

    # --- 4. Initial messages + sub-system (routed by definition / fork) ---
    seed, system = _build_seed(agent_def, prompt, parent_messages)

    # --- 4. Allowed set (globally deny Agent) + F97 third-layer passthrough ---
    all_tools = _all_tool_names(registry)
    # Build tool_categories mapping (name → Category) for use by the background filter.
    # Returns {} when registry is None or tools lack a category attribute; background filter
    # will then apply safe default behavior and filter out all tools not listed in background_allow.
    tool_categories = _build_tool_categories(registry)
    allowed = resolve_allowed_tools(
        all_tools,
        role_allow=agent_def.tools,
        role_deny=agent_def.disallowed_tools,
        background=background,
        background_allow=background_allow,
        tool_categories=tool_categories,
    )

    # --- 3. Isolated permission gate (each sub-agent's own Mode + gate) ---
    gate = _build_isolated_gate(agent_def, registry, pipeline, settings)

    # --- 6+7. Worker thread runs to completion + error softening ---
    holder: dict[str, SubAgentResult] = {}

    def _worker() -> None:
        try:
            agent = _build_loop(
                provider,
                registry=registry,
                executor=executor,
                allowed=allowed,
                gate=gate,
                max_turns=agent_def.max_turns,
                loop_factory=loop_factory,
            )
            holder["result"] = asyncio.run(_drive(agent, seed, system))
        except Exception as exc:  # noqa: BLE001 — N54: exceptions must never escape run_subagent
            holder["result"] = SubAgentResult(
                text=f"Stopped due to EXCEPTION: {exc}",
                usage={},
                stop_reason="STREAM_ERROR",
            )

    # daemon=True: when the executor tool times out and abandons the foreground sub-agent,
    # orphaned threads won't block process exit.
    # thread.join() is still kept: the normal inline call path synchronously waits for the
    # worker to finish (no leak).
    thread = threading.Thread(
        target=_worker, name=f"subagent-{agent_def.name}", daemon=True
    )
    thread.start()
    thread.join()  # Synchronous wait: worker has finished by the time this returns (no leak).

    return holder.get(
        "result",
        SubAgentResult(text="Sub-agent produced no result.", usage={}, stop_reason="ERROR"),
    )


# ---------------------------------------------------------------------------
# Private assembly steps
# ---------------------------------------------------------------------------


def _resolve_model(
    alias: str,
    base_cfg: ProviderConfig,
    model_aliases: dict[str, str],
) -> tuple[str, str | None]:
    """Resolve model alias → ``(mapped_model, error_msg)``.

    - ``inherit`` (mapped to sentinel ``__inherit__``) → keep ``base_cfg.model``, no error.
    - Alias found in table and not sentinel → use mapped target, no error.
    - Alias **not in table** → ``(base_cfg.model, "alias <x> unresolvable…")`` — caller
      uses this to return early, never starting an empty loop.
    """
    if alias not in model_aliases:
        return base_cfg.model, (
            f"Alias {alias} is unresolvable: not defined in model_aliases"
            f" (available: {', '.join(sorted(model_aliases))})."
        )
    mapped = model_aliases[alias]
    if mapped == _INHERIT_SENTINEL:
        return base_cfg.model, None
    return mapped, None


def _build_seed(
    agent_def: AgentDef,
    prompt: str,
    parent_messages: list[Message] | None,
) -> tuple[list[Message], str]:
    """Build the initial messages + sub-system for a sub-conversation (routed by definition / fork).

    **The dispatch type is carried by ``parent_messages``** (``AgentDef`` itself has no
    type field — type is a dispatch-time decision, determined by the caller via
    :class:`~wentian.agents.spec.AgentType` whether to pass parent history):

    - **definition** (``parent_messages`` is None/empty): initial =
      ``[{"role":"user","content":prompt}]``, system = ``agent_def.body``.
    - **fork** (``parent_messages`` is non-empty): initial = ``shallow copy of
      parent_messages + [user prompt]`` (never mutating parent history in-place),
      system = ``agent_def.body``.
    """
    user_turn: Message = {"role": "user", "content": prompt}
    if parent_messages:
        # Shallow-copy each message + append triggering user turn: parent history is never mutated in-place (isolation).
        seed: list[Message] = [dict(m) for m in parent_messages]
        seed.append(user_turn)
    else:
        seed = [user_turn]
    return seed, agent_def.body


def _all_tool_names(registry: object | None) -> set[str]:
    """Retrieve all tool names from the registry (``names()`` duck-type method); no registry → empty set.

    When the set is empty, ``resolve_allowed_tools`` still applies the global deny (no
    side effects), and the allowed set is empty — consistent with a "pure-conversation
    sub-loop" (no tools available).
    """
    if registry is None:
        return set()
    names = getattr(registry, "names", None)
    if callable(names):
        return set(names())
    return set()


def _build_tool_categories(registry: object | None) -> dict:
    """Build the tool name → Category mapping for use by the F97 third-layer (background filter).

    Duck-calls ``registry.names()`` + ``registry.get(name)``; the ``.category`` attribute
    on ``Tool`` is a :class:`~wentian.permissions.decision.Category` member.
    When registry is None, a tool lacks a category, or get() returns None, that tool is
    excluded from the mapping — the background filter's safe default will filter it out
    (unless explicitly listed in ``background_allow``).
    """
    if registry is None:
        return {}
    names_fn = getattr(registry, "names", None)
    get_fn = getattr(registry, "get", None)
    if not callable(names_fn) or not callable(get_fn):
        return {}
    result: dict = {}
    for name in names_fn():
        tool = get_fn(name)
        if tool is not None and hasattr(tool, "category"):
            result[name] = tool.category
    return result


def _build_isolated_gate(
    agent_def: AgentDef,
    registry: object | None,
    pipeline: object | None,
    settings: object | None,  # noqa: ARG001 — reserved: future per-agent settings override
):
    """Build the permission gate **exclusive to this sub-agent**, or None (no pipeline ⇒ no gate).

    Each sub-agent gets its own :class:`Mode` and ``build_permission_gate`` closure —
    never reusing the main REPL's gate (isolation rule). Sub-agents are unattended: the
    ``ask`` callback uses **safe default deny** (any ASK decision is always Deny, never
    blocking to wait for a human). ``get_mode`` returns the newly created fixed Mode
    (from ``permission_mode`` or :data:`Mode.DEFAULT`).
    """
    if pipeline is None:
        return None

    # Assembly-layer import (permission_gate is cross-layer, may import permissions+tools+ui).
    from wentian.permission_gate import build_permission_gate
    from wentian.ui.confirm import Choice

    mode = _resolve_mode(agent_def.permission_mode)

    def get_mode() -> Mode:
        return mode

    async def ask(_call, _decision) -> Choice:
        # Unattended sub-agent: all ASK decisions use safe default deny (never block waiting for human confirmation).
        return Choice.DENY

    return build_permission_gate(
        pipeline=pipeline,
        registry=registry,
        ask=ask,
        get_mode=get_mode,
        on_allow_always=None,  # Sub-agents never persist allow-always rules.
    )


def _resolve_mode(permission_mode: str | None) -> Mode:
    """Parse ``agent_def.permission_mode`` (config string) into :class:`Mode`.

    Missing / unrecognized → safe default :data:`Mode.DEFAULT`.
    """
    if permission_mode is None:
        return Mode.DEFAULT
    try:
        return Mode(permission_mode)
    except ValueError:
        return Mode.DEFAULT


def _build_loop(
    provider: object,
    *,
    registry: object | None,
    executor: object | None,
    allowed: frozenset[str],
    gate,
    max_turns: int | None,
    loop_factory: Callable[..., object] | None,
):
    """Assemble an isolated AgentLoop (tests can inject a fake drive loop via ``loop_factory``).

    ``max_turns`` missing → uses AgentLoop's default ``max_rounds=20``. No
    registry/executor ⇒ pure-conversation sub-loop (declaration ≠ execution; any tool
    request will stop at max_rounds).
    """
    kwargs: dict = {
        "registry": registry,
        "executor": executor,
        "allowed_tools": allowed,
        "permission_gate": gate,
    }
    if max_turns is not None:
        kwargs["max_rounds"] = max_turns

    if loop_factory is not None:
        return loop_factory(provider, **kwargs)
    return AgentLoop(provider, **kwargs)


async def _drive(agent, seed: list[Message], system: str) -> SubAgentResult:
    """Drive the sub-conversation event stream, converting the final ``AgentDone`` to :class:`SubAgentResult` (with softening).

    Mirrors ``skill_activator._drive`` + repl ``_consume_agent``: asynchronously consumes
    ``agent.run(...)``, captures ``AgentDone``. ``AgentDone.text`` is the same text
    written to the last assistant message by the loop. If stop reason is not COMPLETED
    (MAX_ROUNDS/STREAM_ERROR/UNKNOWN_TOOL_LOOP/USER_CANCELLED) → text is prefixed with
    "Stopped due to <REASON>…" (N54).
    """
    final: AgentDone | None = None
    async for ev in agent.run(seed, system=system, tools=None):
        if isinstance(ev, AgentDone):
            final = ev

    if final is None:
        # Event stream produced no AgentDone (exception swallowed by loop or empty stream) — apply error softening.
        return SubAgentResult(
            text="Sub-conversation did not terminate normally (no AgentDone event).",
            usage={},
            stop_reason="STREAM_ERROR",
        )

    reason = final.stop_reason
    usage_dict = _usage_to_dict(final.usage)

    if reason is StopReason.COMPLETED:
        return SubAgentResult(
            text=final.text, usage=usage_dict, stop_reason=reason.name
        )

    # Abnormal termination → soften into structured result (with stop reason + any partial text).
    partial = final.text or ""
    detail = f": {final.error}" if final.error else ""
    return SubAgentResult(
        text=f"Stopped due to {reason.name}{detail}. {partial}".rstrip(),
        usage=usage_dict,
        stop_reason=reason.name,
    )


def _usage_to_dict(usage: Usage | None) -> dict:
    """Convert :class:`~wentian.providers.base.Usage` to dict; None → ``{}``."""
    if usage is None:
        return {}
    return dataclasses.asdict(usage)
