"""v0.11 · C104 · F87/F89 (task T133) — SkillActivator: the single orchestration point for Skill activation.

This module lives in the **repl assembly layer** (not inside the ``skills/`` leaf package), so it
**can** import ``wentian.agent`` / ``wentian.skills`` / ``wentian.providers`` — the nested
``AgentLoop`` orchestration for isolated sub-conversations is intentionally placed here, keeping
the ``skills`` package free of agent/provider/repl dependencies. It does **not import**
``wentian.repl`` (to avoid circular imports) — the main session's messages / system / provider /
tool_registry / executor are all obtained via handles and callbacks injected at construction time.

The ``load_skill`` tool and ``/<name>`` commands share this single entry point:

- **SHARED**: ``render_body(skill.body, args)`` → add ``(name, rendered,
  allowed_tools)`` to the active set (deduplicated by name; re-activation refreshes args) → return
  confirmation string. The body is fed live each round to the reminder channel via
  ``active_bodies()``; the allowlist is fed to AgentLoop assembly via ``allowed_tools()``.
- **ISOLATED**: ``asyncio.run`` a brand-new ``AgentLoop`` in an **independent worker thread** to
  run a nested sub-conversation (last ``skill.history`` messages from main history + sub system =
  main system + Skill body + sub tools = that Skill's allowlist + sub model =
  ``skill.model or current``) → collect the **last assistant text** of the sub-conversation as the
  return value. **Not added to the active set**, does not touch main history / main allowlist.

The worker thread starts its own event loop, avoiding nested-asyncio conflicts with the main
``_chat_once`` ``asyncio.run`` call (safe regardless of whether ``activate`` is triggered from an
executor worker thread or the REPL main thread).
"""

from __future__ import annotations

import asyncio
import copy
import threading
from collections.abc import Callable

from wentian.agent.events import AgentDone
from wentian.agent.loop import AgentLoop
from wentian.providers.base import Message
from wentian.skills.base import Skill, SkillMode
from wentian.skills.loader import render_body
from wentian.skills.registry import SkillRegistry

__all__ = ["SkillActivator"]

# ``load_skill`` is always merged into the narrowed allowlist — even when the active set narrows
# the tools, the model can always continue loading / switching Skills (system-level exemption,
# see plan C103/C104).
_LOAD_SKILL = "load_skill"


class SkillActivator:
    """Single orchestration point for Skill activation (SHARED → active set / ISOLATED → sub-conversation)."""

    def __init__(
        self,
        registry: SkillRegistry,
        *,
        provider,
        tool_registry,
        executor,
        get_main_messages: Callable[[], list[Message]],
        get_main_system: Callable[[], str],
        loop_factory: Callable[..., AgentLoop] | None = None,
    ) -> None:
        self._registry = registry
        self._provider = provider
        self._tool_registry = tool_registry
        self._executor = executor
        self._get_main_messages = get_main_messages
        self._get_main_system = get_main_system
        self._loop_factory = loop_factory
        # Active set (SHARED only): insertion order = activation order, deduplicated by name
        # (re-activation refreshes args).
        # name -> (rendered_body, allowed_tools)
        self._active: dict[str, tuple[str, tuple[str, ...] | None]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def activate(self, name: str, args: str) -> str:
        """Unified entry point for the ``load_skill`` tool and ``/<name>`` commands.

        Unknown name → returns a clear error string (does not raise). SHARED → adds to active set
        + returns confirmation string. ISOLATED → runs sub-conversation, returns the last assistant
        text of the sub-conversation (verbatim).
        """
        skill = self._registry.get(name)
        if skill is None:
            return f"Skill `{name}` not found. Please check the name (use /skills to view the loaded list)."

        if skill.mode is SkillMode.ISOLATED:
            return self._run_isolated(skill, args)

        # SHARED: render body → add to active set (deduplicated by name, refresh args) → confirmation string.
        rendered = render_body(skill.body, args)
        # Delete then re-insert to ensure that on "refresh" the entry moves back to the end of
        # activation order (dict preserves insertion order).
        self._active.pop(name, None)
        self._active[name] = (rendered, skill.allowed_tools)
        return f"Skill `{name}` activated. Instructions have been injected into context."

    def active_bodies(self) -> list[tuple[str, str]]:
        """``[(name, rendered_body), ...]``, in activation order — read live each round by reminders."""
        return [(name, body) for name, (body, _tools) in self._active.items()]

    def allowed_tools(self) -> frozenset[str] | None:
        """Union of active set allowlists ∪ ``{load_skill}``; empty set / any unrestricted → None.

        - Active set empty → None (no narrowing, full tool set).
        - Any active Skill's ``allowed_tools`` is None or empty tuple → None
          (one unrestricted Skill means no narrowing).
        - Otherwise → ``frozenset(union of all allowlists) | {load_skill}``.
        """
        if not self._active:
            return None
        union: set[str] = set()
        for _name, (_body, tools) in self._active.items():
            if not tools:  # None or empty tuple
                return None
            union.update(tools)
        union.add(_LOAD_SKILL)
        return frozenset(union)

    def clear(self) -> None:
        """Clear the active set (called by ``/clear`` // ``session new``)."""
        self._active.clear()

    def get(self, name: str) -> Skill | None:
        """Look up a Skill by name in the current registry (returns None if absent) — for slash
        handlers to determine SHARED / ISOLATED without directly importing registry."""
        return self._registry.get(name)

    def list_skills(self) -> list[Skill]:
        """Return all Skills in the current registry (sorted by name ascending) — data source for the ``/skills`` list."""
        return self._registry.list()

    def set_registry(self, registry: SkillRegistry) -> None:
        """Switch the underlying Skill registry (called by ``/skills reload``).

        v0.11 · C107b (task T134b) — after a successful reload, point the activator to the newly
        discovered registry; the caller should separately call :meth:`clear` to discard the stale
        active set. ``activate`` / ``get`` / ``list_skills`` will subsequently read the new registry.
        """
        self._registry = registry

    # ------------------------------------------------------------------
    # ISOLATED sub-conversation
    # ------------------------------------------------------------------

    def _run_isolated(self, skill: Skill, args: str) -> str:
        """Run a nested AgentLoop in an independent worker thread, returning the last assistant text of the sub-conversation.

        Sub-conversation seed = deep copy of the last ``skill.history`` messages from main history
        (main history is never modified); if the seed is empty or the last message is not from
        user, a trigger user message is appended (uses ``args`` if non-empty, otherwise
        ``f"execute {name}"`` ) — the sub-agent needs a user turn to act. Sub system = main
        system + ``# Skill: <name>\\n<rendered>``; sub tools allowlist = that Skill's
        ``allowed_tools`` (None ⇒ full tool set); sub model = ``skill.model or current``.

        The worker thread starts its own event loop (``asyncio.run``), avoiding conflicts with any
        existing event loop in the calling thread.
        """
        rendered = render_body(skill.body, args)

        # --- Sub-conversation seed history: deep copy of the last history messages from main history (main history is never modified) ---
        main_messages = self._get_main_messages()
        if skill.history > 0:
            tail = main_messages[-skill.history :]
        else:
            tail = []
        seed: list[Message] = copy.deepcopy(tail)

        # Sub-agent needs a user turn to act: empty or last message not from user → append trigger user message.
        if not seed or seed[-1].get("role") != "user":
            trigger = args if args else f"execute {skill.name}"
            seed.append({"role": "user", "content": trigger})

        # --- Sub system ---
        main_system = self._get_main_system() or ""
        sub_system = f"{main_system}\n\n# Skill: {skill.name}\n{rendered}"

        # --- Sub tools allowlist (same rules as SHARED, but for this single Skill only):
        #     allowed_tools None/empty ⇒ full tool set (no narrowing) ---
        sub_allowed: frozenset[str] | None
        if skill.allowed_tools:
            sub_allowed = frozenset(skill.allowed_tools) | {_LOAD_SKILL}
        else:
            sub_allowed = None

        # Note: tool declarations and model are determined by the assembly layer inside
        # loop_factory / provider; this orchestration is only responsible for driving the loop and
        # extracting the last assistant text. skill.model is passed through loop_factory (the
        # default factory uses the injected provider; model overrides are wired by the assembly layer).

        result: dict[str, str] = {"text": ""}

        def _worker() -> None:
            agent = self._build_loop(sub_allowed, skill)
            result["text"] = asyncio.run(self._drive(agent, seed, sub_system))

        thread = threading.Thread(target=_worker, name=f"skill-isolated-{skill.name}")
        thread.start()
        thread.join()  # Synchronous wait: worker has finished by the time activate returns (no leak).
        return result["text"]

    def _build_loop(
        self, sub_allowed: frozenset[str] | None, skill: Skill
    ) -> AgentLoop:
        """Build an AgentLoop for the sub-conversation (tests can inject a fake loop via loop_factory).

        No tool_registry / executor (pure conversational sub-loop) ⇒ ``max_rounds=1``: if the
        sub-model still requests tools, the first round hits the MAX_ROUNDS brake and only text is
        stored (isomorphic to the repl's missing-executor decision). With registry/executor ⇒
        allow multiple rounds, sub-tools narrowed by ``sub_allowed``.
        """
        if self._loop_factory is not None:
            return self._loop_factory(
                self._provider,
                registry=self._tool_registry,
                executor=self._executor,
                allowed_tools=sub_allowed,
                skill=skill,
            )

        tools_enabled = self._tool_registry is not None and self._executor is not None
        if tools_enabled:
            return AgentLoop(
                self._provider,
                registry=self._tool_registry,
                executor=self._executor,
                allowed_tools=sub_allowed,
            )
        # Pure conversational sub-loop: declaration ≠ execution; max_rounds=1 makes any tool request hit the brake on the first round.
        return AgentLoop(
            self._provider,
            registry=None,
            executor=None,
            max_rounds=1,
            allowed_tools=None,
        )

    @staticmethod
    async def _drive(agent: AgentLoop, seed: list[Message], system: str) -> str:
        """Drive the sub-conversation event stream and extract the last assistant text.

        Mirrors repl ``_consume_agent``: consumes the async event stream from ``agent.run(...)``,
        capturing ``AgentDone`` as the terminal value — ``AgentDone.text`` is the same text
        written to the last assistant message on the loop COMPLETED path
        (``messages.append({"role":"assistant", "content": round_result.text})``). The
        sub-conversation does not touch the main session, main active set, or main allowlist — it
        only mutates the local ``seed`` in place (already a deep copy), and only the text is
        extracted as the result.
        """
        final: AgentDone | None = None
        async for ev in agent.run(seed, system=system, tools=None):
            if isinstance(ev, AgentDone):
                final = ev
        return final.text if final is not None else ""
