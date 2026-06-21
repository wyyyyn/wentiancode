"""CLI entry point for wentian.

Provides `wentian` and `wt` console scripts (both point to `app`).

Assembly:
- build_app() is a pure function: load config → pick provider → create store
  → wire session → Renderer → REPL.  No I/O side-effects except returning REPL.
- The typer command `main` calls build_app(...).run() and catches expected
  errors (ConfigError, FileNotFoundError) for friendly stderr output.
"""

from __future__ import annotations

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

__all__ = ["app", "build_app"]

app = typer.Typer(add_completion=False)


# ---------------------------------------------------------------------------
# v0.3 · C13（任务 T45）— tool wiring helpers
# ---------------------------------------------------------------------------


async def _deny_confirm(*, tool_name: str, preview: str, reason: str) -> Choice:
    """v0.6 · C39 · F44（任务 T79）— 非交互/非 TTY 下的人在回路 confirm 回调。

    管道 / CI / 非 TTY 没有终端可弹三选一审批菜单——出于安全默认（N16/AC55），
    一律返回 :attr:`~wentian.ui.confirm.Choice.DENY`。REPL 的权限门据此合成成形
    拒绝结果（is_error）回灌循环：不静默放行、不卡住管道、不触碰文件系统。
    """
    return Choice.DENY


def _build_default_tools(root: Path) -> tuple[ToolRegistry, ToolExecutor]:
    """v0.3 · C13（任务 T45）— register the six standard tools against *root*
    and pair them with an executor.

    v0.6 · C35 · F43/F45（任务 T75）— the executor's v0.3 confirmation gate
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
# v0.7 · C46 · F55/N23（任务 T88）— MCP 发现汇报
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
            f"[dim] · {n} 工具已注册[/dim]"
        )
    for name, reason in report.failed.items():
        console.print(
            f"[dim]       MCP [/dim][dim red]{name}[/dim red]"
            f"[dim] · 连接失败：{reason}[/dim]"
        )


# ---------------------------------------------------------------------------
# v0.9 · C56/C59 · F68（任务 T106）— memory dir resolution
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

    v0.2 · C7 · F13-F18（任务 T25）— UI wiring: startup banner (F13),
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
        v0.3 · C13（任务 T45）— ToolRegistry to advertise to the provider.
        None (with tool_executor also None) → build the six standard tools
        rooted at ``Path.cwd()``.  Injected → passed through verbatim.
    tool_executor:
        v0.3 · C13（任务 T45）— ToolExecutor used to run tool calls.  None
        (with tool_registry also None) → build the default executor.  v0.6 · C35
        · T75 — the executor no longer gates; permission decisions live in the
        five-layer pipeline wired below.  Injected → passed through verbatim.
    confirm_fn:
        v0.6 · C39 · F44（任务 T79）— human-in-the-loop confirm callback
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

    # 3. Session store — v0.9 · C54 · F64（任务 T106）: default to the cwd
    #    partition (project_sessions_dir); an injected sessions_dir still wins
    #    so tests / -c stay unaffected.
    cwd = Path.cwd()
    store_dir = sessions_dir if sessions_dir is not None else project_sessions_dir(cwd)
    store = SessionStore(store_dir)

    # 4. Session — track `resumed` for the banner (F13: 已恢复 vs 新会话)
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

    # 4b. Lazy expired-session pruning (v0.9 · C55 · F66 · 任务 T106) — best
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
        # 起手提示：一行 dim 指路，跟 banner 同属启动内容（show_banner 一并抑制）。
        _console.print(
            "[dim]       输入 [/dim][dim #C84B31]/help[/dim #C84B31][dim] 查看命令 · [/dim][dim #C84B31]/exit[/dim #C84B31][dim] 退出[/dim]"
        )

    # 7. Print hint (only after console is set up)
    if hint:
        _console.print(f"[yellow]{hint}[/yellow]")

    # 8. Wire provider_factory
    def _provider_factory(name: str):
        return create_provider(config.get(name))

    # 8b. Command registry (v0.10 · C92 · F70/N37 · 任务 T115) — 内置命令
    #     注册中心无条件装配（startup panic：ValueError 不吞，直接传播 N37）。
    #     必须在 PromptInput 之前构建，以便 CommandCompleter 可以引用（N38）。
    #     registry 供 REPL._dispatch_command 和 CommandCompleter 双向共用。
    command_registry = build_builtin_registry()

    # 9. Input function (F15) — injected wins; history_path builds a
    #    PromptInput with CommandCompleter (v0.10 · C92 · F70/N38 · 任务 T115);
    #    otherwise plain builtins.input (v0.1 behavior).
    if input_fn is None and history_path is not None:
        input_fn = PromptInput(
            history_path=history_path,
            completer=CommandCompleter(command_registry),
        )
    resolved_input: Callable[..., str] = input_fn if input_fn is not None else input

    # 9b. Tools (v0.3 · C13 · 任务 T45) — default-build both when neither was
    #     injected; otherwise pass injected values through verbatim.
    #     v0.5 · C21（任务 T67）— system prompt assembled via build_system_prompt
    #     + PromptContext, replacing the old _tools_system_prompt helper.
    if tool_registry is None and tool_executor is None:
        tool_registry, tool_executor = _build_default_tools(cwd)

    # 9b-2. MCP discovery (v0.7 · C46 · F55/N23 · 任务 T88) — only when
    #     config.mcp_servers is non-empty; otherwise zero IO / zero behavior
    #     change (N23).  Discovered MCP tools are registered into the same
    #     registry alongside the six built-in tools.  The manager is kept for
    #     lifecycle management (close_all on REPL exit).
    mcp_manager: MCPManager | None = None
    if config.mcp_servers:
        mcp_manager = MCPManager()
        report = mcp_manager.discover_and_register(config.mcp_servers, tool_registry)
        _print_mcp_report(report, _console)

    # 9b-3. Memory store + index injection (v0.9 · C56/C59 · F68 · 任务 T106).
    #     One store rooted at user-scope ($XDG_CONFIG_HOME/wentian/memory) +
    #     project-scope (<cwd>/.wentian/memory). Its two INDEX summaries are read
    #     ONCE at startup (not hot-reloaded — keeps the prompt cache prefix
    #     stable) and injected into the 长期记忆 slot. Reused as the runner's
    #     store for writing extracted notes.
    memory_store = MemoryStore(
        user_dir=_user_memory_dir(),
        project_dir=_project_memory_dir(cwd),
        cfg=config.memory,
    )
    # v0.9 review fix（Major #2 / AC82 / F69）— memory.enabled:false 时**抽取与
    # 注入都关**：注入侧也加 enabled 守卫，磁盘已有 INDEX 也不读不注入。
    if config.memory.enabled:
        try:
            memory_text = memory_store.read_indexes_for_injection()
        except Exception:  # noqa: BLE001 — index read failure must not block startup
            memory_text = ""
    else:
        memory_text = ""

    # 9b-4. Project instructions (v0.9 · C53/C59 · F63 · 任务 T106) — three-layer
    #     WENTIAN.md + @include, read once into the 项目/自定义指令 slot.
    try:
        project_instructions = load_project_instructions(cwd)
    except Exception:  # noqa: BLE001 — instruction read failure must not block startup
        project_instructions = ""

    if tool_registry is not None:
        tool_names = tuple(tool_registry.names())
        system = build_system_prompt(
            PromptContext(
                cwd=cwd,
                tool_names=tool_names,
                project_instructions=project_instructions,
                memory=memory_text,
            )
        )
    else:
        system = None

    # 9c. Permissions (v0.6 · C39 · F44 · 任务 T79) — load the three-layer
    #     settings rooted at cwd and build the five-layer pipeline; the REPL's
    #     _build_gate() turns it into the AgentLoop's permission_gate.  Initial
    #     mode comes from settings.default_mode (本地>项目>用户, else default;
    #     AC58).  The ask callback defaults to the non-TTY safe-default Deny
    #     (N16/AC55); main() injects the interactive confirm only on a TTY.
    project_root = cwd
    settings = load_settings(project_root)
    pipeline = PermissionPipeline(project_root=project_root, settings=settings)
    resolved_confirm = confirm_fn if confirm_fn is not None else _deny_confirm

    # 9d. Compactor (v0.8 · C52 · F61/F62 · 任务 T96) — two-layer context
    #     compaction, default-on this version. Window resolves per-provider
    #     (provider.context_window wins, else ContextConfig.default_window);
    #     offload artifacts live under <sessions_dir>/<session_id>.artifacts/.
    #     The REPL injects compactor.compact as the loop's pre_round_compact and
    #     updates it on /provider · /new · /resume.
    #
    # v0.12 · C99 · F78/F79/F83（任务 T124）— HookEngine assembly.
    # config.hooks is a list[HookRule]; non-empty → build HookEngine and wire
    # into the REPL + Compactor. Empty list → hook_engine=None (no hooks,
    # byte-level v0.11 behavior, N40).
    hook_engine: HookEngine | None = HookEngine(config.hooks) if config.hooks else None

    # v0.12 · C99 · F78（任务 T124）— PreCompact seam: fire PRE_COMPACT event
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

    # 9e. Resume hygiene (v0.9 · C55 · F65 · 任务 T106) — only on a resumed
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

    # 9f. Memory runner (v0.9 · C58 · F67 · 任务 T106) — background fire-and-forget
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
        # v0.10 · C92 · F70/N37/N38（任务 T115）— 命令注册中心 + 长期记忆存储
        commands=command_registry,
        memory_store=memory_store,
        # v0.12 · C99 · F78/F79/F83（任务 T124）— HookEngine（None = no hooks）
        hooks=hook_engine,
    )

    # 11. Status line wiring (F16) — duck-check so any PromptInput-like
    #     object gets the live status_line; plain callables are untouched.
    if hasattr(resolved_input, "status_provider"):
        resolved_input.status_provider = repl.status_line

    return repl


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


# v0.2 · C7 · F13-F18（任务 T25）— real-world wiring decided in main() (not in
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
    """Start the wentian interactive REPL.  v0.2 · C7 · F13-F18（任务 T25）"""
    tty = sys.stdin.isatty() and sys.stdout.isatty()
    if tty:
        selector: Callable[[list[str], str], str] | None = select_provider
        input_fn: Callable[..., str] | None = PromptInput(
            history_path=default_history_path()
        )
        listener: InterruptListener | None = EscListener()
        # v0.6 · C39 · F44（任务 T79）— TTY 才有终端弹三选一审批菜单。
        from wentian.ui.confirm import confirm_action

        confirm: Callable[..., object] | None = confirm_action
    else:
        selector = None
        input_fn = None
        listener = None
        confirm = None  # build_app → _deny_confirm (非 TTY 安全默认拒绝)

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
