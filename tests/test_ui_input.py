"""Tests for PromptInput (v0.2 · C3 · F15/F16 — Task T18).

Testing patterns use prompt_toolkit's create_pipe_input + DummyOutput for
deterministic offline testing without a real terminal.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput


# ===========================================================================
# 1. Basic submit: "hello\r" → "hello"
# ===========================================================================

class TestBasicSubmit:
    def test_hello_returns_hello(self, tmp_path):
        """'hello\\r' (Enter) submits and returns 'hello'."""
        from wentian.ui.input import PromptInput
        with create_pipe_input() as pipe:
            pi = PromptInput(
                history_path=tmp_path / "h",
                input=pipe,
                output=DummyOutput(),
            )
            pipe.send_text("hello\r")
            assert pi() == "hello"


# ===========================================================================
# 2. Ctrl+J inserts newline; Enter submits: "a\x0ab\r" → "a\nb"
# ===========================================================================

class TestCtrlJNewline:
    def test_ctrl_j_inserts_newline(self, tmp_path):
        """Ctrl+J (\\x0a) inserts a newline; Enter submits the multiline text."""
        from wentian.ui.input import PromptInput
        with create_pipe_input() as pipe:
            pi = PromptInput(
                history_path=tmp_path / "h",
                input=pipe,
                output=DummyOutput(),
            )
            pipe.send_text("a\x0ab\r")
            assert pi() == "a\nb"


# ===========================================================================
# 3. Alt+Enter inserts newline: "a\x1b\rb\r" → "a\nb"
# ===========================================================================

class TestAltEnterNewline:
    def test_alt_enter_inserts_newline(self, tmp_path):
        """Alt+Enter (ESC then CR) inserts a newline; final Enter submits."""
        from wentian.ui.input import PromptInput
        with create_pipe_input() as pipe:
            pi = PromptInput(
                history_path=tmp_path / "h",
                input=pipe,
                output=DummyOutput(),
            )
            pipe.send_text("a\x1b\rb\r")
            assert pi() == "a\nb"


# ===========================================================================
# 4. History persists across program restarts (separate PromptInput instances)
# ===========================================================================

class TestHistoryPersistence:
    def test_history_recalled_by_new_instance(self, tmp_path):
        """Instance A submits 'one'; Instance B (same file) up-arrow recalls it."""
        from wentian.ui.input import PromptInput
        hist = tmp_path / "history"

        # Instance A: submit "one"
        with create_pipe_input() as pipe:
            pi_a = PromptInput(history_path=hist, input=pipe, output=DummyOutput())
            pipe.send_text("one\r")
            result_a = pi_a()
        assert result_a == "one"

        # Instance B: press up arrow then Enter to submit recalled entry
        with create_pipe_input() as pipe:
            pi_b = PromptInput(history_path=hist, input=pipe, output=DummyOutput())
            pipe.send_text("\x1b[A\r")
            result_b = pi_b()
        assert result_b == "one"

        # History file must exist and be non-empty
        assert hist.exists()
        assert hist.stat().st_size > 0

    def test_history_forward_after_backward(self, tmp_path):
        """↑↑ then ↓ then Enter: with history ['one','two'] returns 'two'."""
        from wentian.ui.input import PromptInput
        hist = tmp_path / "history"

        # Pre-populate history: submit "one" then "two"
        with create_pipe_input() as pipe:
            pi = PromptInput(history_path=hist, input=pipe, output=DummyOutput())
            pipe.send_text("one\r")
            pi()
        with create_pipe_input() as pipe:
            pi = PromptInput(history_path=hist, input=pipe, output=DummyOutput())
            pipe.send_text("two\r")
            pi()

        # ↑↑ goes back to "one"; ↓ goes forward to "two"; Enter submits
        with create_pipe_input() as pipe:
            pi = PromptInput(history_path=hist, input=pipe, output=DummyOutput())
            pipe.send_text("\x1b[A\x1b[A\x1b[B\r")
            result = pi()
        assert result == "two"


# ===========================================================================
# 5. status_provider settable post-construction
# ===========================================================================

class TestStatusProvider:
    def test_status_provider_post_assignment(self, tmp_path):
        """status_provider can be set after construction; toolbar returns its value."""
        from wentian.ui.input import PromptInput
        with create_pipe_input() as pipe:
            pi = PromptInput(
                history_path=tmp_path / "h",
                input=pipe,
                output=DummyOutput(),
            )
            pi.status_provider = lambda: "model:gpt │ 会话 abc │ 3 条消息"
            # Access the toolbar callable from the PromptSession
            toolbar = pi._session.bottom_toolbar
            result = toolbar() if callable(toolbar) else toolbar
            assert "model:gpt" in result


# ===========================================================================
# 6. default_history_path() respects XDG_STATE_HOME
# ===========================================================================

class TestDefaultHistoryPath:
    def test_xdg_state_home_used(self, tmp_path, monkeypatch):
        """default_history_path() uses $XDG_STATE_HOME/wentian/history.

        The env var is read at call time, so no module reload is needed.
        """
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        from wentian.ui.input import default_history_path
        p = default_history_path()
        assert p == tmp_path / "wentian" / "history"
        # Parents are created
        assert p.parent.exists()

    def test_fallback_when_xdg_not_set(self, tmp_path, monkeypatch):
        """When XDG_STATE_HOME is unset, falls back to HOME/.local/state/wentian/history.

        HOME is monkeypatched to tmp_path to avoid creating real ~/.local/state/wentian/.
        The env var is read at call time, so no module reload is needed.
        """
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        from wentian.ui.input import default_history_path
        p = default_history_path()
        assert p == tmp_path / ".local" / "state" / "wentian" / "history"


# ===========================================================================
# 7. EOF: empty pipe close → EOFError
# ===========================================================================

class TestEOF:
    def test_eof_raises_eoferror(self, tmp_path):
        """Closing the pipe without input causes pi() to raise EOFError."""
        from wentian.ui.input import PromptInput
        with create_pipe_input() as pipe:
            pi = PromptInput(
                history_path=tmp_path / "h",
                input=pipe,
                output=DummyOutput(),
            )
            pipe.close()
            with pytest.raises(EOFError):
                pi()
