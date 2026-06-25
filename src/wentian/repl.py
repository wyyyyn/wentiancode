"""REPL — main interactive loop for wentian.

Responsibilities:
- Read user input, dispatch slash commands or run one chat turn.
- v0.4 · C19 · F29 (task T55) — a chat turn is one multi-round
  :class:`~wentian.agent.loop.AgentLoop` run driven via ``asyncio.run``;
  :meth:`REPL._consume_agent` is the single meeting point between async
  agent events and the Rich renderer.
- Maintain conversation history in the session; persist per tool round
  (RoundEnd) and once at turn end; roll back the user message on zero
  progress (len-baseline rule).
- All dependencies injected: provider, session, store, renderer, console,
  input_fn — fully testable offline.

No anthropic/openai/yaml imports here, and NEVER ``wentian.tools`` —
registry/executor stay duck-typed; the AgentLoop coupling is by design
(repl drives the loop, the loop never imports UI).
"""

from __future__ import annotations

import asyncio
import datetime
import platform
import subprocess
from collections.abc import Callable
from pathlib import Path

from rich.console import Console, Group
from rich.table import Table
from rich.text import Text

from wentian.agent.events import (
    AgentDone,
    RoundEnd,
    RoundStart,
    StopReason,
    StreamEnd,
    ToolCallStarted,
    ToolResultReady,
    UsageUpdate,
)
from wentian.agent.loop import AgentLoop
from wentian.hooks.spec import HookEvent
from wentian.memory import extractor as _memory_extractor

# v0.6 · C38 · F47 (task T78) — assembly/UI layer may import permissions (pure leaf module).
from wentian.permissions.decision import MODE_CYCLE, Mode
from wentian.prompt.reminders import EnvInfo, build_request_decorator
from wentian.providers.base import Message, Provider, TextDelta, ThinkingDelta
from wentian.render import Renderer
from wentian.session import Session, SessionStore
from wentian.ui.confirm import Cancelled as _Cancelled
from wentian.ui.interrupt import InterruptListener, NullListener

__all__ = ["REPL"]


# v0.12 · C99 · F79 (task T124) — PreToolUse intercept result placeholder (duck-typed outcome).
# Compatible with tools.executor.ToolOutcome structure: is_error/content/tool_call_id/name/denied.
# Do not import tools package (layering rule: repl layer does not depend on tools).
class _HookDenyOutcome:
    """A tool outcome that represents a hook-denied call.

    Synthesised by the REPL when ``engine.pretool`` returns a deny reason;
    mirrors the ToolOutcome duck type so AgentLoop's history builder can
    consume it without knowing whether the denial came from the permission
    pipeline or the hook engine.
    """

    __slots__ = ("content", "is_error", "tool_call_id", "name", "denied")

    def __init__(self, *, reason: str, call_id: str, tool_name: str) -> None:
        self.content = reason
        self.is_error = True
        self.tool_call_id = call_id
        self.name = tool_name
        self.denied = True


# v0.6 · C37 · F48 (task T77) — friendly name → config rule prefix (for writing permanent rules).
# Equivalent to permissions.rules.FRIENDLY_TO_TOOL (only the forward friendly name set is needed here).
_FRIENDLY_NAMES = frozenset({"Bash", "Read", "Write", "Edit", "Glob", "Grep"})

_PROMPT = "wentian> "

#: v0.4 · C19 · F33 (task T56) — explicit allowlist of read-only tools for plan mode.
_PLAN_MODE_TOOLS = ("read_file", "find_files", "search_text")

#: Slash commands (invocation string, description) — rendering data source for /help, in display order.
_COMMANDS: tuple[tuple[str, str], ...] = (
    ("/help", "Show this help"),
    ("/new", "Start a new session"),
    ("/sessions", "List saved sessions"),
    ("/resume <id>", "Resume a session by id"),
    ("/provider <name>", "Switch backend provider"),
    ("/plan [text]", "Enter plan mode (read-only tools)"),
    ("/do [text]", "Exit plan mode (restore all tools)"),
    ("/compact", "Compact current conversation context"),
    ("/exit", "Exit wentian"),
)

#: Cinnabar — brand accent color shared with banner / cat face / tool dots.
_CINNABAR = "#C84B31"


def _current_git_branch() -> str | None:
    """Return the current Git branch name; silently return None for non-git repos or any error.

    Uses subprocess to call ``git rev-parse --abbrev-ref HEAD``;
    timeout 1 second, does not inherit stdin/stderr (safe fallback in test environments or non-git dirs).
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=1,
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
            return branch if branch else None
        return None
    except Exception:  # noqa: BLE001 — FileNotFoundError, TimeoutExpired, etc.
        return None


def _build_help() -> Group:
    """Build the styled renderable for /help: cinnabar command name + dim description in aligned two columns.

    The command name column is non-wrapping, ensuring long commands like ``/provider <name>``
    align correctly; tests only assert the command string appears, color scheme has no effect
    (auto-degrades to plain text in non-TTY/pipe environments).
    """
    grid = Table.grid(padding=(0, 3))
    grid.add_column(no_wrap=True)
    grid.add_column()
    for invocation, desc in _COMMANDS:
        grid.add_row(
            Text(invocation, style=f"bold {_CINNABAR}"),
            Text(desc, style="dim"),
        )
    return Group(Text("Available commands", style="bold"), grid)


def _persist_allow_rule(project_root: Path, rule_str: str) -> None:
    """v0.6 · C37 · F48 (task T77) — idempotently write a precise allow rule to the local layer config.

    Target file is the ``permissions.allow`` list in
    ``<project_root>/.wentian/settings.local.yaml``. Creates the file/directory if absent;
    preserves existing content; does not add a duplicate if the rule already exists (idempotent).
    Any I/O / parse error is silently swallowed — a failure to persist permanently must not
    interrupt the conversation (in-memory rules are already active for this session).
    """
    import yaml  # Local import: keep the repl module top-level free of yaml dependency (layering convention).

    try:
        wt = project_root / ".wentian"
        wt.mkdir(parents=True, exist_ok=True)
        path = wt / "settings.local.yaml"

        data: dict = {}
        if path.exists():
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded

        perms = data.get("permissions")
        if not isinstance(perms, dict):
            perms = {}
            data["permissions"] = perms
        allow = perms.get("allow")
        if not isinstance(allow, list):
            allow = []
            perms["allow"] = allow

        if rule_str not in allow:
            allow.append(rule_str)
            path.write_text(
                yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
    except (OSError, yaml.YAMLError):
        # Permanent persist failure is non-fatal: in-memory rules are active; this session is unaffected.
        return


def _rule_string(friendly: str, target: str) -> str:
    """Assemble (friendly_name, target) into a config rule string: ``Friendly(target)`` or bare ``Friendly``."""
    if target:
        return f"{friendly}({target})"
    return friendly


def _format_compaction_report(result) -> str:
    """v0.8 · C52 · F61/F62 (task T96) — render a CompactionResult into a human-readable report.

    Report order: number of offloaded items (first layer) → whether summarized (second layer) → circuit-breaker/failure status.
    Duck-typed: only reads ``offloaded`` / ``summarized`` / ``tripped`` /
    ``failed_this_call`` fields; does not import context package types.
    """
    parts: list[str] = []
    n_off = len(getattr(result, "offloaded", []) or [])
    if n_off:
        parts.append(f"Offloaded {n_off} oversized tool results")
    if getattr(result, "summarized", False):
        parts.append("Summarized earlier history")
    elif getattr(result, "failed_this_call", False):
        parts.append("Summary failed this call")
    if getattr(result, "tripped", False):
        parts.append("Weight-based summary tripped (subsequent auto rounds skipped)")
    if not parts:
        parts.append("Current context needs no compaction")
    return " · ".join(parts)


class REPL:
    """Interactive REPL.

    Parameters
    ----------
    provider:
        Active LLM backend (replaceable via /provider).
    session:
        Active conversation session.
    store:
        SessionStore for persistence.
    renderer:
        Renderer for displaying stream events.
    provider_factory:
        Callable(name) -> Provider — called by /provider; may raise ConfigError.
    input_fn:
        Callable used to read a line of user input (default: builtins.input).
    system:
        Optional system prompt passed to provider.stream(). Default None.
    interrupt_listener:
        v0.2 · C6 · F18 (task T23) — InterruptListener; from v0.4 · C19 · F29
        (task T55) onwards handed off to AgentLoop constructor injection, armed by
        the loop at the stream phase of each round.
        Default None → NullListener (yields None → no interrupt semantics).
    registry:
        v0.3 · C12 · F23 (task T42/T43) — optional ToolRegistry whose
        ``specs()`` is advertised to the provider. None → tools disabled,
        pure v0.2 behavior (the provider receives ``tools=None``).
    executor:
        v0.3 · C12 · F23 (task T42/T43) — optional ToolExecutor, from v0.4
        onwards called by AgentLoop in multi-round loops. Required (paired with ``registry``)
        for tools to actually execute; None → tools disabled.
    max_rounds:
        v0.4 · C19 · F29 (task T55) — AgentLoop per-turn round limit (runaway brake).
    plan_tools:
        v0.4 · C19 · F33 (task T56) — read-only tool allowlist for plan mode: the tools
        declaration for rounds after /plan is filtered by name to this list, and the same
        list is injected as ``allowed_tools`` into AgentLoop (double safety).
        Constructor parameter can be overridden for testing.
    """

    def __init__(
        self,
        provider: Provider,
        session: Session,
        store: SessionStore,
        renderer: Renderer,
        *,
        provider_factory: Callable[[str], Provider],
        input_fn: Callable[..., str] = input,
        system: str | None = None,
        interrupt_listener: InterruptListener | None = None,
        # Typed as object on purpose: repl stays decoupled from wentian.tools
        # (duck-typed at call sites; see plan.md C12 layering note).
        registry: object | None = None,
        executor: object | None = None,
        max_rounds: int = 20,
        plan_tools: tuple[str, ...] = _PLAN_MODE_TOOLS,
        # v0.6 · C37 · F48 (task T77) — permission pipeline (duck-typed: uses decide /
        # project_root / settings). None ⇒ no permission gate, v0.5 behavior (regression-safe).
        pipeline: object | None = None,
        # v0.6 · C37 · F48 (task T77) — human-in-the-loop async approval callback; defaults to
        # wrapping ui.confirm.confirm_action; tests can inject a fake callback returning Choice / raising Cancelled.
        confirm_fn: Callable[..., object] | None = None,
        # v0.6 · C38 · F47 (task T78) — initial permission mode; T79 injected from settings.default_mode,
        # default Mode.DEFAULT. Mode lives in REPL state → persists across rounds (not reset by /new //resume //provider), lives in
        # REPL state → naturally persists across rounds (AC49).
        default_mode: Mode = Mode.DEFAULT,
        # v0.7 · C46 · F55/N23 (task T88) — MCPManager for lifecycle management.
        # None when no mcpServers configured (N23: zero behavior change).
        mcp_manager: object | None = None,
        # v0.8 · C52 · F61/F62 (task T96) — two-layer context compactor
        # (duck-typed: only .compact / .set_provider / .set_artifacts_dir used).
        # None ⇒ no compaction, byte-level v0.7 behavior (N25, regression-safe).
        compactor: object | None = None,
        # v0.9 · C58 · F67/N29 (task T106) — background memory runner (duck-typed:
        # only .submit / .close used). None ⇒ no extraction, v0.8 behavior.
        memory_runner: object | None = None,
        # v0.9 · C55 · F65/N29 (task T106) — one-shot resume time-gap reminder
        # string injected via the <system-reminder> channel on the FIRST turn
        # after resume, then cleared. Never written back to session.messages /
        # persisted. None ⇒ no reminder, v0.8 behavior.
        resume_reminder: str | None = None,
        # v0.10 · C91 · F73/F76/N35 (task T114) — slash command registry (duck-typed:
        # only .lookup / .visible used). None ⇒ fall back to v0.9 hard-coded dict dispatch (regression-safe).
        commands: object | None = None,
        # v0.10 · C91 · F74 (task T114) — long-term memory store (duck-typed: only
        # .read_indexes_for_injection / .user_dir / .project_dir used) for /memory display.
        # None ⇒ /memory shows "long-term memory not enabled".
        memory_store: object | None = None,
        # v0.11 · C104 · F73/F87 (task T134a) — Skill activation orchestrator (duck-typed: only
        # .active_bodies / .allowed_tools / .clear used). None ⇒ no skill body injection,
        # no skill allowlist narrowing, /clear · /new do not clear activation set — byte-level v0.10 behavior (regression-safe).
        activator: object | None = None,
        # v0.12 · C99 · F78/F79/F83 (task T124) — HookEngine (duck-typed: only
        # .fire / .pretool / .drain_injections / .close used). None ⇒ all seam operations are no-ops,
        # byte-level equivalent to v0.11 (regression-safe, N40).
        hooks: object | None = None,
        # v0.13 · C117 · F98/F99 (task T145) — BackgroundTaskManager (duck-typed: only
        # .drain_completions / .close used). None ⇒ no background task result feed-back, byte-level equivalent to v0.12
        # (regression-safe, N50).
        agents_manager: object | None = None,
    ) -> None:
        self._provider = provider
        self._session = session
        self._store = store
        self._renderer = renderer
        self._provider_factory = provider_factory
        self._input_fn = input_fn
        self._system = system
        # Default resolved here (not in the signature) to avoid a shared
        # mutable default instance across REPLs.
        self._interrupt_listener: InterruptListener = (
            interrupt_listener if interrupt_listener is not None else NullListener()
        )
        # v0.3 · C12 · F23 (task T42/T43) — tools are enabled only when both a
        # registry and an executor are injected; either missing → v0.2 path.
        self._registry = registry
        self._executor = executor
        # v0.4 · C19 · F29 (task T55) — loop configuration.
        self._max_rounds = max_rounds
        self._plan_tools = tuple(plan_tools)
        # v0.6 · C38 · F47 (task T78) — permission mode unified as a single REPL state:
        # Shift+Tab cycles through MODE_CYCLE; /plan·/do are the dedicated entry/exit points for the plan tier.
        # Mode is a UI policy (not persisted, not reset on /new //resume //provider), stored in
        # REPL state → naturally persists across rounds (AC49). Original self._plan_mode consolidated as
        # derived property ``self._mode == Mode.PLAN`` (see property below).
        self._mode: Mode = default_mode
        # v0.6 · C37 · F48 (task T77) — permission gate assembly components.
        self._pipeline = pipeline
        self._confirm_fn = confirm_fn
        self._console: Console = renderer.console
        # v0.7 · C46 · F55/N23 (task T88) — MCPManager lifecycle holder.
        # When None, the close_all call in run() exit paths is silently skipped (N23).
        self._mcp_manager = mcp_manager
        # v0.8 · C52 · F61/F62 (task T96) — context compactor (duck-typed). None ⇒
        # no pre_round_compact hook injection, byte-level equivalent to v0.7 (N25). _last_round_usage
        # is refreshed by _consume_agent on UsageUpdate as anchor point for next-round compaction estimate;
        # initial value None (no anchor on first round, estimate falls back to full character conversion).
        self._compactor = compactor
        self._last_round_usage: object | None = None
        # v0.9 · C58 · F67 (task T106) — background memory extraction (duck-typed). None ⇒ no extraction.
        self._memory_runner = memory_runner
        # v0.9 · C55 · F65 (task T106) — one-shot resume time-gap reminder; injected on first round then cleared.
        self._resume_reminder = resume_reminder
        # v0.10 · C91 · F73/F74 (task T114) — command registry + memory store (duck-typed).
        self._commands = commands
        self._memory_store = memory_store
        # v0.11 · C104 · F73/F87 (task T134a) — Skill activation orchestrator (duck-typed).
        # None ⇒ no skill body injection, no skill allowlist narrowing, /clear · /new do not clear activation set.
        self._activator = activator
        # v0.12 · C99 · F78/F79/F83 (task T124) — HookEngine (duck-typed; None = no hooks).
        self._hooks = hooks
        # v0.13 · C117 · F98/F99 (task T145) — background task manager (duck-typed; None = not enabled).
        # drain_completions() is called once per round inside request_decorator; results are fed back via <system-reminder>
        # into the request copy (never written back to session.messages, never persisted, N50).
        self._agents_manager = agents_manager
        # v0.9 · C54 · F64 (task T106) — append-write cursor: number of already-persisted messages. Resumed sessions
        # use the current in-memory message count as base (those lines are already on disk); new sessions start at 0.
        # RoundEnd / turn end now uses store.append(messages[cursor:]) for incremental append (F64: crash loses only last line).
        self._persisted_count = len(session.messages)
        # v0.9 review fix (Major #1) — content fingerprint of the already-persisted prefix. In normal pure-append
        # mode it advances with the cursor; when context compaction (offload) rewrites message content **below**
        # the cursor in place (list length unchanged, invisible to the append path), the fingerprint changes →
        # triggers a one-time full atomic save, otherwise the disk retains old text, the whole segment is
        # re-injected on resume, and offload is ineffective.
        self._persisted_fingerprint: list[int] = self._fingerprint(
            session.messages[: self._persisted_count]
        )

    # ------------------------------------------------------------------
    # v0.6 · C38 · F47 (task T78) — permission mode state
    # ------------------------------------------------------------------

    @property
    def _plan_mode(self) -> bool:
        """Derived plan mode property: ``self._mode == Mode.PLAN``.

        All read sites from F33 (declaration filtering / allowed_tools / plan reminder decorator)
        continue to read this boolean, behavior unchanged — only the truth source is consolidated
        from a standalone boolean into the unified ``self._mode``.
        """
        return self._mode is Mode.PLAN

    def get_mode(self) -> Mode:
        """Return the current permission mode (directly reused as permission gate get_mode callback, see _build_gate)."""
        return self._mode

    def cycle_mode(self) -> None:
        """Shift+Tab: advance self._mode to the next tier in MODE_CYCLE (wraps around at the end).

        Mode lives in REPL state → persists across rounds (AC49). The bottom toolbar is a
        callable recomputed on every prompt (reads live status_line), so switching is reflected
        automatically on the next render.
        """
        idx = MODE_CYCLE.index(self._mode)
        self._mode = MODE_CYCLE[(idx + 1) % len(MODE_CYCLE)]

    # ------------------------------------------------------------------
    # v0.10 · C91 · F73/F74/F76 (task T114) — CommandContext protocol implementation
    #
    # REPL acts as the assembly/UI layer implementing the commands.context.CommandContext
    # protocol (duck-typed, no explicit inheritance needed — @runtime_checkable structural
    # check is sufficient); handlers in commands/ depend only on this protocol surface,
    # never directly importing REPL/agent/provider. Print responsibility is described
    # in each method's docstring below.
    # ------------------------------------------------------------------

    def print(self, renderable: object) -> None:
        """Print *renderable* to the terminal (string or Rich Renderable) — delegated to console."""
        self._console.print(renderable)

    def send_user_message(self, text: str) -> None:
        """Send *text* as a user message into the conversation, triggering one AI turn (reuses :meth:`_chat_once`).

        The sole exit point for PROMPT-type commands (e.g. /review); semantically identical
        to the user typing the text directly.
        """
        self._chat_once(text)

    def set_mode(self, mode: Mode) -> None:
        """Switch permission mode (pure operation, no printing — confirmation text is the handler's responsibility)."""
        self._mode = mode

    def token_usage(self) -> object | None:
        """Return the last-round token usage snapshot (``self._last_round_usage``; None if no history)."""
        return self._last_round_usage

    def memory_summary(self) -> str:
        """Return a read-only string with long-term memory directories and per-domain INDEX summaries.

        When ``memory_store`` is injected, assembles "memory directories (user_dir / project_dir) +
        read_indexes_for_injection() text (truncated to a reasonable length)"; when not injected,
        returns a "long-term memory not enabled" message. store is duck-typed: reads public user_dir / project_dir.
        """
        if self._memory_store is None:
            return "(long-term memory not enabled)"
        store = self._memory_store
        user_dir = getattr(store, "user_dir", None)
        project_dir = getattr(store, "project_dir", None)
        lines = ["Long-term memory directories:"]
        lines.append(f"  user    : {user_dir}")
        lines.append(f"  project : {project_dir}")
        index_text = ""
        try:
            index_text = store.read_indexes_for_injection() or ""
        except Exception:  # noqa: BLE001 — reading INDEX failure is non-fatal; only body is missing.
            index_text = ""
        index_text = index_text.strip()
        if index_text:
            # Truncate to a reasonable length (avoid screen overflow; INDEX is one summary per line, 2000 chars is enough).
            if len(index_text) > 2000:
                index_text = index_text[:2000] + "…(truncated)"
            lines.append("")
            lines.append(index_text)
        else:
            lines.append("")
            lines.append("(no memory entries yet)")
        return "\n".join(lines)

    def visible_commands(self) -> list:
        """Return the list of currently visible commands (``commands.visible()``; returns [] when not injected)."""
        return self._commands.visible() if self._commands is not None else []

    def refresh_skill_menu(self, new_system: str | None) -> None:
        """v0.11 · C107b · F73 (task T134b) — after ``/skills reload``, swap in the rebuilt system prompt
        (containing the refreshed "Available Skills" menu) in real time.

        Replaces ``self._system`` entirely with *new_system*. If the ``activator``'s
        ``get_main_system`` callback has closed over the same system holder, this method does not
        directly touch that holder — the reload closure in the assembly layer (build_app) is
        responsible for syncing the holder, keeping the two consistent.
        No-op when ``new_system`` is None (conservative preservation, does not clear the existing system prompt).
        """
        if new_system is None:
            return
        self._system = new_system

    def clear_context(self) -> None:
        """/clear semantics: clear current session messages, **keeping the same session id** (AC92).

        Pure operation, no printing (confirmation output is ``_h_clear``'s responsibility):
        clears ``session.messages``, resets the append-write cursor / fingerprint / last-round usage,
        and overwrites the on-disk file with an empty history (same id unchanged).
        """
        self._session.messages.clear()
        self._persisted_count = 0
        self._persisted_fingerprint = []
        self._last_round_usage = None
        self._store.save(self._session)
        # v0.11 · C104 · F73/F87 (task T134a) — clear Skill activation set (duck-typed).
        if self._activator is not None:
            self._activator.clear()

    # ------------------------------------------------------------------
    # v0.12 · C99 · F78/F79/F83 (task T124) — Hook helper methods
    # ------------------------------------------------------------------

    def _fire_hook(self, event: HookEvent, ctx: dict) -> None:
        """Fire a hook event; fail-safe (None guard + try/except)."""
        if self._hooks is None:
            return
        try:
            self._hooks.fire(event, ctx)
        except Exception:  # noqa: BLE001 — hook failure must never affect dialogue
            pass

    def _drain_injections(self) -> str:
        """Drain pending hook injections; returns '' when hooks is None."""
        if self._hooks is None:
            return ""
        try:
            return self._hooks.drain_injections() or ""
        except Exception:  # noqa: BLE001
            return ""

    def _pretool_check(self, call) -> str | None:
        """Run pretool check; fail-open (returns None) on any exception."""
        if self._hooks is None:
            return None
        try:
            args = call.arguments if isinstance(call.arguments, dict) else {}
            return self._hooks.pretool(
                {
                    "event": "PreToolUse",
                    "cwd": str(Path.cwd()),
                    "session_id": self._session.id,
                    "tool_name": call.name,
                    "tool_call_id": call.id,
                    # v0.12 · C99 · F79 — flatten common tool parameters for fine-grained security
                    # policy matching on command / file_path (e.g. regex-block dangerous command strings).
                    "command": str(args.get("command", "")),
                    "file_path": str(args.get("file_path") or args.get("path") or ""),
                    "arguments": args,
                }
            )
        except Exception:  # noqa: BLE001 — fail-open: pretool exception = no block
            return None

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Enter the REPL loop; returns when the user types /exit or sends EOF.

        v0.7 · C46 · F55/N23 (task T88) — All exit paths (normal /exit,
        EOFError/KeyboardInterrupt, unexpected exception) call
        ``_mcp_manager.close_all()`` via try/finally so MCP subprocess
        connections are never leaked.  When ``_mcp_manager`` is None the
        finally block is a no-op (N23).
        """
        import atexit

        # atexit fallback: guards against finally not executing (e.g. os._exit / external kill).
        # v0.9 · C58 (task T106) — memory runner is also closed in the fallback (short join, daemon
        # thread does not block exit).
        # v0.12 · C99 (task T124) — hook engine is also closed in the fallback.
        if (
            self._mcp_manager is not None
            or self._memory_runner is not None
            or self._hooks is not None
            or self._agents_manager is not None
        ):
            _manager_ref = self._mcp_manager
            _runner_ref = self._memory_runner
            _hooks_ref = self._hooks
            _agents_mgr_ref = self._agents_manager

            def _atexit_close() -> None:
                if _manager_ref is not None:
                    try:
                        _manager_ref.close_all()
                    except Exception:  # noqa: BLE001
                        pass
                if _runner_ref is not None:
                    try:
                        _runner_ref.close()
                    except Exception:  # noqa: BLE001
                        pass
                if _hooks_ref is not None:
                    try:
                        _hooks_ref.close()
                    except Exception:  # noqa: BLE001
                        pass
                if _agents_mgr_ref is not None:
                    try:
                        _agents_mgr_ref.close()
                    except Exception:  # noqa: BLE001
                        pass

            atexit.register(_atexit_close)

        # v0.12 · C99 (task T124) — SESSION_START seam: session start event.
        self._fire_hook(HookEvent.SESSION_START, {"session_id": self._session.id})

        try:
            while True:
                try:
                    raw = self._input_fn(_PROMPT)
                except (EOFError, KeyboardInterrupt):
                    self._console.print()
                    return

                line = raw.strip()

                if not line:
                    continue

                if line.startswith("/"):
                    should_exit = self._dispatch_command(line)
                    if should_exit:
                        return
                else:
                    self._chat_once(line)
        finally:
            if self._mcp_manager is not None:
                self._mcp_manager.close_all()
            # v0.9 · C58 (task T106) — short-join background extraction thread on all exit paths.
            if self._memory_runner is not None:
                try:
                    self._memory_runner.close()
                except Exception:  # noqa: BLE001 — exit cleanup failure is non-fatal
                    pass
            # v0.12 · C99 (task T124) — SESSION_END seam + close engine.
            self._fire_hook(HookEvent.SESSION_END, {"session_id": self._session.id})
            if self._hooks is not None:
                try:
                    self._hooks.close()
                except Exception:  # noqa: BLE001
                    pass
            # v0.13 · C117 (task T145) — close background task manager (short-join daemon thread, no leak).
            if self._agents_manager is not None:
                try:
                    self._agents_manager.close()
                except Exception:  # noqa: BLE001
                    pass

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    def _chat_once(self, user_text: str) -> None:
        """v0.4 · C19 · F29 (task T55) — one user turn = one AgentLoop run.

        A tool-free turn is simply a loop where "round 1 is already COMPLETED" —
        there is no separate pure-chat path. Flow: append user → record len-baseline →
        ``asyncio.run`` drives :meth:`_consume_agent` to consume loop events → finalize
        by the len-baseline rule:

        - After the loop, if messages length is still == baseline ⇒ zero progress
          (zero-text interrupt / first-round stream error): pop the unanswered question,
          do not persist (history has no unanswered question, AC16 continuity);
        - Otherwise persist. Per-round persist (tool side effects have truly occurred,
          must not lose on crash) happens at RoundEnd in :meth:`_consume_agent`.

        All interrupt/stop semantics live in AgentLoop (USER_CANCELLED partial text stored
        as text only, STREAM_ERROR whole round discarded with exception never escaping,
        MAX_ROUNDS/UNKNOWN_TOOL_LOOP brakes); this method handles only rollback and persistence.
        ``KeyboardInterrupt`` fallback: loop enters history atomically per round, so history
        is always pair-consistent at this point; finalize by the same len-baseline rule.

        executor-absent decision (maintaining v0.3 external behavior): when registry is present,
        tools= is still passed to the provider (declaration ≠ execution, v0.3 existing contract),
        but the loop runs with registry=None / executor=None / max_rounds=1 — if the model still
        requests tools, round 1 triggers the MAX_ROUNDS brake: only text is stored, nothing is
        executed, no unanswered tool_use is added to history; equivalent to v0.3's
        "executor=None ⇒ ignore tool_calls and take the plain-text path"; the corresponding
        limit notice is suppressed with ``limit_notice=False`` (v0.3 was silent in this scenario).
        """
        tools = self._effective_tools()
        system = self._system

        # v0.5 · C22 · F35/F39 (task T66) — build request_decorator: before each round is sent,
        # inject environment info + plan-mode toggle reminder into the message channel (<system-reminder>
        # tag), never written back to session.messages (persistence is clean, AC37).
        env = EnvInfo(
            cwd=Path.cwd(),
            os=platform.system(),
            date=datetime.date.today().isoformat(),
            git_branch=_current_git_branch(),
        )
        # v0.11 · C104 · F73/F87 (task T134a) — when activator is present, bind its active_bodies
        # method (live callback) to the decorator: each round request reads the currently activated
        # skill bodies in real time and injects them via the <system-reminder> channel into the last
        # user message (never written back to session.messages).
        active_skill_bodies = (
            self._activator.active_bodies if self._activator is not None else None
        )
        base_decorator = build_request_decorator(
            env=env,
            plan_mode=self._plan_mode,
            active_skill_bodies=active_skill_bodies,
        )

        # v0.9 · C55 · F65 (task T106) — one-shot resume time-gap reminder: injected once via the
        # <system-reminder> channel on the first round after resume, then cleared. Never written back
        # to session.messages, not persisted (same structure as env/plan reminders — lives only in
        # the request copy for this turn).
        reminder = self._resume_reminder
        self._resume_reminder = None  # Cleared on retrieval: injected only once this round
        reminder_block = (
            f"<system-reminder>\n{reminder}\n</system-reminder>"
            if reminder is not None
            else None
        )

        # v0.12 · C99 · F79/F83 (task T124) — injection channel: each round, hook-accumulated
        # injection text (drain_injections) is injected into the current request copy via the
        # <system-reminder> channel, never written back to session.messages, not persisted.
        # Hook injections are "inject when present" per-round — SessionStart / UserPromptSubmit
        # injections land on round 1, PostToolUse / RoundEnd injections land on the next round;
        # the resume time reminder (reminder_block) is still injected only on round 1.
        # No hooks and no reminder ⇒ decorator = base_decorator (byte-level equivalent to v0.11, N40).
        # v0.13 · C117 · F99 (task T145) — when agents_manager is non-None, it also needs to enter
        # the decorator build path (drain_completions feeds back background task completion notices each round).
        if (
            self._hooks is None
            and reminder_block is None
            and self._agents_manager is None
        ):
            decorator = base_decorator
        else:

            def decorator(messages: list[Message], round_index: int) -> list[Message]:
                result = base_decorator(messages, round_index)
                extra_blocks: list[str] = []
                injection = self._drain_injections()
                if injection:
                    extra_blocks.append(
                        f"<system-reminder>\n{injection}\n</system-reminder>"
                    )
                # v0.13 · C117 · F99 (task T145) — drain background task completion feed-back (runs every round).
                if self._agents_manager is not None:
                    completions = self._agents_manager.drain_completions()
                    if completions:
                        extra_blocks.append(
                            f"<system-reminder>\n{completions}\n</system-reminder>"
                        )
                if reminder_block is not None and round_index == 1:
                    extra_blocks.append(reminder_block)
                if not extra_blocks:
                    return result
                # Append to the content of the last user message in the request copy (never touches the original dict).
                last_user_idx: int | None = None
                for i, msg in enumerate(result):
                    if msg.get("role") == "user":
                        last_user_idx = i
                if last_user_idx is None:
                    return result
                copied = dict(result[last_user_idx])
                copied["content"] = (
                    copied.get("content", "") + "\n" + "\n".join(extra_blocks)
                )
                new_result = list(result)
                new_result[last_user_idx] = copied
                return new_result

        user_msg: Message = {"role": "user", "content": user_text}
        self._session.messages.append(user_msg)
        baseline = len(self._session.messages)

        # v0.12 · C99 · F78 (task T124) — USER_PROMPT_SUBMIT seam.
        self._fire_hook(
            HookEvent.USER_PROMPT_SUBMIT,
            {"prompt": user_text, "session_id": self._session.id},
        )

        # v0.8 · C52 · F61/F62/N25 (task T96) — use the compactor's compact (manual=False,
        # auto margin) as the loop's pre_round_compact write-back hook. compactor is None ⇒
        # no hook passed; AgentLoop is byte-level equivalent to v0.7 (regression-safe).
        pre_round_compact = (
            self._compactor.compact if self._compactor is not None else None
        )

        tools_enabled = self._registry is not None and self._executor is not None
        if tools_enabled:
            # v0.4 · C19 · F33 (task T56) — plan mode double safety part 2: same list injected as
            # allowed_tools into the loop; calls outside the list are blocked by the loop with a
            # blocked result (declaration filtering blocks guided requests, blocked intercept blocks hard attempts).
            # v0.11 · C104 · F73/F87 (task T134a) — combined with skill allowlist (intersection = strictest wins,
            # load_skill always preserved), see _combine_allowed_tools.
            plan_allowed = frozenset(self._plan_tools) if self._plan_mode else None
            skill_allowed = (
                self._activator.allowed_tools() if self._activator is not None else None
            )
            allowed_tools = self._combine_allowed_tools(plan_allowed, skill_allowed)
            agent = AgentLoop(
                self._provider,
                registry=self._registry,
                executor=self._executor,
                interrupt_listener=self._interrupt_listener,
                max_rounds=self._max_rounds,
                allowed_tools=allowed_tools,
                # v0.6 · C37 · F48 (task T77) — attach permission gate only when pipeline is present;
                # None ⇒ no gate, v0.5 behavior (regression-safe).
                permission_gate=self._build_gate(),
            )
        else:
            # Pure chat loop (see docstring's executor-absent decision).
            agent = AgentLoop(
                self._provider,
                registry=None,
                executor=None,
                interrupt_listener=self._interrupt_listener,
                max_rounds=1,
                allowed_tools=None,
            )

        try:
            done = asyncio.run(
                self._consume_agent(
                    agent.run(
                        self._session.messages,
                        system=system,
                        tools=tools,
                        request_decorator=decorator,
                        pre_round_compact=pre_round_compact,
                    ),
                    limit_notice=tools_enabled,
                )
            )
        except KeyboardInterrupt:
            # Loop enters history atomically per round ⇒ history is pair-consistent at this point; finalize by same rule.
            done = None
        except _Cancelled:
            # v0.6 · C37 · F48/N13 (task T77) — human-in-the-loop pressed Esc/Ctrl+C to cancel:
            # clean end of this turn, do not exit the program, no task leak (asyncio.run has
            # already wound down this turn's event loop and pending tasks). History enters atomically
            # per round ⇒ is pair-consistent at this point; finalize by same len-baseline rule
            # (zero progress rolls back the unanswered question).
            done = None
            self._console.print("[yellow dim]Tool confirmation cancelled[/yellow dim]")

        if len(self._session.messages) == baseline:
            # Zero progress → roll back the unanswered question, do not persist or extract memory.
            self._session.messages.pop()
            return

        # End-of-turn incremental persist (F64: incremental append; per-round writes in RoundEnd are not repeated).
        self._persist_pending()

        # v0.9 · C58 · F67 (task T106) — fire-and-forget background extraction after a COMPLETED round.
        # Duck-typed call: runner is None → skip (regression to v0.8); submit returns immediately, never
        # blocks; extraction exceptions are silently swallowed by the runner and do not affect this turn.
        if (
            self._memory_runner is not None
            and done is not None
            and done.stop_reason is StopReason.COMPLETED
        ):
            try:
                window = _memory_extractor.build_recent_window(self._session.messages)
                self._memory_runner.submit(window)
            except Exception:  # noqa: BLE001 — extraction dispatch must never affect the main dialogue flow
                pass

    def _effective_tools(self) -> list | None:
        """v0.5 · C22 · F35/F39 (task T66) — effective tools declaration for this turn.

        No registry → None (pure v0.2 behavior, plan mode toggle has no effect).
        Registry present and plan mode active → specs filtered by name to ``self._plan_tools``
        (declaration filtering blocks guided requests); otherwise full specs.

        Note: in v0.4, this method used to return (tools, system) and append a plan-mode suffix
        to system (old F33 implementation). From v0.5, system stays stable — plan mode reminders
        are carried by the <system-reminder> message channel produced by build_request_decorator
        (AC40); so this method has been narrowed to only determine tools.
        """
        if self._registry is None:
            return None
        specs = self._registry.specs()
        if not self._plan_mode:
            return specs
        allowed = set(self._plan_tools)
        return [spec for spec in specs if spec.name in allowed]

    @staticmethod
    def _combine_allowed_tools(
        plan_allowed: frozenset[str] | None,
        skill_allowed: frozenset[str] | None,
    ) -> frozenset[str] | None:
        """v0.11 · C104 · F73/F87 (task T134a) — combine plan mode and skill allowlists.

        Rules (strictest wins, ``load_skill`` always preserved):

        - Both None → None (no narrowing).
        - Exactly one is None → return the other (one-sided narrowing).
        - Both are sets → ``(plan & skill) | {"load_skill"}`` (intersection = strictest,
          but ``load_skill`` is always callable so the model can switch/load Skills at any time).

        Plan mode read-only constraints and skill allowlist are not mutually exempt:
        when plan is present its read-only constraint still applies, and the skill
        allowlist further narrows on top of that.
        """
        if plan_allowed is None and skill_allowed is None:
            return None
        if plan_allowed is None:
            return skill_allowed
        if skill_allowed is None:
            return plan_allowed
        return (plan_allowed & skill_allowed) | {"load_skill"}

    # ------------------------------------------------------------------
    # v0.6 · C37 · F48 (task T77) — human-in-the-loop permission gate assembly
    # ------------------------------------------------------------------

    def _build_permission_gate(self):
        """Construct the async ``permission_gate`` injected into AgentLoop, or None (no pipeline).

        When pipeline is present, uses ``ui.confirm`` (or the injected ``confirm_fn``) as the ask
        callback (including Esc/Ctrl+C for clean turn cancellation, N13), and builds a closure via
        :func:`build_permission_gate`; ``get_mode`` directly returns the unified ``self._mode``
        (v0.6 · C38 · F47 · task T78: removed the T77 temporary plan-boolean mapping).
        Returns None when pipeline is absent ⇒ v0.5 behavior.
        """
        if self._pipeline is None:
            return None

        # Assembly layer import (permission_gate module is cross-layer; may import permissions+tools+ui).
        from wentian.permission_gate import build_permission_gate

        def get_mode() -> Mode:
            return self._mode

        async def ask(call, decision):
            # v0.12 · C99 · T124 — fire NOTIFICATION on ASK verdict.
            self._fire_hook(
                HookEvent.NOTIFICATION,
                {
                    "kind": "permission_ask",
                    "tool_name": call.name,
                    "reason": getattr(decision, "reason", ""),
                },
            )
            # Key parameter preview: command string or path (extracted from arguments, falls back to full args).
            preview = self._preview_args(call)
            return await self._confirm(
                tool_name=call.name,
                preview=preview,
                reason=getattr(decision, "reason", "") or "Confirmation required for this tool call",
            )

        def on_allow_always(friendly: str, target: str, is_path: bool) -> None:
            self._persist_always_rule(friendly, target, is_path)

        return build_permission_gate(
            pipeline=self._pipeline,
            registry=self._registry,
            ask=ask,
            get_mode=get_mode,
            on_allow_always=on_allow_always,
        )

    def _build_gate(self):
        """v0.12 · C99 · F79 (task T124) — composite permission gate: hook pretool + permission gate.

        Gate combination logic:
          1. For each tool call, first check the hook engine pretool: if a deny reason string is
             returned → synthesize a _HookDenyOutcome short-circuit (skip permission gate / executor).
          2. pretool returns None → fall through to the existing permission gate (v0.6 behavior unchanged).
          3. hooks=None or pretool raises an exception → fail-open, use the original permission gate.
        No pipeline and no hooks → return None (v0.5 behavior).
        """
        perm_gate = self._build_permission_gate()

        # If no hooks, return the original permission gate directly (zero new overhead).
        if self._hooks is None:
            return perm_gate

        # With hooks: wrap with a pretool check layer.
        async def gate_with_pretool(call) -> object | None:
            # PreToolUse hook runs before the permission gate.
            reason = self._pretool_check(call)
            if reason is not None:
                # Blocked by hook → synthesize a deny outcome, short-circuit.
                return _HookDenyOutcome(
                    reason=reason,
                    call_id=call.id,
                    tool_name=call.name,
                )
            # pretool passes → go through original permission gate (None = pass if no gate).
            if perm_gate is not None:
                return await perm_gate(call)
            return None

        return gate_with_pretool

    async def _confirm(self, *, tool_name: str, preview: str, reason: str):
        """Call the injected confirm_fn, otherwise use the default ui.confirm.confirm_action."""
        if self._confirm_fn is not None:
            return await self._confirm_fn(
                tool_name=tool_name, preview=preview, reason=reason
            )
        from wentian.ui.confirm import confirm_action

        return await confirm_action(tool_name=tool_name, preview=preview, reason=reason)

    @staticmethod
    def _preview_args(call) -> str:
        """Pick a short, human-readable preview string from the tool call arguments."""
        args = getattr(call, "arguments", None)
        if not isinstance(args, dict) or not args:
            return ""
        # Prefer command / path / first string value.
        for key in ("command", "path", "pattern", "file_path"):
            value = args.get(key)
            if isinstance(value, str) and value:
                return value
        for value in args.values():
            if isinstance(value, str) and value:
                return value
        return ""

    def _persist_always_rule(self, friendly: str, target: str, is_path: bool) -> None:
        """ALLOW_ALWAYS: persist to disk + take effect in memory immediately.

        - Permanently: write the precise rule to the local layer settings.local.yaml (idempotent);
        - Immediately: append to pipeline's LayeredRules.local (takes effect in this session right away).
        """
        if friendly not in _FRIENDLY_NAMES:
            return
        rule_str = _rule_string(friendly, target)

        # 1) Persist permanently (project root comes from pipeline.project_root).
        project_root = getattr(self._pipeline, "project_root", None)
        if project_root is not None:
            _persist_allow_rule(Path(project_root), rule_str)

        # 2) Take effect in memory immediately: append Rule to the local layer.
        try:
            from wentian.permissions.decision import Verdict
            from wentian.permissions.rules import Rule

            rules = self._pipeline.settings.rules  # LayeredRules
            local = rules.local  # RuleSet (mutable)
            pattern = target if target else None
            new_rule = Rule(friendly=friendly, pattern=pattern, effect=Verdict.ALLOW)
            if new_rule not in local.allow:
                local.allow.append(new_rule)
        except Exception:  # noqa: BLE001 — in-memory append failure is non-fatal (already persisted).
            return

    async def _consume_agent(
        self, events, *, limit_notice: bool = True
    ) -> AgentDone | None:
        """v0.4 · C19 · F29 (task T55) — async events → render/persistence mapper.

        The sole meeting point between the async world and Rich. Mapping:
        RoundStart → new StreamView armed with spinner; Thinking/TextDelta → view.feed;
        StreamEnd → view.finish (interrupt marker printed here); ToolCallStarted/ToolResultReady
        → ⏺/⎿ lines; RoundEnd (with tool results) → per-round persist; AgentDone → captured
        as final value. UsageUpdate (v0.8 · C52 · F61/F62 · task T96) → stored in
        ``self._last_round_usage`` as anchor for next-round compaction estimate; total usage
        is still displayed once via ``render_usage`` from ``AgentDone.usage`` (external behavior unchanged).

        After the loop, prints a notice based on the stop reason: STREAM_ERROR red error line
        (same style as v0.3's "Error: ..."), MAX_ROUNDS yellow notice (suppressed when
        ``limit_notice=False``, see _chat_once's executor-absent decision), UNKNOWN_TOOL_LOOP
        yellow notice; finally render_usage (usage=None auto-suppresses output).
        """
        view = None
        final: AgentDone | None = None
        _current_round_index: int = 0
        async for ev in events:
            if isinstance(ev, RoundStart):
                _current_round_index = ev.index
                # v0.12 · C99 (task T124) — ROUND_START seam.
                self._fire_hook(
                    HookEvent.ROUND_START,
                    {"round_index": ev.index, "session_id": self._session.id},
                )
                view = self._renderer.new_stream_view()
                view.start()
            elif isinstance(ev, (ThinkingDelta, TextDelta)):
                view.feed(ev)
            elif isinstance(ev, StreamEnd):
                view.finish(interrupted=ev.interrupted)
                view = None
            elif isinstance(ev, ToolCallStarted):
                self._renderer.render_tool_call(ev.call)
            elif isinstance(ev, ToolResultReady):
                self._renderer.render_tool_result(ev.outcome)
                # v0.12 · C99 (task T124) — POST_TOOL_USE seam.
                outcome = ev.outcome
                self._fire_hook(
                    HookEvent.POST_TOOL_USE,
                    {
                        "event": "PostToolUse",
                        "cwd": str(Path.cwd()),
                        "tool_name": getattr(outcome, "name", ""),
                        "tool_call_id": getattr(outcome, "tool_call_id", ""),
                        # v0.12 · C99 — tool result text, for PostToolUse condition/action consumption.
                        "result": str(getattr(outcome, "content", "")),
                        "is_error": bool(getattr(outcome, "is_error", False)),
                        "round_index": _current_round_index,
                        "session_id": self._session.id,
                    },
                )
            elif isinstance(ev, RoundEnd):
                # v0.12 · C99 (task T124) — ROUND_END seam.
                self._fire_hook(
                    HookEvent.ROUND_END,
                    {
                        "round_index": ev.index,
                        "tool_results": ev.tool_results,
                        "session_id": self._session.id,
                    },
                )
                if ev.tool_results:
                    # Per-round persist: side effects have truly occurred, must not lose on crash.
                    # v0.9 changed to incremental append (F64: incremental append, crash loses only last line).
                    self._persist_pending()
            elif isinstance(ev, UsageUpdate):
                # v0.8 · C52 · F61/F62 (task T96) — store per-round usage as anchor for next-round
                # compaction estimate; total display still goes through AgentDone.usage (this event was
                # intentionally ignored before; now only one extra field is stored, external behavior unchanged).
                self._last_round_usage = ev.round_usage
            elif isinstance(ev, AgentDone):
                # v0.12 · C99 (task T124) — STOP seam.
                self._fire_hook(
                    HookEvent.STOP,
                    {
                        "stop_reason": ev.stop_reason.value,
                        "rounds": ev.rounds,
                        "session_id": self._session.id,
                    },
                )
                final = ev

        if view is not None:
            # STREAM_ERROR round has no StreamEnd: just stop spinner/Live, do not print final text
            # (consistent with _stop_displays usage in render_stream's error path).
            view._stop_displays()

        if final is None:
            return None
        if final.stop_reason is StopReason.STREAM_ERROR:
            self._console.print(f"[red]Error: {final.error}[/red]")
        elif final.stop_reason is StopReason.MAX_ROUNDS and limit_notice:
            self._console.print(
                f"[yellow dim]Reached the tool loop limit for this turn ({self._max_rounds} rounds); "
                "remaining tool requests were not executed[/yellow dim]"
            )
        elif final.stop_reason is StopReason.UNKNOWN_TOOL_LOOP:
            self._console.print(
                "[yellow dim]Model repeatedly called unknown tools; stopped this round's loop[/yellow dim]"
            )
        self._renderer.render_usage(final.usage, final.rounds)
        return final

    # ------------------------------------------------------------------
    # Slash command dispatch
    # ------------------------------------------------------------------

    def _dispatch_command(self, line: str) -> bool:
        """Parse and execute a slash command.

        Returns True if the REPL should exit, False otherwise.

        v0.10 · C91 · F73/F76/N35 (task T114) — when the ``commands`` registry is injected,
        follows the "parse → lookup → handler(self, args)" path: handler is called with this REPL
        (implementing the :class:`~wentian.commands.context.CommandContext` protocol) as ctx;
        a truthy return value exits. When ``commands`` is None, falls back to the v0.9 hard-coded
        dict dispatch (verbatim, regression-safe). Commands are locally trusted — dispatch does not
        go through AgentLoop / permission gate.
        """
        if self._commands is not None:
            from wentian.commands.parser import parse

            parsed = parse(line)
            if parsed is None:
                self._console.print(
                    "[yellow]Please enter a command name; type /help for help[/yellow]"
                )
                return False
            spec = self._commands.lookup(parsed.name)
            if spec is None:
                self._console.print(
                    f"[yellow]Unknown command: /{parsed.name}  Type /help for help[/yellow]"
                )
                return False
            result = spec.handler(self, parsed.args)
            return bool(result)

        # ----------------------------------------------------------------
        # commands=None → v0.9 hard-coded dict dispatch (verbatim, regression path).
        # ----------------------------------------------------------------
        parts = line.split(maxsplit=1)
        cmd = parts[0]
        args = parts[1] if len(parts) > 1 else ""

        handlers: dict[str, Callable[[str], bool | None]] = {
            "/help": self._cmd_help,
            "/new": self._cmd_new,
            "/sessions": self._cmd_sessions,
            "/resume": self._cmd_resume,
            "/provider": self._cmd_provider,
            "/plan": self._cmd_plan,
            "/do": self._cmd_do,
            "/compact": self._cmd_compact,
            "/exit": self._cmd_exit,
        }

        handler = handlers.get(cmd)
        if handler is None:
            self._console.print(
                f"[yellow]Unknown command: {cmd}  Type /help for help[/yellow]"
            )
            return False

        result = handler(args)
        return bool(result)

    # ------------------------------------------------------------------
    # Command handlers — all take args: str, return truthy to exit
    # ------------------------------------------------------------------

    def _cmd_help(self, args: str) -> None:
        self._console.print(_build_help())

    def _cmd_new(self, args: str) -> None:
        """Legacy thin shell: reuses :meth:`new_session` (registry path uses _h_session new)."""
        self.new_session()

    def _cmd_sessions(self, args: str) -> None:
        """Legacy thin shell: reuses :meth:`list_sessions` (``--all`` spans partitions)."""
        self.list_sessions(all_projects=args.strip() == "--all")

    def _cmd_resume(self, args: str) -> None:
        """Legacy thin shell: reuses :meth:`resume_session` (bare /resume shows usage hint)."""
        sid = args.strip()
        if not sid:
            self._console.print("[yellow]Usage: /resume <id>[/yellow]")
            return
        self.resume_session(sid)

    def _cmd_provider(self, args: str) -> None:
        """Legacy thin shell: reuses :meth:`switch_provider` (bare /provider shows usage hint)."""
        name = args.strip()
        if not name:
            self._console.print("[yellow]Usage: /provider <name>[/yellow]")
            return
        self.switch_provider(name)

    def _cmd_plan(self, args: str) -> None:
        """v0.4 · C19 · F33 (task T56) — enter plan mode (idempotent).

        Trailing text is sent immediately as the next user message (that turn already uses plan mode filtering).

        v0.6 · C38 · F47 (task T78) — plan is unified as one tier: entering plan sets self._mode
        to Mode.PLAN (Shift+Tab can also reach this tier); all F33 mechanisms are re-keyed on mode==PLAN.
        """
        self._mode = Mode.PLAN
        self._console.print("[green]Entered plan mode (read-only tools). Use /do to exit[/green]")
        text = args.strip()
        if text:
            self._chat_once(text)

    def _cmd_do(self, args: str) -> None:
        """v0.4 · C19 · F33 (task T56) — exit plan mode, restore all tools.

        Trailing text is sent immediately as the next user message (e.g. ``/do execute the plan``);
        bare /do just switches mode without sending a message.

        v0.6 · C38 · F47 (task T78) — /do always switches back to Mode.DEFAULT (does not restore
        the previous mode before entering plan).
        """
        self._mode = Mode.DEFAULT
        self._console.print("[green]Exited plan mode; all tools restored[/green]")
        text = args.strip()
        if text:
            self._chat_once(text)

    def _cmd_compact(self, args: str) -> None:
        """Legacy thin shell: reuses :meth:`compact_now` to get the report string, then prints it."""
        self._console.print(f"[dim]{self.compact_now()}[/dim]")

    def _cmd_exit(self, args: str) -> bool:
        return True

    # ------------------------------------------------------------------
    # v0.10 · C91 · F76 (task T114) — shared logic from consolidated old commands (ctx methods)
    #
    # Logic from existing _cmd_new/_cmd_sessions/_cmd_resume/_cmd_provider/_cmd_compact
    # is moved here; legacy thin shells and registry handlers share the same implementation,
    # zero duplication. Print responsibility: builtins _h_session new/resume and _h_provider
    # are pure routing with no confirmation printing → new_session/resume_session/switch_provider
    # print dynamic confirmations/errors here; _h_compact handles printing → compact_now
    # only returns the report string.
    # ------------------------------------------------------------------

    def new_session(self) -> None:
        """Create and switch to a new session, print the new id as confirmation (reuses old _cmd_new wording)."""
        self._session = self._store.create(provider=self._provider.name)
        self._update_compactor_session()
        self._reset_persist_cursor()
        # v0.11 · C104 · F73/F87 (task T134a) — clear Skill activation set on new session (duck-typed).
        if self._activator is not None:
            self._activator.clear()
        self._console.print(f"[green]New session created: {self._session.id}[/green]")

    def list_sessions(self, *, all_projects: bool) -> None:
        """List saved sessions (print logic from old _cmd_sessions; ``all_projects`` spans partitions)."""
        sessions = self._store.list(all_projects=all_projects)
        if not sessions:
            self._console.print("[dim]No saved sessions[/dim]")
            return
        for sid, updated_at, summary in sessions:
            preview = f"  {summary[:40]}" if summary else ""
            self._console.print(f"  {sid}  {updated_at}{preview}")

    def resume_session(self, sid: str) -> None:
        """Resume a historical session by id, print confirmation or not-found (reuses old _cmd_resume wording)."""
        try:
            self._session = self._store.load(sid)
            self._update_compactor_session()
            self._reset_persist_cursor()
            self._console.print(f"[green]Session resumed: {sid}[/green]")
        except FileNotFoundError:
            self._console.print(f"[red]Session not found: {sid}[/red]")

    def switch_provider(self, name: str) -> None:
        """Switch provider by name, print success or failure (reuses old _cmd_provider wording)."""
        try:
            new_provider = self._provider_factory(name)
            self._provider = new_provider
            self._session.provider = new_provider.name
            self._store.save(self._session)
            self._update_compactor_provider(new_provider)
            self._console.print(f"[green]Provider switched: {name}[/green]")
        except Exception as exc:  # noqa: BLE001 — provider_factory may raise anything.
            self._console.print(f"[red]Failed to switch provider: {exc}[/red]")

    def agents_manager(self) -> object | None:
        """v0.13 · C117 · F101 (task T145) — background task manager handle.

        Used by the /agents command to read background task lists and results (CommandContext protocol method).
        Returns ``None`` when agents.enabled=False or not assembled.
        """
        return self._agents_manager

    def compact_now(self) -> str:
        """v0.8 · C52 · F61/F62 (task T96) — manually trigger one heavyweight compaction; returns a human-readable report string.

        Calls the compactor with ``manual=True`` (ignores circuit breaker to force retry, narrows margin,
        more aggressive), then does an explicit :meth:`SessionStore.save` (compaction rewrote
        ``session.messages`` in place), and finally **returns** the human-readable report of
        :class:`CompactionResult` (printing is the caller's responsibility).
        When no compactor is injected, returns a friendly unavailable message (compactor=None ⇒ v0.7 behavior).
        """
        if self._compactor is None:
            return "/compact not available: context compaction not enabled"
        result = self._compactor.compact(
            self._session.messages, self._last_round_usage, manual=True
        )
        self._store.save(self._session)
        return _format_compaction_report(result)

    # ------------------------------------------------------------------
    # v0.8 · C52 · F61/F62 (task T96) — sync compactor on provider/session switch
    # ------------------------------------------------------------------

    def _update_compactor_provider(self, new_provider) -> None:
        """Sync the compactor's backend and window after switching provider (compactor=None ⇒ no-op).

        The new window is preferentially taken from the new provider's exposed ``context_window``
        (duck-typed); otherwise the compactor's current window is kept (REPL does not hold config
        and cannot recompute the default window; conservative preservation).
        """
        if self._compactor is None:
            return
        window = getattr(new_provider, "context_window", None)
        if not isinstance(window, int) or window <= 0:
            window = getattr(self._compactor, "context_window", 0) or 0
        self._compactor.set_provider(new_provider, window)

    def _update_compactor_session(self) -> None:
        """Point the compactor's artifact directory at the new session after switching (/new, /resume).

        Artifact directory = ``<sessions_dir>/<session_id>.artifacts/`` (consistent with build_app assembly).
        compactor=None ⇒ no-op.
        """
        if self._compactor is None:
            return
        sessions_dir = self._store._path(self._session.id).parent
        artifacts_dir = sessions_dir / f"{self._session.id}.artifacts"
        self._compactor.set_artifacts_dir(artifacts_dir)

    # ------------------------------------------------------------------
    # v0.9 · C54 · F64 (task T106) — incremental append persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _fingerprint(messages: list[Message]) -> list[int]:
        """Per-message content fingerprint of the already-persisted prefix.

        Cheap ``hash`` of each message's ``content`` (coerced to str so non-str
        tool payloads are covered). Used to detect an **in-place rewrite of the
        persisted prefix** — e.g. v0.8 offload shrinking a tool result's content
        below the cursor without changing ``len(messages)`` (the pure-append
        path can't see that; the fingerprint can).
        """
        return [hash(str(m.get("content", ""))) for m in messages]

    def _persist_pending(self) -> None:
        """Append messages beyond the persisted cursor to the session JSONL.

        Delivers F64 "append, crash loses only the last line": normal path uses ``store.append`` incremental write.
        Two "already-persisted prefix invalidated" cases fall back to one atomic full ``store.save`` and reset
        cursor + fingerprint (correctness first):

        - **Shortened**: compaction shortened ``session.messages`` (``n < cursor``);
        - **In-place rewrite** (v0.9 review fix · Major #1): offload replaced already-persisted
          message content below the cursor with a preview (list length unchanged, invisible to append
          path) — detected by comparing content fingerprints of the already-persisted prefix; if changed,
          do a full rewrite, otherwise the disk retains old text, the whole segment is re-injected on
          resume, and offload is ineffective.

        Normal case (pure append, prefix fingerprint unchanged) still uses ``store.append`` incremental write.
        """
        n = len(self._session.messages)
        prefix_len = min(n, self._persisted_count)
        prefix_fingerprint = self._fingerprint(self._session.messages[:prefix_len])
        prefix_rewritten = (
            prefix_fingerprint != self._persisted_fingerprint[:prefix_len]
        )

        if n < self._persisted_count or prefix_rewritten:
            # History was rewritten in place / shrank → full atomic rewrite.
            self._store.save(self._session)
            self._persisted_count = len(self._session.messages)
            self._persisted_fingerprint = self._fingerprint(self._session.messages)
            return
        new = self._session.messages[self._persisted_count :]
        if not new:
            return
        self._store.append(self._session, new)
        self._persisted_count = n
        self._persisted_fingerprint = self._fingerprint(self._session.messages)

    def _reset_persist_cursor(self) -> None:
        """Switch to a new/resumed session: cursor + fingerprint = on-disk state."""
        self._persisted_count = len(self._session.messages)
        self._persisted_fingerprint = self._fingerprint(
            self._session.messages[: self._persisted_count]
        )

    # ------------------------------------------------------------------
    # v0.2 · C2 · F16 (task T17) — bottom toolbar status line data source
    # ------------------------------------------------------------------

    def status_line(self) -> str:
        """v0.2 · C2 · F16 (task T17) — bottom toolbar status line data source.

        v0.6 · C38 · F47 (task T78) — **first segment changed from provider:model to current permission mode**:
        occupies the original provider name slot, **no longer shows provider name** (AC49). Reads live
        self._mode / self._session; Shift+Tab, /new, /resume are automatically reflected on the next
        toolbar recompute, no extra notification needed.

        v0.10 · C91 · F74/AC88 (task T114) — mode label changed to bracket form ``[{mode.name}]``
        (``[DEFAULT]`` / ``[ACCEPT_EDITS]`` / ``[PLAN]`` / ``[BYPASS]``); other segments
        (session id, message count, `` │ plan mode`` suffix) remain unchanged.

        v0.4 · C19 · F33 (task T56) — append `` │ plan mode`` when in plan mode: plan tier is already
        shown in the first segment as ``plan``; this suffix is kept as a redundant English hint
        (F33 existing status_line behavior unchanged; plan-mode regression assertion still matches "plan mode").
        """
        n = len(self._session.messages)
        line = f"[{self._mode.name}] │ session {self._session.id} │ {n} messages"
        if self._plan_mode:
            line += " │ plan mode"
        return line
