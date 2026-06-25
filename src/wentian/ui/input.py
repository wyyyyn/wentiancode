"""v0.2 · C3 · F15/F16 (task T18, T29 revision: remove hand-drawn border)

PromptInput — multiline input widget with history and bottom toolbar.

Design decisions:
- multiline=True; Enter submits; Ctrl+J / Alt+Enter insert a newline.
- History is persisted via prompt_toolkit's FileHistory when history_path
  is provided; otherwise InMemoryHistory is used.
- status_provider is settable post-construction (avoids REPL↔PromptInput
  circular dependency at __init__ time).
- T29: NO out-of-band terminal writes here. The earlier hand-drawn frame
  (print of box-drawing lines around the prompt) conflicted with
  prompt_toolkit's repaint accounting on real terminals and stacked
  duplicate prompts; everything visual is now owned by the PromptSession.
- input/output kwargs are forwarded to PromptSession ONLY when not None,
  so the real terminal auto-detects by default.
- EOF (Ctrl+D) and KeyboardInterrupt propagate to the caller unchanged.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.shortcuts import CompleteStyle, PromptSession

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


def _build_key_bindings(owner: PromptInput) -> KeyBindings:
    """Return key bindings for the multiline prompt.

    - Enter            → submit (validate_and_handle)
    - Ctrl+J           → insert newline (primary newline key)
    - Alt+Enter        → insert newline (compatibility alias)
    - Up when on first line → history_backward (fine-grained check inside handler)
    - Down when on last line → history_forward (fine-grained check inside handler)
    - Shift+Tab        → v0.6 · C38 · F47 (task T78) — calls the injected on_mode_cycle
                         callback to cycle the permission mode (no-op when
                         owner.on_mode_cycle is None, following the post-construction
                         injection convention of status_provider)

    *owner* is the PromptInput instance holding ``on_mode_cycle``: the binding reads
    the live attribute in a closure, so the callback can be injected after construction
    (no cross-layer import needed; the callback is provided by REPL).
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
    @kb.add("up")
    def _up(event) -> None:
        buf = event.current_buffer
        if buf.document.cursor_position_row == 0:
            buf.history_backward()
        else:
            buf.cursor_up()

    # Down: history navigation when cursor is on the last line.
    @kb.add("down")
    def _down(event) -> None:
        buf = event.current_buffer
        doc = buf.document
        last_row = doc.text.count("\n")
        if doc.cursor_position_row == last_row:
            buf.history_forward()
        else:
            buf.cursor_down()

    # Shift+Tab (BackTab): cycle the permission mode via the injected callback.
    @kb.add("s-tab")
    def _mode_cycle(event) -> None:
        callback = owner.on_mode_cycle
        if callback is not None:
            callback()

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
    on_mode_cycle:
        v0.6 · C38 · F47 (task T78) — zero-argument callback fired on
        Shift+Tab to cycle the permission mode.  Settable post-construction
        (same pattern as ``status_provider``); ``None`` → Shift+Tab is a no-op.
    completer:
        v0.10 · C90 · F75 (task T113) — prompt_toolkit Completer instance (e.g.
        ``CommandCompleter``); injects into PromptSession with multi-column menu
        style when not None.  ``None`` → completion disabled (fully backward-compatible
        with prior behavior).
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
        on_mode_cycle: Callable[[], None] | None = None,
        completer: Any = None,
        input: Any = None,
        output: Any = None,
    ) -> None:
        self.status_provider = status_provider
        # v0.6 · C38 · F47 (task T78) — Shift+Tab callback; injectable post-construction.
        self.on_mode_cycle = on_mode_cycle

        history = (
            FileHistory(str(history_path))
            if history_path is not None
            else InMemoryHistory()
        )
        kb = _build_key_bindings(self)

        # Build PromptSession kwargs; omit input/output when None so the
        # real terminal auto-detects.
        session_kwargs: dict[str, Any] = dict(
            multiline=True,
            history=history,
            key_bindings=kb,
            bottom_toolbar=self._toolbar,
        )
        # v0.10 · C90 · F75 (task T113) — completer wiring; not injected when None (preserves prior behavior).
        if completer is not None:
            session_kwargs["completer"] = completer
            session_kwargs["complete_style"] = CompleteStyle.MULTI_COLUMN
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

        v0.2 · C3 · F15 (task T29 revision): the prompt is fixed as ``❯ ``; the
        hand-drawn border is no longer rendered, and the fallback prompt text passed
        in from REPL is no longer echoed.  Rationale: any terminal writes outside
        prompt_toolkit's renderer (the earlier box-drawing character prints) conflict
        with its repaint mechanism and caused prompts to stack repeatedly on real
        Terminal.app.  The *prompt* argument is only for builtins.input fallback and
        is ignored here.

        Propagates EOFError (Ctrl+D) and KeyboardInterrupt unchanged.
        """
        return self._session.prompt(
            message=[("bold #C84B31", "❯ ")],
            prompt_continuation="  ",
        )
