"""v0.2 · C3 · F15/F16（任务 T18）

PromptInput — multiline input widget with history and bottom toolbar.

Design decisions:
- multiline=True; Enter submits; Ctrl+J / Alt+Enter insert a newline.
- History is persisted via prompt_toolkit's FileHistory when history_path
  is provided; otherwise InMemoryHistory is used.
- status_provider is settable post-construction (avoids REPL↔PromptInput
  circular dependency at __init__ time).
- Frame art (╭─…╰─) is printed with plain print() guarded by
  sys.stdout.isatty(), so pipe-input tests remain deterministic and only
  see the return value (not control characters).
- input/output kwargs are forwarded to PromptSession ONLY when not None,
  so the real terminal auto-detects by default.
- EOF (Ctrl+D) and KeyboardInterrupt propagate to the caller unchanged.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from prompt_toolkit.filters import Condition
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.shortcuts import PromptSession

__all__ = ["PromptInput", "default_history_path"]


def default_history_path() -> Path:
    """Return the default history file path.

    Tries ``$XDG_STATE_HOME/wentian/history`` first; falls back to
    ``~/.local/state/wentian/history``.  Parent directories are created
    automatically.
    """
    xdg = os.environ.get("XDG_STATE_HOME", "")
    if xdg:
        base = Path(xdg)
    else:
        base = Path.home() / ".local" / "state"
    path = base / "wentian" / "history"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _build_key_bindings() -> KeyBindings:
    """Return key bindings for the multiline prompt.

    - Enter            → submit (validate_and_handle)
    - Ctrl+J           → insert newline (primary newline key)
    - Alt+Enter        → insert newline (compatibility alias)
    - Up when on first line and no completion open → history_backward
    - Down when on last line and no completion open → history_forward
    """
    kb = KeyBindings()

    @kb.add("enter")
    def _submit(event) -> None:
        event.current_buffer.validate_and_handle()

    @kb.add("c-j")
    def _newline_ctrl_j(event) -> None:
        event.current_buffer.insert_text("\n")

    @kb.add("escape", "enter")
    def _newline_alt_enter(event) -> None:
        event.current_buffer.insert_text("\n")

    # Up: history navigation when cursor is on the first line.
    @kb.add(
        "up",
        filter=Condition(
            lambda: True  # evaluated at binding time; fine-grained check below
        ),
    )
    def _up(event) -> None:
        buf = event.current_buffer
        if buf.document.cursor_position_row == 0:
            buf.history_backward()
        else:
            buf.cursor_up()

    # Down: history navigation when cursor is on the last line.
    @kb.add(
        "down",
        filter=Condition(lambda: True),
    )
    def _down(event) -> None:
        buf = event.current_buffer
        doc = buf.document
        last_row = doc.text.count("\n")
        if doc.cursor_position_row == last_row:
            buf.history_forward()
        else:
            buf.cursor_down()

    return kb


class PromptInput:
    """Callable matching REPL input_fn signature: (prompt: str) -> str.

    Parameters
    ----------
    history_path:
        Path to the history file.  None → use InMemoryHistory.
    status_provider:
        Zero-argument callable returning the toolbar string.  Can be set
        after construction to break a circular dependency with REPL.
    input:
        prompt_toolkit Input object (inject for tests; None → real terminal).
    output:
        prompt_toolkit Output object (inject for tests; None → real terminal).
    """

    def __init__(
        self,
        *,
        history_path: Path | None = None,
        status_provider: Callable[[], str] | None = None,
        input: Any = None,
        output: Any = None,
    ) -> None:
        self.status_provider = status_provider

        history = (
            FileHistory(str(history_path)) if history_path is not None
            else InMemoryHistory()
        )
        kb = _build_key_bindings()

        # Build PromptSession kwargs; omit input/output when None so the
        # real terminal auto-detects.
        session_kwargs: dict[str, Any] = dict(
            multiline=True,
            history=history,
            key_bindings=kb,
            bottom_toolbar=self._toolbar,
        )
        if input is not None:
            session_kwargs["input"] = input
        if output is not None:
            session_kwargs["output"] = output

        self._session: PromptSession = PromptSession(**session_kwargs)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _toolbar(self) -> str:
        """Return the bottom toolbar text (live-reads status_provider)."""
        if self.status_provider is not None:
            return self.status_provider()
        return ""

    # ------------------------------------------------------------------
    # Public callable
    # ------------------------------------------------------------------

    def __call__(self, prompt: str = "") -> str:
        """Read a line (possibly multiline) from the user and return it.

        Prints a visual open-box frame around the input area when stdout
        is a real terminal (guarded by sys.stdout.isatty()), so that
        pipe-based tests receive only the return value.

        Propagates EOFError (Ctrl+D) and KeyboardInterrupt unchanged.
        """
        is_tty = sys.stdout.isatty()
        width = min(os.get_terminal_size().columns, 80) if is_tty else 80

        if is_tty:
            bar = "─" * (width - 2)
            print(f"╭{bar}╮")

        result = self._session.prompt(
            message="│ ❯ " if not prompt else f"│ ❯ {prompt}",
            prompt_continuation="│   ",
        )

        if is_tty:
            bar = "─" * (width - 2)
            print(f"╰{bar}╯")

        return result
