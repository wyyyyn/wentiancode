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
from wentian.providers.factory import create_provider
from wentian.render import Renderer
from wentian.repl import REPL
from wentian.session import SessionStore, default_sessions_dir
from wentian.tools.executor import ToolExecutor
from wentian.tools.files import EditFileTool, ReadFileTool, WriteFileTool
from wentian.tools.registry import ToolRegistry
from wentian.tools.search import FindFilesTool, SearchTextTool
from wentian.tools.shell import RunCommandTool
from wentian.ui.banner import build_banner
from wentian.ui.input import PromptInput, default_history_path
from wentian.ui.interrupt import EscListener, InterruptListener
from wentian.ui.select import select_provider

__all__ = ["app", "build_app"]

app = typer.Typer(add_completion=False)


# ---------------------------------------------------------------------------
# v0.3 · C13（任务 T45）— tool wiring helpers
# ---------------------------------------------------------------------------

def _make_confirm(interactive: bool) -> Callable[[str], bool]:
    """v0.3 · C13（任务 T45）— build the confirmation callable for side-effect
    tools.

    Non-interactive (pipes / CI / non-TTY) → a callable that always returns
    False, so confirmation-gated tools (write_file/edit_file/run_command) are
    auto-denied and never run their side effects. Interactive → prints the
    tool description and reads a yes/no answer; only ``y``/``yes`` (case
    insensitive) approves, everything else (including empty) denies.
    """
    if not interactive:
        return lambda _description: False

    def _confirm(description: str) -> bool:
        print(description)
        answer = input("执行该操作？[y/N] ").strip().lower()
        return answer in ("y", "yes")

    return _confirm


def _build_default_tools(root: Path) -> tuple[ToolRegistry, ToolExecutor]:
    """v0.3 · C13（任务 T45）— register the six standard tools against *root*
    and pair them with an executor whose confirmation gate follows TTY.

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

    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    executor = ToolExecutor(registry, confirm=_make_confirm(interactive))
    return registry, executor


def _tools_system_prompt(root: Path) -> str:
    """v0.3 · C13（任务 T45）— system prompt appended when tools are enabled:
    advertise the tools, state the absolute working directory, and prefer
    relative paths."""
    return (
        "You have access to file and shell tools: read_file, write_file, "
        "edit_file, run_command, find_files, search_text.\n"
        f"The current working directory is {root}.\n"
        "Prefer relative paths (resolved against the working directory) over "
        "absolute paths."
    )


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
        (with tool_registry also None) → build the default executor whose
        confirmation gate follows TTY (non-TTY auto-denies side effects).
        Injected → passed through verbatim.

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

    provider_cfg = config.get(provider_name)   # uses default when None
    provider = create_provider(provider_cfg)

    # 3. Session store
    store_dir = sessions_dir if sessions_dir is not None else default_sessions_dir()
    store = SessionStore(store_dir)

    # 4. Session — track `resumed` for the banner (F13: 已恢复 vs 新会话)
    hint: str | None = None
    resumed = False
    if resume_id is not None:
        # Raises FileNotFoundError for bad id — propagated to caller
        session = store.load(resume_id)
        resumed = True
    elif continue_:
        session = store.load_latest()
        if session is None:
            session = store.create(provider=provider.name)
            hint = "No previous session found — starting a new session."
        else:
            resumed = True
    else:
        session = store.create(provider=provider.name)

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

    # 7. Print hint (only after console is set up)
    if hint:
        _console.print(f"[yellow]{hint}[/yellow]")

    # 8. Wire provider_factory
    def _provider_factory(name: str):
        return create_provider(config.get(name))

    # 9. Input function (F15) — injected wins; history_path builds a
    #    PromptInput; otherwise plain builtins.input (v0.1 behavior).
    if input_fn is None and history_path is not None:
        input_fn = PromptInput(history_path=history_path)
    resolved_input: Callable[..., str] = input_fn if input_fn is not None else input

    # 9b. Tools (v0.3 · C13 · 任务 T45) — default-build both when neither was
    #     injected; otherwise pass injected values through verbatim. The
    #     system prompt only gains the tool note when a registry is present.
    if tool_registry is None and tool_executor is None:
        tool_registry, tool_executor = _build_default_tools(Path.cwd())
    system = _tools_system_prompt(Path.cwd()) if tool_registry is not None else None

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
    else:
        selector = None
        input_fn = None
        listener = None

    try:
        repl = build_app(
            provider_name=provider,
            continue_=continue_,
            resume_id=resume,
            provider_selector=selector,
            input_fn=input_fn,
            interrupt_listener=listener,
        )
    except (ConfigError, FileNotFoundError) as exc:
        err_console = Console(stderr=True)
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1)

    repl.run()
