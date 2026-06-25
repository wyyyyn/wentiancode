"""v0.13 · C114 · F91/F94/F95/F98② (task T143/FixB2) — unified Agent tool AgentTool.

Exposes **exactly one** ``Agent`` tool to the model (category COMMAND_EXEC, requires confirmation):
- ``type="definition"``: retrieves AgentDef by ``agent_type``, runs synchronously in **foreground** by default
  (calls manager.run_foreground); uses manager.submit for background when ``background=True``.
- ``type="fork"``: constructs a placeholder AgentDef, **always uses** manager.submit (AgentType.FORK),
  forced background, does not block the main conversation.
- Depth guard: when ``depth >= 1``, returns an error text immediately, **does not call** manager (N52 double safeguard).
- Role not found graceful degradation: agent_type not found in registry → returns clear error text, never crashes (N50).

Layering discipline: only imports agents.spec / agents.loader / agents.manager /
config / tools.base / permissions.decision + stdlib; **never imports** wentian.repl /
wentian.cli.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from wentian.agents.loader import AgentRegistry
from wentian.agents.spec import AgentDef, AgentType
from wentian.config import AgentsConfig
from wentian.permissions.decision import Category
from wentian.tools.base import Tool

__all__ = ["AgentTool"]

#: When depth >= this value, refuse to spawn a sub-Agent (N52 double safeguard, aligned with _MAX_DEPTH in runner.py).
_MAX_DEPTH = 1


class AgentTool(Tool):
    """Unified Agent tool — type routing (definition / fork) + nested depth interception.

    Constructor parameters are passed via **duck injection**, no direct dependency on REPL/CLI classes:

    Parameters
    ----------
    registry:
        AgentRegistry, looks up AgentDef by name.
    manager:
        BackgroundTaskManager (or duck-compatible object), provides ``run_foreground`` and ``submit``
        methods; definition foreground path uses run_foreground (F98②), background / fork uses submit.
    cfg:
        AgentsConfig, used to read foreground_timeout_s and other configuration.
    get_parent_messages:
        Optional callable, called on the fork path to retrieve parent conversation history (F95/AC120).
    """

    # ------------------------------------------------------------------ #
    # Tool ABC class attributes
    # ------------------------------------------------------------------ #

    name: str = "Agent"
    description: str = (
        "Delegate a task to a sub-Agent.\n"
        "- type='definition': launches an isolated sub-Agent by named role (agent_type),"
        " synchronous foreground by default; background=True switches to background.\n"
        "- type='fork': forks a sub-Agent reusing the current conversation context, always background,"
        " result will be injected back via <system-reminder> in the next turn."
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["definition", "fork"],
                "description": "Delegation path: definition (named role) or fork (inherit current context).",
            },
            "agent_type": {
                "type": "string",
                "description": "Role name (required for definition path; ignored for fork path).",
            },
            "prompt": {
                "type": "string",
                "description": "Task description to pass to the sub-Agent.",
            },
            "background": {
                "type": "boolean",
                "description": "Whether to execute in background (fork is always true; definition defaults to false).",
            },
        },
        "required": ["type", "prompt"],
    }
    category: Category = Category.COMMAND_EXEC
    friendly_name: str = "Agent"

    # ------------------------------------------------------------------ #
    # Constructor
    # ------------------------------------------------------------------ #

    def __init__(
        self,
        *,
        registry: AgentRegistry | None,
        manager: Any,
        cfg: AgentsConfig,
        get_parent_messages: Callable[[], list] | None = None,
    ) -> None:
        self._registry = registry
        self._manager = manager
        self._cfg = cfg
        self._get_parent_messages = get_parent_messages
        # margin: executor's per-tool join must not kill a foreground call;
        # manager's bounded wait is the real governor.
        self.timeout_s: float = cfg.foreground_timeout_s + 60.0

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #

    def run(self, args: dict, *, depth: int = 0) -> str:  # type: ignore[override]
        """Execute the Agent tool.

        Parameters
        ----------
        args:
            Tool call arguments, must include ``type`` and ``prompt``.
        depth:
            Call depth (0 = main conversation; >=1 = inside a sub-Agent). Intercepted immediately when depth >=1.
        """
        # ① Nesting interception — checked first, never spawns (N52 double safeguard).
        # Note: intentionally redundant with the global "Agent" ban in runner.py — in the normal sub-Agent
        # execution path the Agent tool is stripped by resolve_allowed_tools; depth>=1 is only reachable in direct/test scenarios.
        if depth >= _MAX_DEPTH:
            return "Sub-Agents are not allowed to nest-call the Agent tool (depth >= 1)."

        agent_type_str = args.get("type", "")
        prompt = args.get("prompt", "")

        if agent_type_str == "definition":
            return self._run_definition(args, prompt)
        elif agent_type_str == "fork":
            return self._run_fork(prompt)
        else:
            return f"Unknown type value '{agent_type_str}', must be 'definition' or 'fork'."

    # ------------------------------------------------------------------ #
    # Private routing methods
    # ------------------------------------------------------------------ #

    def _run_definition(self, args: dict, prompt: str) -> str:
        """definition path: look up registry → foreground manager.run_foreground or background manager.submit.

        - Role not found in registry → returns "No such role: <name>" (N50 graceful degradation, never crashes).
        - background=True → manager.submit, returns "Task id=X started" immediately.
        - Default foreground → manager.run_foreground (F98② bounded wait):
          - Completes within threshold → returns text.
          - Timeout auto-switches to background → returns "Switched to background id=X" (F98②).
        - manager=None + foreground/background → returns "agents not enabled" (N50 graceful degradation).
        """
        role_name = args.get("agent_type", "")
        agent_def: AgentDef | None = (
            self._registry.get(role_name) if self._registry is not None else None
        )
        if agent_def is None:
            return f"No such role: {role_name}"

        background: bool = bool(args.get("background", False))

        if background:
            if self._manager is None:
                return "agents not enabled, cannot dispatch background task"
            task_id = self._manager.submit(agent_def, prompt, background=True)
            return f"Task id={task_id} started"

        # Foreground synchronous — uses manager.run_foreground (F98② bounded wait, auto-switches to background on timeout).
        if self._manager is None:
            return "agents not enabled, cannot execute foreground sub-Agent task"
        text, task_id, backgrounded = self._manager.run_foreground(agent_def, prompt)
        if backgrounded:
            return f"Task id={task_id} switched to background (foreground timeout, continuing in background, result will be injected when done)"
        return text or ""

    def _run_fork(self, prompt: str) -> str:
        """fork path: constructs a placeholder AgentDef, always background manager.submit (AgentType.FORK, F95).

        v0.13 · T145 assembly: if get_parent_messages is injected, passes parent history via
        parent_messages to manager.submit → runner → run_subagent (F95/AC120).
        manager=None → returns "agents not enabled" (N50 graceful degradation).
        """
        if self._manager is None:
            return "agents not enabled, cannot dispatch fork sub-Agent"

        synthetic_def = AgentDef(
            name="fork",
            description="fork current context",
            body="",
        )

        extra_kw: dict[str, Any] = {}
        if self._get_parent_messages is not None:
            extra_kw["parent_messages"] = self._get_parent_messages()

        task_id = self._manager.submit(
            synthetic_def,
            prompt,
            agent_type=AgentType.FORK,
            **extra_kw,
        )
        return f"Task id={task_id} started"
