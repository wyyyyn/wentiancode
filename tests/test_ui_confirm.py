"""Tests for confirm_action (v0.6 · C37 · F48 — Task T77).

The human-in-the-loop three-way approval menu. Testing patterns follow the
established create_pipe_input/DummyOutput pattern from tests/test_ui_select.py.
"""
from __future__ import annotations

import asyncio

import pytest
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput


def _confirm(keys, *, tool_name="run_command", preview="rm -rf build", reason="需要确认"):
    """Run confirm_action with the given key sequence; return the Choice."""
    from wentian.ui.confirm import confirm_action

    with create_pipe_input() as pipe:
        pipe.send_text(keys)

        async def _go():
            return await confirm_action(
                tool_name=tool_name,
                preview=preview,
                reason=reason,
                input=pipe,
                output=DummyOutput(),
            )

        return asyncio.run(_go())


# ===========================================================================
# 1. Choice enum exists with three members
# ===========================================================================

class TestChoiceEnum:
    def test_three_members(self):
        from wentian.ui.confirm import Choice

        assert {c.name for c in Choice} == {"ALLOW_ONCE", "ALLOW_ALWAYS", "DENY"}


# ===========================================================================
# 2. Default highlight = ALLOW_ONCE → Enter returns ALLOW_ONCE
# ===========================================================================

class TestDefaultEnter:
    def test_enter_returns_allow_once(self):
        from wentian.ui.confirm import Choice

        assert _confirm("\r") == Choice.ALLOW_ONCE


# ===========================================================================
# 3. Arrow navigation: down moves to ALLOW_ALWAYS, down again to DENY
# ===========================================================================

class TestArrowNavigation:
    def test_down_then_enter(self):
        from wentian.ui.confirm import Choice

        assert _confirm("\x1b[B\r") == Choice.ALLOW_ALWAYS

    def test_down_down_then_enter(self):
        from wentian.ui.confirm import Choice

        assert _confirm("\x1b[B\x1b[B\r") == Choice.DENY

    def test_down_down_up_then_enter(self):
        from wentian.ui.confirm import Choice

        assert _confirm("\x1b[B\x1b[B\x1b[A\r") == Choice.ALLOW_ALWAYS

    def test_up_at_top_clamps(self):
        from wentian.ui.confirm import Choice

        assert _confirm("\x1b[A\r") == Choice.ALLOW_ONCE

    def test_down_at_bottom_clamps(self):
        from wentian.ui.confirm import Choice

        assert _confirm("\x1b[B\x1b[B\x1b[B\x1b[B\r") == Choice.DENY


# ===========================================================================
# 4. Number keys 1/2/3 select directly
# ===========================================================================

class TestNumberKeys:
    def test_key_1_allow_once(self):
        from wentian.ui.confirm import Choice

        assert _confirm("1") == Choice.ALLOW_ONCE

    def test_key_2_allow_always(self):
        from wentian.ui.confirm import Choice

        assert _confirm("2") == Choice.ALLOW_ALWAYS

    def test_key_3_deny(self):
        from wentian.ui.confirm import Choice

        assert _confirm("3") == Choice.DENY


# ===========================================================================
# 5. Esc / Ctrl+C raise Cancelled
# ===========================================================================

class TestCancel:
    def test_ctrl_c_raises_cancelled(self):
        from wentian.ui.confirm import Cancelled

        with pytest.raises(Cancelled):
            _confirm("\x03")

    def test_escape_raises_cancelled(self):
        from wentian.ui.confirm import Cancelled

        # Bare escape (no following bytes). pipe input flushes the lone \x1b.
        with pytest.raises(Cancelled):
            _confirm("\x1b")
