"""v0.10 · C87 · F73/N34 (task T111) — command context protocol (CommandContext).

Once a REPL implements this protocol, each command handler in the commands/ package
can interact with the UI/session layer through the protocol interface,
without directly depending on the concrete REPL class.

Layering rule: pure leaf module; Mode / CommandSpec both use TYPE_CHECKING forward references,
zero runtime dependencies (stdlib only).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from wentian.commands.spec import CommandSpec
    from wentian.permissions.decision import Mode

__all__ = ["CommandContext"]


@runtime_checkable
class CommandContext(Protocol):
    """UI control protocol between command handlers and REPL.

    Objects implementing this protocol are injected by the REPL when invoking commands;
    all handlers in the commands/ package depend only on this protocol,
    and do not directly import REPL / agent / provider.

    ``@runtime_checkable`` allows ``isinstance(obj, CommandContext)``
    structural checks in tests (duck-typing style).
    """

    def print(self, renderable: object) -> None:
        """Print *renderable* to the terminal (a string or Rich Renderable)."""
        ...

    def send_user_message(self, text: str) -> None:
        """Send *text* as a user message into the conversation, triggering one AI response turn.

        Primarily used by PROMPT-type commands.
        """
        ...

    def get_mode(self) -> Mode:
        """Return the current permission mode."""
        ...

    def set_mode(self, mode: Mode) -> None:
        """Switch the permission mode."""
        ...

    def token_usage(self) -> object | None:
        """Return the token usage snapshot from the last turn; returns ``None`` if no history."""
        ...

    def status_line(self) -> str:
        """Return the current status bar text (for display by /status-type commands)."""
        ...

    def memory_summary(self) -> str:
        """Return the long-term memory directory and per-domain INDEX summary (read-only string)."""
        ...

    def visible_commands(self) -> list[CommandSpec]:
        """Return the list of currently visible commands (for /help rendering)."""
        ...

    def clear_context(self) -> None:
        """Clear the messages of the current session, keeping the same session id."""
        ...

    def new_session(self) -> None:
        """Create and switch to a new session."""
        ...

    def list_sessions(self, *, all_projects: bool) -> None:
        """List session history.

        Parameters
        ----------
        all_projects:
            ``True`` to list all sessions across projects; ``False`` for current project only.
        """
        ...

    def resume_session(self, sid: str) -> None:
        """Resume a historical session by session id."""
        ...

    def switch_provider(self, name: str) -> None:
        """Switch AI provider by name."""
        ...

    def compact_now(self) -> str:
        """Trigger heavyweight context compaction, returning a human-readable compaction report string."""
        ...

    def agents_manager(self) -> object | None:
        """Return the background task manager handle (BackgroundTaskManager), or ``None`` if not enabled.

        v0.13 · C116 · F101: used by the /agents command to read background task lists and results;
        REPL-side wiring is implemented by T145.
        """
        ...
