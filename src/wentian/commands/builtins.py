"""v0.10 · C89 · F76/F72 (task T112) — built-in slash command registry.
v0.13 · C116 · F101 (task T144) — added /agents command (13 commands total).

Provides the ``build_builtin_registry()`` factory function, registers all 13 built-in commands
in order and their handlers; provides ``render_help`` / ``parse_mode`` two helper functions.

Layering rule: pure leaf module, only imports same-package spec/registry + wentian.permissions.decision.Mode.
Never import rich / prompt_toolkit / any provider / repl / agent.
"""

from __future__ import annotations

from wentian.commands.registry import CommandRegistry
from wentian.commands.spec import CommandSpec, CommandType
from wentian.permissions.decision import Mode

__all__ = ["build_builtin_registry", "render_help", "parse_mode"]

# keyword-to-Mode map for each mode (lowercase) → Mode
_MODE_MAP: dict[str, Mode] = {
    "default": Mode.DEFAULT,
    "acceptedits": Mode.ACCEPT_EDITS,
    "accept": Mode.ACCEPT_EDITS,
    "plan": Mode.PLAN,
    "bypasspermissions": Mode.BYPASS,
    "bypass": Mode.BYPASS,
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def render_help(specs: list[CommandSpec]) -> str:
    """Produces plain-text multi-line help (command name + summary per line, simple alignment).

    Does not produce Rich objects — Rich rendering is left to the REPL side.
    """
    if not specs:
        return "(no commands available)"
    max_name_len = max(len(s.name) for s in specs)
    lines: list[str] = []
    for spec in specs:
        aliases_hint = ""
        if spec.aliases:
            aliases_hint = f" ({', '.join(spec.aliases)})"
        lines.append(f"  /{spec.name:<{max_name_len}}{aliases_hint}  {spec.summary}")
    return "\n".join(lines)


def parse_mode(arg: str) -> Mode | None:
    """Maps a string to Mode case-insensitively; returns None if it cannot be parsed.

    Supports:
    - ``"plan"``
    - ``"default"``
    - ``"acceptEdits"`` / ``"accept"``
    - ``"bypassPermissions"`` / ``"bypass"``
    """
    return _MODE_MAP.get(arg.strip().lower())


# ---------------------------------------------------------------------------
# Handler free functions (only call ctx.*, do not touch concrete implementation classes)
# ---------------------------------------------------------------------------


def _h_help(ctx, args: str) -> bool | None:  # noqa: ANN001
    """Display the list of visible command help."""
    ctx.print(render_help(ctx.visible_commands()))
    return None


def _h_status(ctx, args: str) -> bool | None:  # noqa: ANN001
    """Display the current status bar and last-round token usage."""
    status = ctx.status_line()
    usage = ctx.token_usage()
    lines = [status]
    if usage is not None:
        lines.append(f"Last-round token usage: {usage}")
    ctx.print("\n".join(lines))
    return None


def _h_memory(ctx, args: str) -> bool | None:  # noqa: ANN001
    """Display the long-term memory summary."""
    ctx.print(ctx.memory_summary())
    return None


def _h_compact(ctx, args: str) -> bool | None:  # noqa: ANN001
    """Trigger context compaction."""
    ctx.print(f"[dim]{ctx.compact_now()}[/dim]")
    return None


def _h_clear(ctx, args: str) -> bool | None:  # noqa: ANN001
    """Clear the current conversation context."""
    ctx.clear_context()
    ctx.print("Current conversation context has been cleared.")
    return None


def _h_plan(ctx, args: str) -> bool | None:  # noqa: ANN001
    """Switch to plan mode; if args provided, also send that message."""
    ctx.set_mode(Mode.PLAN)
    ctx.print("Entered plan mode (read-only tools). Use /do to exit")
    if args:
        ctx.send_user_message(args)
    return None


def _h_do(ctx, args: str) -> bool | None:  # noqa: ANN001
    """Switch back to default mode; if args provided, also send that message."""
    ctx.set_mode(Mode.DEFAULT)
    ctx.print("Exited plan mode, all tools restored")
    if args:
        ctx.send_user_message(args)
    return None


def _h_permission(ctx, args: str) -> bool | None:  # noqa: ANN001
    """Query / switch permission mode.

    No args: print current mode; with args: try to parse and switch, print available options if unrecognized.
    """
    stripped = args.strip()
    if not stripped:
        ctx.print(f"Current permission mode: {ctx.get_mode().value}")
        return None
    mode = parse_mode(stripped)
    if mode is None:
        options = ", ".join(m.value for m in Mode)
        ctx.print(f"Unrecognized mode {stripped!r}. Available options: {options}")
        return None
    ctx.set_mode(mode)
    ctx.print(f"Permission mode switched to: {mode.name}")
    return None


def _h_session(ctx, args: str) -> bool | None:  # noqa: ANN001
    """Session management sub-command router: new / list [--all] / resume <id>."""
    parts = args.strip().split()
    if not parts:
        ctx.print("Usage: /session new | list [--all] | resume <id>")
        return None
    sub = parts[0].lower()
    if sub == "new":
        ctx.new_session()
    elif sub == "list":
        all_projects = "--all" in args
        ctx.list_sessions(all_projects=all_projects)
    elif sub == "resume":
        if len(parts) < 2:
            ctx.print("Usage: /session resume <id>")
        else:
            ctx.resume_session(parts[1])
    else:
        ctx.print("Usage: /session new | list [--all] | resume <id>")
    return None


def _h_provider(ctx, args: str) -> bool | None:  # noqa: ANN001
    """Switch AI provider; prints usage hint if no args."""
    name = args.strip()
    if not name:
        ctx.print("Usage: /provider <provider-name>")
        return None
    ctx.switch_provider(name)
    return None


def _h_review(ctx, args: str) -> bool | None:  # noqa: ANN001
    """Send a code review request to AI."""
    target = args.strip() or "all uncommitted changes"
    ctx.send_user_message(f"Please review the uncommitted changes: {target}...")
    return None


def _h_exit(ctx, args: str) -> bool | None:  # noqa: ANN001
    """Exit the REPL."""
    return True


# ---------------------------------------------------------------------------
# /agents helper formatting functions (v0.13 · C116 · F101)
# ---------------------------------------------------------------------------

import time as _time  # noqa: E402 — stdlib only, layering rule permits


def _fmt_elapsed(created_at: float) -> str:
    """Convert a created_at timestamp to a relative time string (e.g., "3 minutes ago")."""
    delta = _time.time() - created_at
    if delta < 60:
        return f"{int(delta)} seconds ago"
    if delta < 3600:
        return f"{int(delta // 60)} minutes ago"
    if delta < 86400:
        return f"{int(delta // 3600)} hours ago"
    return f"{int(delta // 86400)} days ago"


def _fmt_usage(usage: dict) -> str:
    """Format a usage dict into a human-readable string; returns "-" for empty dicts."""
    if not usage:
        return "-"
    parts = []
    if "input_tokens" in usage:
        parts.append(f"in={usage['input_tokens']}")
    if "output_tokens" in usage:
        parts.append(f"out={usage['output_tokens']}")
    if not parts:
        # generic fallback
        parts = [f"{k}={v}" for k, v in usage.items()]
    return " ".join(parts)


def _format_task(task) -> str:  # noqa: ANN001
    """Format a single background task as a one-line list entry (id / label / status / relative time / usage).

    v0.13 · C116 · F101
    """
    status_str = (
        task.status.value if hasattr(task.status, "value") else str(task.status)
    )
    elapsed = _fmt_elapsed(task.created_at)
    usage_str = _fmt_usage(task.usage) if task.status.value != "running" else "-"
    return f"  {task.id}  {task.label}  [{status_str}]  {elapsed}  token:{usage_str}"


def _format_task_detail(task) -> str:  # noqa: ANN001
    """Format a single background task as detailed output (including full result text + usage).

    v0.13 · C116 · F101
    """
    status_str = (
        task.status.value if hasattr(task.status, "value") else str(task.status)
    )
    lines = [
        f"Task id: {task.id}",
        f"Name: {task.label}",
        f"Status: {status_str}",
        f"Created: {_fmt_elapsed(task.created_at)}",
        f"Usage: {_fmt_usage(task.usage)}",
        "---",
        f"Result:\n{task.result}",
    ]
    return "\n".join(lines)


def _h_agents(ctx, args: str) -> bool | None:  # noqa: ANN001
    """List background agent tasks (no args), or query details of a specific task (with id arg).

    v0.13 · C116 · F101/AC126 · N50
    """
    mgr = getattr(ctx, "agents_manager", lambda: None)()
    if mgr is None:
        ctx.print("agents not enabled")
        return None

    arg = args.strip()
    if not arg:
        # no args: list all tasks
        tasks = mgr.list()
        if not tasks:
            ctx.print("(no background tasks)")
            return None
        lines = ["Background agent task list:"]
        for t in tasks:
            lines.append(_format_task(t))
        ctx.print("\n".join(lines))
        return None

    # with arg: query details by id
    task = mgr.get(arg)
    if task is None:
        ctx.print(f"Task not found: {arg}")
        return None

    from wentian.agents.spec import TaskStatus  # noqa: PLC0415 — lazy import, layering permits

    if task.status == TaskStatus.RUNNING:
        ctx.print(f"Task {arg} is running, result not yet available")
        return None

    ctx.print(_format_task_detail(task))
    return None


# ---------------------------------------------------------------------------
# Factory function
# ---------------------------------------------------------------------------


def build_builtin_registry() -> CommandRegistry:
    """Register all 13 built-in commands in order and return a :class:`CommandRegistry`.

    v0.13 · C116 · F101: added the 13th command /agents (LOCAL type).
    """
    reg = CommandRegistry()

    reg.register(
        CommandSpec(
            name="help",
            summary="Display available command list",
            usage="/help",
            type=CommandType.LOCAL,
            handler=_h_help,
            aliases=("?", "h"),
        )
    )
    reg.register(
        CommandSpec(
            name="status",
            summary="Display current status bar and last-round token usage",
            usage="/status",
            type=CommandType.LOCAL,
            handler=_h_status,
            aliases=("st",),
        )
    )
    reg.register(
        CommandSpec(
            name="memory",
            summary="Display long-term memory summary",
            usage="/memory",
            type=CommandType.LOCAL,
            handler=_h_memory,
            aliases=("mem",),
        )
    )
    reg.register(
        CommandSpec(
            name="compact",
            summary="Trigger context compaction (saves tokens)",
            usage="/compact",
            type=CommandType.LOCAL,
            handler=_h_compact,
        )
    )
    reg.register(
        CommandSpec(
            name="clear",
            summary="Clear current conversation context (preserves session id)",
            usage="/clear",
            type=CommandType.UI_STATE,
            handler=_h_clear,
            aliases=("cls",),
        )
    )
    reg.register(
        CommandSpec(
            name="plan",
            summary="Switch to plan mode (optional: with content to plan)",
            usage="/plan [content]",
            type=CommandType.UI_STATE,
            handler=_h_plan,
            arg_hint="[content]",
        )
    )
    reg.register(
        CommandSpec(
            name="do",
            summary="Switch back to default mode (optional: with content to execute)",
            usage="/do [content]",
            type=CommandType.UI_STATE,
            handler=_h_do,
            arg_hint="[content]",
        )
    )
    reg.register(
        CommandSpec(
            name="permission",
            summary="Query / switch permission mode (default/acceptEdits/plan/bypassPermissions)",
            usage="/permission [mode]",
            type=CommandType.UI_STATE,
            handler=_h_permission,
            aliases=("perm",),
            arg_hint="[mode]",
        )
    )
    reg.register(
        CommandSpec(
            name="session",
            summary="Session management: new / list [--all] / resume <id>",
            usage="/session new | list [--all] | resume <id>",
            type=CommandType.UI_STATE,
            handler=_h_session,
            aliases=("sess",),
            arg_hint="new|list|resume <id>",
        )
    )
    reg.register(
        CommandSpec(
            name="provider",
            summary="Switch AI provider",
            usage="/provider <provider-name>",
            type=CommandType.UI_STATE,
            handler=_h_provider,
            arg_hint="<provider-name>",
        )
    )
    reg.register(
        CommandSpec(
            name="review",
            summary="Ask AI to review uncommitted changes",
            usage="/review [file or range]",
            type=CommandType.PROMPT,
            handler=_h_review,
            arg_hint="[file or range]",
        )
    )
    reg.register(
        CommandSpec(
            name="exit",
            summary="Exit WentianCode",
            usage="/exit",
            type=CommandType.LOCAL,
            handler=_h_exit,
            aliases=("quit", "q"),
        )
    )
    reg.register(
        CommandSpec(
            name="agents",
            summary="List background agent tasks; /agents <id> to view full result + usage",
            usage="/agents [id]",
            type=CommandType.LOCAL,
            handler=_h_agents,
            arg_hint="[id]",
        )
    )

    return reg
