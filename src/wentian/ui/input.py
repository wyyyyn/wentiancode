"""v0.2 · C3 · F15/F16（任务 T18，T29 改版：去手绘边框）

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


def _build_key_bindings(owner: PromptInput) -> KeyBindings:
    """Return key bindings for the multiline prompt.

    - Enter            → submit (validate_and_handle)
    - Ctrl+J           → insert newline (primary newline key)
    - Alt+Enter        → insert newline (compatibility alias)
    - Up when on first line → history_backward (fine-grained check inside handler)
    - Down when on last line → history_forward (fine-grained check inside handler)
    - Shift+Tab        → v0.6 · C38 · F47（任务 T78）— 调注入的 on_mode_cycle
                         回调切换权限模式（owner.on_mode_cycle 为 None 时无操作，
                         仿 status_provider 的后置注入惯例）

    *owner* 是持有 ``on_mode_cycle`` 的 PromptInput 实例：绑定在闭包里读 live
    属性，因此回调可在构造后再注入（不引入跨层 import，回调由 REPL 提供）。
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
        v0.6 · C38 · F47（任务 T78）— zero-argument callback fired on
        Shift+Tab to cycle the permission mode.  Settable post-construction
        (same pattern as ``status_provider``); ``None`` → Shift+Tab is a no-op.
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
        input: Any = None,
        output: Any = None,
    ) -> None:
        self.status_provider = status_provider
        # v0.6 · C38 · F47（任务 T78）— Shift+Tab 回调；可后置注入。
        self.on_mode_cycle = on_mode_cycle

        history = (
            FileHistory(str(history_path)) if history_path is not None
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

        v0.2 · C3 · F15（任务 T29 改版）：提示符固定为 ``❯ ``，不再绘制
        手绘边框，也不复读 REPL 传入的 fallback 提示文本。理由：任何在
        prompt_toolkit 渲染器之外的终端写入（此前的盒线字符 print）都会
        与其重绘机制冲突，真实 Terminal.app 上曾导致 prompt 重复堆叠满屏。
        传入的 *prompt* 参数仅供 builtins.input fallback 使用，这里忽略。

        Propagates EOFError (Ctrl+D) and KeyboardInterrupt unchanged.
        """
        return self._session.prompt(
            message=[("bold #C84B31", "❯ ")],
            prompt_continuation="  ",
        )
