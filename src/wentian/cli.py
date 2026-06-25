"""CLI entry point for wentian.

Provides `wentian` and `wt` console scripts (both point to `app`).

Assembly:
- build_app() is a pure function: load config → pick provider → create store
  → wire session → Renderer → REPL.  No I/O side-effects except returning REPL.
- The typer command `main` calls build_app(...).run() and catches expected
  errors (ConfigError, FileNotFoundError) for friendly stderr output.
"""

from __future__ import annotations

import dataclasses
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console

import wentian
from wentian.config import ConfigError, load_config
from wentian.context.compactor import Compactor
from wentian.hooks.engine import HookEngine
from wentian.hooks.spec import HookEvent
from wentian.context.estimator import estimate_total
from wentian.mcp.manager import MCPManager
from wentian.memory.runner import MemoryRunner
from wentian.memory.store import MemoryStore
from wentian.permissions.pipeline import PermissionPipeline
from wentian.permissions.settings import load_settings
from wentian.providers.factory import create_provider
from wentian.prompt.instructions import load_project_instructions
from wentian.prompt.system import PromptContext, build_system_prompt
from wentian.commands.spec import CommandSpec, CommandType
from wentian.skill_activator import SkillActivator
from wentian.skills.base import SkillMode
from wentian.skills.loader import discover_skills
from wentian.tools.skill_tool import LoadSkillTool
from wentian.ui.confirm import Choice
from wentian.render import Renderer
from wentian.repl import REPL
from wentian.session import (
    SessionStore,
    project_sessions_dir,
    prune_expired,
    resume_gap_reminder,
    truncate_unpaired,
)
from wentian.tools.executor import ToolExecutor
from wentian.tools.files import EditFileTool, ReadFileTool, WriteFileTool
from wentian.tools.registry import ToolRegistry
from wentian.tools.search import FindFilesTool, SearchTextTool
from wentian.tools.shell import RunCommandTool
from wentian.commands.builtins import build_builtin_registry
from wentian.ui.banner import build_banner
from wentian.ui.completion import CommandCompleter
from wentian.ui.input import PromptInput, default_history_path
from wentian.ui.interrupt import EscListener, InterruptListener
from wentian.ui.select import select_provider
from wentian.agents.loader import discover_agents
from wentian.agents.manager import BackgroundTaskManager
from wentian.agents.runner import run_subagent
from wentian.agents.tool import AgentTool

__all__ = ["app", "build_app"]

app = typer.Typer(add_completion=False)


# ---------------------------------------------------------------------------
# v0.3 · C13 (task T45) — tool wiring helpers
# ---------------------------------------------------------------------------


async def _deny_confirm(*, tool_name: str, preview: str, reason: str) -> Choice:
    """v0.6 · C39 · F44 (task T79) — human-in-the-loop confirm callback for non-interactive/non-TTY.

    Pipelines / CI / non-TTY have no terminal to show a three-option approval menu — by secure default (N16/AC55),
    always returns :attr:`~wentian.ui.confirm.Choice.DENY`. The REPL's permission gate synthesizes a
    rejection result (is_error) fed back into the loop: no silent pass-through, no blocked pipeline, no filesystem access.
    """
    return Choice.DENY


def _build_default_tools(root: Path) -> tuple[ToolRegistry, ToolExecutor]:
    """v0.3 · C13 (task T45) — register the six standard tools against *root*
    and pair them with an executor.

    v0.6 · C35 · F43/F45 (task T75) — the executor's v0.3 confirmation gate
    (F26) is gone; permission decisions move up to the AgentLoop's five-layer
    pipeline (wired in T76). The executor is now pure execute+timeout.

    Order: read_file, write_file, edit_file, run_command, find_files,
    search_text (registration order drives the advertised tools= list).
    """
    registry = ToolRegistry()
    registry.register(ReadFileTool(root))
    registry.register(WriteFileTool(root))
    registry.register(EditFileTool(root))
    registry.register(RunCommandTool(root))
    registry.register(FindFilesTool(root))
    registry.register(SearchTextTool(root))

    executor = ToolExecutor(registry)
    return registry, executor


# ---------------------------------------------------------------------------
# v0.7 · C46 · F55/N23 (task T88) — MCP discovery report
# ---------------------------------------------------------------------------


def _print_mcp_report(report, console: Console) -> None:  # type: ignore[type-arg]
    """Print a dim-style MCP discovery summary (successes + failures).

    Matches the dim inline-hint style of the startup banner area.
    Called only when mcp_servers is non-empty — zero output for empty config.
    """
    from wentian.mcp.manager import DiscoveryReport

    if not isinstance(report, DiscoveryReport):
        return
    for name, n in report.ok.items():
        console.print(
            f"[dim]       MCP [/dim][dim #C84B31]{name}[/dim #C84B31]"
            f"[dim] · {n} tools registered[/dim]"
        )
    for name, reason in report.failed.items():
        console.print(
            f"[dim]       MCP [/dim][dim red]{name}[/dim red]"
            f"[dim] · connection failed: {reason}[/dim]"
        )


# ---------------------------------------------------------------------------
# v0.9 · C56/C59 · F68 (task T106) — memory dir resolution
# ---------------------------------------------------------------------------


def _user_memory_dir() -> Path:
    """User-scope memory root: ``$XDG_CONFIG_HOME/wentian/memory`` (XDG-aware)."""
    import os

    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "wentian" / "memory"


def _project_memory_dir(cwd: Path) -> Path:
    """Project-scope memory root: ``<cwd>/.wentian/memory``."""
    return cwd / ".wentian" / "memory"


# ---------------------------------------------------------------------------
# v0.11 · C107a · F73 (task T134a) — skill dir resolution (mirrors memory convention)
# ---------------------------------------------------------------------------


def _user_skills_dir() -> Path:
    """User-scope skills root: ``$XDG_CONFIG_HOME/wentian/skills`` (XDG-aware)."""
    import os

    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "wentian" / "skills"


def _project_skills_dir(cwd: Path) -> Path:
    """Project-scope skills root: ``<cwd>/.wentian/skills``."""
    return cwd / ".wentian" / "skills"


# ---------------------------------------------------------------------------
# v0.13 · C117 · F93 (task T145) — agent dir resolution (mirrors skills convention)
# ---------------------------------------------------------------------------


def _user_agents_dir() -> Path:
    """User-scope agents root: ``$XDG_CONFIG_HOME/wentian/agents`` (XDG-aware)."""
    import os

    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "wentian" / "agents"


def _project_agents_dir(cwd: Path) -> Path:
    """Project-scope agents root: ``<cwd>/.wentian/agents``."""
    return cwd / ".wentian" / "agents"


def _skill_provider_cfg(provider_cfg, model_override):  # noqa: ANN001, ANN201
    """v0.11 · F84/F89 (T135 review-fix) — provider configuration for isolated sub-conversations.

    When ``skill.model`` is non-empty → use :func:`dataclasses.replace` to copy ``provider_cfg``,
    overriding only the ``model`` field (does not mutate the original cfg); empty/None → return ``provider_cfg`` as-is.
    Allows frontmatter's ``model:`` to truly affect backend selection for isolated mode sub-conversations.
    """
    if model_override:
        return dataclasses.replace(provider_cfg, model=model_override)
    return provider_cfg


# ---------------------------------------------------------------------------
# v0.11 · C107b · F73 (task T134b) — Skill slash command surface: handler factory + conflict strategy
# ---------------------------------------------------------------------------


def _make_skill_handler(name, activator, skill_registry):  # noqa: ANN001
    """Create a command handler that maps ``/<name>`` to Skill activation.

    Handler signature ``(ctx, args) -> None``:

    - ISOLATED → ``ctx.print(activator.activate(name, args))`` (prints the summary result).
    - SHARED → ``activator.activate(name, args)`` then ``ctx.send_user_message(...)``
      triggers one AI round (body injected via the T134a-wired reminder channel); empty args use the default trigger string.
    """

    def handler(ctx, args):  # noqa: ANN001
        skill = skill_registry.get(name)
        if skill is not None and skill.mode is SkillMode.ISOLATED:
            ctx.print(activator.activate(name, args))
            return None
        # SHARED (or fallback when the registry no longer has this skill): activate + trigger one AI round.
        activator.activate(name, args)
        ctx.send_user_message(args.strip() or f"Please follow the {name} skill instructions")
        return None

    return handler


def _register_skill_command(cmd_registry, spec, console) -> bool:  # noqa: ANN001
    """Register a Skill slash command into *cmd_registry* following the conflict strategy.

    - No existing command with the same name → register directly.
    - Same name and existing command is PROMPT (old skill slash / built-in ``/review``-type prompt command) →
      **replace** (skill wins): unregister the old, register the new.
    - Same name and existing command is not PROMPT (LOCAL / UI_STATE control commands, e.g. ``/exit`` / ``/skills``) →
      **skip + warn** (control commands are protected); skill can still be loaded via the ``load_skill`` tool, never raises.

    Returns whether the slash command was actually registered (used to track the registered set on reload).
    """
    existing = cmd_registry.lookup(spec.name)
    if existing is None:
        cmd_registry.register(spec)
        return True
    if existing.type == CommandType.PROMPT:
        cmd_registry.unregister(existing.name)
        cmd_registry.register(spec)
        return True
    # Protected control command: skip slash registration, warn only.
    console.print(
        f"[yellow]Skill '{spec.name}' conflicts with a built-in control command, "
        f"skipping slash registration (still loadable via load_skill)[/yellow]"
    )
    return False


def _build_skill_command_spec(skill, activator, skill_registry) -> CommandSpec:
    """Build the PROMPT slash command :class:`CommandSpec` from a Skill."""
    return CommandSpec(
        name=skill.name,
        summary=skill.description,
        usage=f"/{skill.name} [args]",
        type=CommandType.PROMPT,
        handler=_make_skill_handler(skill.name, activator, skill_registry),
        aliases=(),
        arg_hint="",
        hidden=False,
    )


def _register_all_skill_commands(
    cmd_registry, skill_registry, activator, console
) -> set[str]:  # noqa: ANN001
    """Build a spec for each item in ``skill_registry.list()`` and register following the conflict strategy.

    Returns the set of successfully registered skill command names (used to unregister old commands on reload).
    """
    registered: set[str] = set()
    for skill in skill_registry.list():
        spec = _build_skill_command_spec(skill, activator, skill_registry)
        if _register_skill_command(cmd_registry, spec, console):
            registered.add(skill.name)
    return registered


def _render_skills_list(skills) -> str:  # noqa: ANN001
    """Render a set of Skills into the ``/skills`` list plain text (each line ``- /<name> [<source>·<mode>]
    <description>``). Pure function, zero provider requests.

    The brackets in ``[<source>·<mode>]`` are escaped with ``\\[`` — otherwise Rich would consume them as style tags
    (consistent on non-TTY/pipes too)."""
    if not skills:
        return "[dim](No skills loaded)[/dim]"
    lines = ["Available Skills:"]
    for skill in skills:
        lines.append(
            f"  - /{skill.name}  \\[{skill.source}·{skill.mode.value}]  "
            f"{skill.description}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pure assembly function
# ---------------------------------------------------------------------------


def build_app(
    config_path=None,
    sessions_dir=None,
    *,
    provider_name: str | None = None,
    continue_: bool = False,
    resume_id: str | None = None,
    console: Console | None = None,
    provider_selector: Callable[[list[str], str], str] | None = None,
    input_fn: Callable[..., str] | None = None,
    history_path: Path | None = None,
    show_banner: bool = True,
    interrupt_listener: InterruptListener | None = None,
    tool_registry: ToolRegistry | None = None,
    tool_executor: ToolExecutor | None = None,
    confirm_fn: Callable[..., object] | None = None,
) -> REPL:
    """Assemble and return a REPL instance — pure function, no I/O side-effects.

    v0.2 · C7 · F13-F18 (task T25) — UI wiring: startup banner (F13),
    provider selector (F14), PromptInput status line (F15/F16), Esc
    interrupt listener (F18).  TTY detection lives in the typer ``main``,
    NOT here — callers inject the real UI components (or nothing, in which
    case behavior is identical to v0.1).

    Parameters
    ----------
    config_path:
        Path to the config YAML.  None → XDG default.
    sessions_dir:
        Directory for session files.  None → XDG default.
    provider_name:
        Override the default provider with this name.
    continue_:
        If True, load the most-recently-updated session (or create new + hint).
    resume_id:
        Load this specific session by id.  Takes precedence over continue_.
    console:
        Rich Console to use.  None → new Console().
    provider_selector:
        Callable(names, default) -> name.  Called only when no provider_name
        was given AND the config defines more than one provider (AC12).
        None → default provider, no selection (v0.1 behavior).
    input_fn:
        Callable used by the REPL to read user input.  None → fall back to
        ``history_path``-built PromptInput if given, else builtins.input.
    history_path:
        Convenience: when input_fn is None and this is set, build_app
        constructs ``PromptInput(history_path=history_path)`` itself.
    show_banner:
        If True (default) print the startup banner (printed regardless of
        TTY — pipes see it too; AC11).
    interrupt_listener:
        Passed through to REPL.  None → REPL defaults to NullListener.
    tool_registry:
        v0.3 · C13 (task T45) — ToolRegistry to advertise to the provider.
        None (with tool_executor also None) → build the six standard tools
        rooted at ``Path.cwd()``.  Injected → passed through verbatim.
    tool_executor:
        v0.3 · C13 (task T45) — ToolExecutor used to run tool calls.  None
        (with tool_registry also None) → build the default executor.  v0.6 · C35
        · T75 — the executor no longer gates; permission decisions live in the
        five-layer pipeline wired below.  Injected → passed through verbatim.
    confirm_fn:
        v0.6 · C39 · F44 (task T79) — human-in-the-loop confirm callback
        (``async (*, tool_name, preview, reason) -> Choice``) used by the REPL's
        permission gate when the pipeline returns Ask.  None (default) → the
        non-TTY safe-default :func:`_deny_confirm` (always Deny; N16/AC55).
        ``main`` injects the real :func:`~wentian.ui.confirm.confirm_action`
        only on a TTY.

    Returns
    -------
    REPL
        Fully wired, ready to call .run() on.

    Raises
    ------
    ConfigError
        On missing or invalid configuration.
    FileNotFoundError
        When resume_id does not match any stored session.
    """
    # 1. Config
    config = load_config(config_path)

    # 2. Provider — F14 (AC12): no -p + multiple providers + selector wired
    #    → ask; otherwise go straight in.
    if (
        provider_name is None
        and len(config.providers) > 1
        and provider_selector is not None
    ):
        provider_name = provider_selector(list(config.providers), config.default)

    provider_cfg = config.get(provider_name)  # uses default when None
    provider = create_provider(provider_cfg)

    # 3. Session store — v0.9 · C54 · F64 (task T106): default to the cwd
    #    partition (project_sessions_dir); an injected sessions_dir still wins
    #    so tests / -c stay unaffected.
    cwd = Path.cwd()
    store_dir = sessions_dir if sessions_dir is not None else project_sessions_dir(cwd)
    store = SessionStore(store_dir)

    # 4. Session — track `resumed` for the banner (F13: resumed vs new session)
    #    NOTE (review fix #11): session selection happens BEFORE expired-session
    #    pruning so a critically-stale resumed/continued session is never deleted
    #    out from under its own load. The active id is then exempt from pruning.
    hint: str | None = None
    resumed = False
    resumed_session_path: Path | None = None
    if resume_id is not None:
        # Raises FileNotFoundError for bad id — propagated to caller
        session = store.load(resume_id)
        resumed = True
        resumed_session_path = store._path(resume_id)
    elif continue_:
        session = store.load_latest()
        if session is None:
            session = store.create(provider=provider.name)
            hint = "No previous session found — starting a new session."
        else:
            resumed = True
            resumed_session_path = store._path(session.id)
    else:
        session = store.create(provider=provider.name)

    # 4b. Lazy expired-session pruning (v0.9 · C55 · F66 · task T106) — best
    #     effort on the current partition, AFTER session selection and exempting
    #     the active session id (review fix #11). Failures are warned + skipped
    #     inside prune_expired and never abort startup.
    try:
        prune_expired(
            store_dir,
            config.sessions.retention_days,
            exempt_ids={session.id},
        )
    except Exception:  # noqa: BLE001 — pruning must never crash startup
        pass

    # 5. Console + Renderer
    _console = console if console is not None else Console()
    renderer = Renderer(_console)

    # 6. Banner (F13 / AC11) — printed even on non-TTY when show_banner=True
    if show_banner:
        _console.print(
            build_banner(
                version=wentian.__version__,
                provider_name=provider_cfg.name,
                model=provider_cfg.model,
                session_id=session.id,
                resumed=resumed,
            )
        )
        # Opening hint: one dim-style line, same startup content as the banner (suppressed together with show_banner).
        _console.print(
            "[dim]       Type [/dim][dim #C84B31]/help[/dim #C84B31][dim] for commands · [/dim][dim #C84B31]/exit[/dim #C84B31][dim] to quit[/dim]"
        )

    # 7. Print hint (only after console is set up)
    if hint:
        _console.print(f"[yellow]{hint}[/yellow]")

    # 8. Wire provider_factory
    def _provider_factory(name: str):
        return create_provider(config.get(name))

    # 8b. Command registry (v0.10 · C92 · F70/N37 · task T115) — built-in command
    #     registry assembled unconditionally (startup panic: ValueError not swallowed, propagates directly N37).
    #     Must be built before PromptInput so CommandCompleter can reference it (N38).
    #     registry shared bidirectionally by REPL._dispatch_command and CommandCompleter.
    command_registry = build_builtin_registry()

    # 9. Input function (F15) — injected wins; history_path builds a
    #    PromptInput with CommandCompleter (v0.10 · C92 · F70/N38 · task T115);
    #    otherwise plain builtins.input (v0.1 behavior).
    if input_fn is None and history_path is not None:
        input_fn = PromptInput(
            history_path=history_path,
            completer=CommandCompleter(command_registry),
        )
    resolved_input: Callable[..., str] = input_fn if input_fn is not None else input

    # 9b. Tools (v0.3 · C13 · task T45) — default-build both when neither was
    #     injected; otherwise pass injected values through verbatim.
    #     v0.5 · C21 (task T67) — system prompt assembled via build_system_prompt
    #     + PromptContext, replacing the old _tools_system_prompt helper.
    if tool_registry is None and tool_executor is None:
        tool_registry, tool_executor = _build_default_tools(cwd)

    # 9b-2. MCP discovery (v0.7 · C46 · F55/N23 · task T88) — only when
    #     config.mcp_servers is non-empty; otherwise zero IO / zero behavior
    #     change (N23).  Discovered MCP tools are registered into the same
    #     registry alongside the six built-in tools.  The manager is kept for
    #     lifecycle management (close_all on REPL exit).
    mcp_manager: MCPManager | None = None
    if config.mcp_servers:
        mcp_manager = MCPManager()
        report = mcp_manager.discover_and_register(config.mcp_servers, tool_registry)
        _print_mcp_report(report, _console)

    # 9b-2b. Skill discovery + assembly (v0.11 · C107a · F73/F87 · task T134a) —
    #     gated on config.skills.enabled. Runs AFTER MCP discovery so MCP tools
    #     are already in tool_registry when the whitelist validation checks each
    #     Skill's allowed_tools against the live registry. Three layers
    #     (builtin → user → project) are merged by discover_skills; an empty
    #     result OR skills.enabled=false ⇒ activator=None, no menu (v0.10 behavior).
    #
    #     Whitelist fail-fast (DELIBERATELY different from the loader's silent skip
    #     of malformed files): every non-empty allowed_tools entry MUST resolve to
    #     a registered tool — a missing one raises ValueError here, propagating as
    #     a startup panic (N37). The activator's get_main_system reads a holder so
    #     it always returns the freshly-built system prompt (set below).
    activator: SkillActivator | None = None
    skill_menu: tuple[tuple[str, str], ...] = ()
    # v0.11 · C107b (task T134b) — expose the discovered registry to the outer scope for the slash command surface
    # (/skills + /skills reload + skill→PROMPT commands) to wire after system prompt is built.
    skill_registry_live: object | None = None
    # holder: activator's get_main_system reads it, filled with the final value after system prompt is built
    # (activate is only called at runtime, by which time the holder is ready).
    system_holder: dict[str, str] = {"system": ""}
    if config.skills.enabled and tool_registry is not None:
        skill_registry = discover_skills(_project_skills_dir(cwd), _user_skills_dir())
        discovered = skill_registry.list()
        if discovered:
            registered_names = set(tool_registry.names())
            for skill in discovered:
                if not skill.allowed_tools:
                    continue
                for tool in skill.allowed_tools:
                    if tool not in registered_names:
                        raise ValueError(
                            f"Skill '{skill.name}' allowed_tools references "
                            f"non-existent tool: {tool}"
                        )

            def _fresh_provider(model_override=None):  # noqa: ANN001, ANN202
                # N47 thread safety: worker thread uses a fresh provider instance, never shares the main provider.
                # F89: skill.model overrides the model when non-empty (_skill_provider_cfg copies cfg).
                return create_provider(
                    _skill_provider_cfg(provider_cfg, model_override)
                )

            def _skill_loop_factory(
                worker_provider, *, registry, executor, allowed_tools, skill
            ):
                # Ignore the passed-in worker_provider (main provider) — per N47 construct a fresh provider
                # instance inside the worker thread (never bring the main provider into a sub-thread);
                # F89: skill.model is applied to the sub-conversation provider via _fresh_provider.
                from wentian.agent.loop import AgentLoop

                tools_enabled = registry is not None and executor is not None
                if tools_enabled:
                    return AgentLoop(
                        _fresh_provider(skill.model),
                        registry=registry,
                        executor=executor,
                        allowed_tools=allowed_tools,
                    )
                return AgentLoop(
                    _fresh_provider(skill.model),
                    registry=None,
                    executor=None,
                    max_rounds=1,
                    allowed_tools=None,
                )

            activator = SkillActivator(
                skill_registry,
                provider=provider,
                tool_registry=tool_registry,
                executor=tool_executor,
                get_main_messages=lambda: session.messages,
                get_main_system=lambda: system_holder["system"],
                loop_factory=_skill_loop_factory,
            )
            # System-level tool: load_skill is always registered (exempt even when the activation set narrows the allowlist).
            # Must be registered before system prompt tool list is rendered ⇒ load_skill appears in tool declarations.
            tool_registry.register(LoadSkillTool(activator=activator))
            skill_menu = skill_registry.menu()
            skill_registry_live = skill_registry

    # 9b-2d. Agents assembly (v0.13 · C117 · F91/F93/F98/F99/N50 · task T145) —
    #     discover agents from project + user dirs; build the runner closure (CRITICAL
    #     ORDERING: captures `pipeline` and `settings` which are assigned BELOW at
    #     ~9c; closure resolves them at CALL TIME, not now — this is safe).
    #     BackgroundTaskManager + AgentTool are always registered (N50: Agent tool
    #     always present in default tool set; fork-only works even when enabled=False).
    #     When enabled=False, agent_registry=None and agents_manager=None, but the
    #     AgentTool is still registered (gracefully handles None registry/runner).
    if config.agents.enabled:
        agent_registry = discover_agents(_project_agents_dir(cwd), _user_agents_dir())

        def _agent_runner(agent_def, prompt, **kw):  # noqa: ANN001, ANN202
            # Late-bound closure: `pipeline` and `settings` are assigned at ~9c
            # (PermissionPipeline section below). At call time (run-time, long after
            # build_app returns), they are already resolved. Do NOT move the
            # assignment earlier — the pipeline reads settings which loads disk.
            return run_subagent(
                agent_def,
                prompt,
                base_provider_cfg=provider_cfg,
                provider_factory=create_provider,
                registry=tool_registry,
                executor=tool_executor,
                pipeline=pipeline,  # late-bound
                settings=settings,  # late-bound
                model_aliases=config.agents.model_aliases,
                background_allow=config.agents.background_allow,  # F97 third-layer pass-through
                **kw,  # carries parent_messages for fork; background flag via **kw
            )

        agents_manager: BackgroundTaskManager | None = BackgroundTaskManager(
            _agent_runner, config.agents
        )
    else:
        agent_registry = None
        _agent_runner = None
        agents_manager = None

    if tool_registry is not None:
        tool_registry.register(
            AgentTool(
                registry=agent_registry,
                manager=agents_manager,
                cfg=config.agents,
                get_parent_messages=lambda: session.messages,
            )
        )

    # 9b-3. Memory store + index injection (v0.9 · C56/C59 · F68 · task T106).
    #     One store rooted at user-scope ($XDG_CONFIG_HOME/wentian/memory) +
    #     project-scope (<cwd>/.wentian/memory). Its two INDEX summaries are read
    #     ONCE at startup (not hot-reloaded — keeps the prompt cache prefix
    #     stable) and injected into the long-term memory slot. Reused as the runner's
    #     store for writing extracted notes.
    memory_store = MemoryStore(
        user_dir=_user_memory_dir(),
        project_dir=_project_memory_dir(cwd),
        cfg=config.memory,
    )
    # v0.9 review fix (Major #2 / AC82 / F69) — when memory.enabled:false, **both extraction and
    # injection are off**: the injection side also has the enabled guard, existing INDEX files on disk are neither read nor injected.
    if config.memory.enabled:
        try:
            memory_text = memory_store.read_indexes_for_injection()
        except Exception:  # noqa: BLE001 — index read failure must not block startup
            memory_text = ""
    else:
        memory_text = ""

    # 9b-4. Project instructions (v0.9 · C53/C59 · F63 · task T106) — three-layer
    #     WENTIAN.md + @include, read once into the project/custom instructions slot.
    try:
        project_instructions = load_project_instructions(cwd)
    except Exception:  # noqa: BLE001 — instruction read failure must not block startup
        project_instructions = ""

    if tool_registry is not None:
        # tool_names is read after load_skill registration ⇒ tool declarations include load_skill (when skills are present).
        tool_names = tuple(tool_registry.names())
        system = build_system_prompt(
            PromptContext(
                cwd=cwd,
                tool_names=tool_names,
                project_instructions=project_instructions,
                memory=memory_text,
                # v0.11 · C107a · F73 (task T134a) — "Available Skills" menu (no skills ⇒ ()).
                available_skills=skill_menu,
            )
        )
    else:
        system = None

    # v0.11 · C107a · F73 (task T134a) — fill holder: activator's get_main_system
    # closure reads the final system from this point on (first activate call is at runtime, holder is ready by then).
    system_holder["system"] = system or ""

    # 9b-2c. Skill slash command surface (v0.11 · C107b · F73 · task T134b) — only when skills are enabled
    #     and Skills are discovered (activator is not None). Wired after system prompt is built, because
    #     /skills reload's live menu refresh needs cwd / project_instructions / memory_text /
    #     tool_names and other assembly inputs (all visible here).
    #
    #     Registration order: /skills first (LOCAL control command) → then each skill→PROMPT command. This way
    #     the "existing command is not PROMPT ⇒ skip+warn" protection naturally covers /skills itself (a user-written
    #     skill named "skills" will also be skipped), while letting the PROMPT built-in /review be replaced by a skill
    #     and control commands like /exit·/clear be skipped+warned — never panics.
    skill_registered_names: set[str] = set()
    if activator is not None and skill_registry_live is not None:

        def _refresh_system(menu) -> str:  # noqa: ANN001
            """Rebuild the system prompt with the refreshed menu (other inputs reuse startup-time assembly values)."""
            return build_system_prompt(
                PromptContext(
                    cwd=cwd,
                    tool_names=tuple(tool_registry.names()),
                    project_instructions=project_instructions,
                    memory=memory_text,
                    available_skills=menu,
                )
            )

        def _skills_handler(ctx, args):  # noqa: ANN001
            sub = args.strip().lower()
            if sub == "reload":
                _skills_reload(ctx)
                return None
            ctx.print(_render_skills_list(activator.list_skills()))
            return None

        def _skills_reload(ctx) -> None:  # noqa: ANN001
            # Guard: activator absent ⇒ skills system not enabled (this closure is only registered when activator is not None,
            # normally unreachable; retained to satisfy AC contract).
            if activator is None:
                ctx.print("[yellow]Skill system not enabled[/yellow]")
                return
            new_registry = discover_skills(_project_skills_dir(cwd), _user_skills_dir())
            # Reload permissive validation: if any skill's allowed_tools references an unregistered tool ⇒ print error,
            # retain old registry/commands/menu (never apply, never crash). This is the reload counterpart of startup fail-fast
            # — startup is strict, reload is permissive.
            registered_tools = set(tool_registry.names())
            for skill in new_registry.list():
                if not skill.allowed_tools:
                    continue
                for tool in skill.allowed_tools:
                    if tool not in registered_tools:
                        ctx.print(
                            f"[red]Reload failed: Skill '{skill.name}' allowed_tools "
                            f"references non-existent tool '{tool}', retaining existing Skills unchanged[/red]"
                        )
                        return
            # Apply: remove old skill commands → switch activator registry + clear stale activation set →
            # re-run part-B registration (refresh tracking set).
            for name in list(skill_registered_names):
                command_registry.unregister(name)
            skill_registered_names.clear()
            activator.set_registry(new_registry)
            activator.clear()
            new_names = _register_all_skill_commands(
                command_registry, new_registry, activator, _console
            )
            skill_registered_names.update(new_names)
            # Live menu refresh: rebuild system prompt, sync holder + repl._system.
            new_system = _refresh_system(new_registry.menu())
            system_holder["system"] = new_system
            ctx.refresh_skill_menu(new_system)
            ctx.print(
                f"[green]Reloaded: {len(new_registry.list())} Skills discovered[/green]"
            )

        # Register /skills first (protected LOCAL control command).
        command_registry.register(
            CommandSpec(
                name="skills",
                summary="List loaded Skills; /skills reload to rediscover",
                usage="/skills [reload]",
                type=CommandType.LOCAL,
                handler=_skills_handler,
                aliases=(),
                arg_hint="[reload]",
                hidden=False,
            )
        )
        # Then register each skill→PROMPT command (conflict strategy: PROMPT replaces / control skips+warns).
        skill_registered_names = _register_all_skill_commands(
            command_registry, skill_registry_live, activator, _console
        )

    # 9c. Permissions (v0.6 · C39 · F44 · task T79) — load the three-layer
    #     settings rooted at cwd and build the five-layer pipeline; the REPL's
    #     _build_gate() turns it into the AgentLoop's permission_gate.  Initial
    #     mode comes from settings.default_mode (local>project>user, else default;
    #     AC58).  The ask callback defaults to the non-TTY safe-default Deny
    #     (N16/AC55); main() injects the interactive confirm only on a TTY.
    project_root = cwd
    settings = load_settings(project_root)
    pipeline = PermissionPipeline(project_root=project_root, settings=settings)
    resolved_confirm = confirm_fn if confirm_fn is not None else _deny_confirm

    # 9d. Compactor (v0.8 · C52 · F61/F62 · task T96) — two-layer context
    #     compaction, default-on this version. Window resolves per-provider
    #     (provider.context_window wins, else ContextConfig.default_window);
    #     offload artifacts live under <sessions_dir>/<session_id>.artifacts/.
    #     The REPL injects compactor.compact as the loop's pre_round_compact and
    #     updates it on /provider · /new · /resume.
    #
    # v0.12 · C99 · F78/F79/F83 (task T124) — HookEngine assembly.
    # config.hooks is a list[HookRule]; non-empty → build HookEngine and wire
    # into the REPL + Compactor. Empty list → hook_engine=None (no hooks,
    # byte-level v0.11 behavior, N40).
    hook_engine: HookEngine | None = (
        HookEngine(config.hooks, agents_manager=agents_manager)
        if config.hooks
        else None
    )

    # v0.12 · C99 · F78 (task T124) — PreCompact seam: fire PRE_COMPACT event
    # via a callback injected into Compactor. None when no hook engine.
    def _on_pre_compact(trigger: str) -> None:
        if hook_engine is not None:
            hook_engine.fire(
                HookEvent.PRE_COMPACT,
                {"trigger": trigger, "session_id": session.id},
            )

    on_pre_compact_cb = _on_pre_compact if hook_engine is not None else None

    context_window = provider_cfg.context_window or config.context.default_window
    artifacts_dir = store_dir / f"{session.id}.artifacts"
    compactor = Compactor(
        provider,
        artifacts_dir=artifacts_dir,
        context_window=context_window,
        cfg=config.context,
        on_pre_compact=on_pre_compact_cb,
    )

    # 9e. Resume hygiene (v0.9 · C55 · F65 · task T106) — only on a resumed
    #     session: ① drop a trailing unpaired tool_call turn; ② if the recovered
    #     history overflows the window budget, compact once (reuse v0.8 Compactor);
    #     ③ compute a one-shot time-gap reminder string (injected via the
    #     <system-reminder> channel on the first turn, never persisted).
    resume_reminder: str | None = None
    if resumed:
        session.messages[:] = truncate_unpaired(session.messages)
        overflow_threshold = (
            context_window - config.context.reserved_output - config.context.auto_margin
        )
        est = estimate_total(
            0, session.messages, char_per_token=config.context.char_per_token
        )
        if est > overflow_threshold:
            try:
                compactor.compact(session.messages, None, manual=False)
            except Exception:  # noqa: BLE001 — overflow pre-compaction is best-effort
                pass
        if resumed_session_path is not None:
            try:
                import datetime as _dt

                updated_at = resumed_session_path.stat().st_mtime
                now = _dt.datetime.now(_dt.timezone.utc).timestamp()
                resume_reminder = resume_gap_reminder(
                    updated_at, now, config.sessions.resume_gap_reminder_hours
                )
            except OSError:
                resume_reminder = None

    # 9f. Memory runner (v0.9 · C58 · F67 · task T106) — background fire-and-forget
    #     extraction. Off when memory.enabled is false (runner=None ⇒ v0.8). The
    #     extraction provider is a FRESH instance built per worker (never shares
    #     the chat provider thread); its config comes from memory.provider when
    #     set, else the active conversation provider's own config.
    memory_runner: MemoryRunner | None = None
    if config.memory.enabled:
        mem_provider_name = config.memory.provider
        mem_provider_cfg = (
            config.get(mem_provider_name)
            if mem_provider_name is not None
            else provider_cfg
        )

        def _memory_provider_factory():
            return create_provider(mem_provider_cfg)

        memory_runner = MemoryRunner(
            provider_factory=_memory_provider_factory,
            store=memory_store,
            cfg=config.memory,
            session_id=session.id,
        )

    # 10. Assemble REPL
    repl = REPL(
        provider=provider,
        session=session,
        store=store,
        renderer=renderer,
        provider_factory=_provider_factory,
        input_fn=resolved_input,
        system=system,
        interrupt_listener=interrupt_listener,
        registry=tool_registry,
        executor=tool_executor,
        pipeline=pipeline,
        confirm_fn=resolved_confirm,
        default_mode=settings.default_mode,
        mcp_manager=mcp_manager,
        compactor=compactor,
        memory_runner=memory_runner,
        resume_reminder=resume_reminder,
        # v0.10 · C92 · F70/N37/N38 (task T115) — command registry + long-term memory store
        commands=command_registry,
        memory_store=memory_store,
        # v0.11 · C107a · F73/F87 (task T134a) — Skill activation orchestration (None ⇒ v0.10 behavior).
        activator=activator,
        # v0.12 · C99 · F78/F79/F83 (task T124) — HookEngine (None = no hooks)
        hooks=hook_engine,
        # v0.13 · C117 · F98/F99 (task T145) — background task manager (None = agents not enabled)
        agents_manager=agents_manager,
    )

    # 11. Status line wiring (F16) — duck-check so any PromptInput-like
    #     object gets the live status_line; plain callables are untouched.
    if hasattr(resolved_input, "status_provider"):
        resolved_input.status_provider = repl.status_line

    return repl


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


# v0.2 · C7 · F13-F18 (task T25) — real-world wiring decided in main() (not in
# build_app): on a real terminal we use the arrow-key provider selector,
# the PromptInput box with persistent history and the Esc interrupt listener;
# on pipes/redirects everything degrades to v0.1 behavior (builtins.input +
# NullListener, no selector).  The banner prints in both cases.
@app.command()
def main(
    provider: Annotated[
        Optional[str],
        typer.Option("--provider", "-p", help="Provider name from config."),
    ] = None,
    continue_: Annotated[
        bool,
        typer.Option("--continue", help="Resume the most recent session."),
    ] = False,
    resume: Annotated[
        Optional[str],
        typer.Option("--resume", help="Resume a specific session by id."),
    ] = None,
) -> None:
    """Start the wentian interactive REPL.  v0.2 · C7 · F13-F18 (task T25)"""
    tty = sys.stdin.isatty() and sys.stdout.isatty()
    if tty:
        selector: Callable[[list[str], str], str] | None = select_provider
        input_fn: Callable[..., str] | None = PromptInput(
            history_path=default_history_path()
        )
        listener: InterruptListener | None = EscListener()
        # v0.6 · C39 · F44 (task T79) — only on TTY is there a terminal for the three-option approval menu.
        from wentian.ui.confirm import confirm_action

        confirm: Callable[..., object] | None = confirm_action
    else:
        selector = None
        input_fn = None
        listener = None
        confirm = None  # build_app → _deny_confirm (non-TTY secure default deny)

    try:
        repl = build_app(
            provider_name=provider,
            continue_=continue_,
            resume_id=resume,
            provider_selector=selector,
            input_fn=input_fn,
            interrupt_listener=listener,
            confirm_fn=confirm,
        )
    except (ConfigError, FileNotFoundError) as exc:
        err_console = Console(stderr=True)
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1)

    repl.run()
