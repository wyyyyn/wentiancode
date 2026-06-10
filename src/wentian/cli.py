"""CLI entry point for wentian.

Provides `wentian` and `wt` console scripts (both point to `app`).

Assembly:
- build_app() is a pure function: load config → pick provider → create store
  → wire session → Renderer → REPL.  No I/O side-effects except returning REPL.
- The typer command `main` calls build_app(...).run() and catches expected
  errors (ConfigError, FileNotFoundError) for friendly stderr output.
"""
from __future__ import annotations

from typing import Annotated, Optional

import typer
from rich.console import Console

from wentian.config import ConfigError, load_config
from wentian.providers.factory import create_provider
from wentian.render import Renderer
from wentian.repl import REPL
from wentian.session import SessionStore, default_sessions_dir

__all__ = ["app", "build_app"]

app = typer.Typer(add_completion=False)


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
) -> REPL:
    """Assemble and return a REPL instance — pure function, no I/O side-effects.

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

    # 2. Provider
    provider_cfg = config.get(provider_name)   # uses default when None
    provider = create_provider(provider_cfg)

    # 3. Session store
    store_dir = sessions_dir if sessions_dir is not None else default_sessions_dir()
    store = SessionStore(store_dir)

    # 4. Session
    hint: str | None = None
    if resume_id is not None:
        # Raises FileNotFoundError for bad id — propagated to caller
        session = store.load(resume_id)
    elif continue_:
        session = store.load_latest()
        if session is None:
            session = store.create(provider=provider.name)
            hint = "No previous session found — starting a new session."
    else:
        session = store.create(provider=provider.name)

    # 5. Console + Renderer
    _console = console if console is not None else Console()
    renderer = Renderer(_console)

    # 6. Print hint (only after console is set up)
    if hint:
        _console.print(f"[yellow]{hint}[/yellow]")

    # 7. Wire provider_factory
    def _provider_factory(name: str):
        return create_provider(config.get(name))

    # 8. Assemble REPL
    return REPL(
        provider=provider,
        session=session,
        store=store,
        renderer=renderer,
        provider_factory=_provider_factory,
        system=None,
    )


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------

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
    """Start the wentian interactive REPL."""
    try:
        repl = build_app(
            provider_name=provider,
            continue_=continue_,
            resume_id=resume,
        )
    except (ConfigError, FileNotFoundError) as exc:
        err_console = Console(stderr=True)
        err_console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1)

    repl.run()
