"""Tests for select_provider (v0.2 · C4 · F14 — Task T19).

Testing patterns follow the established create_pipe_input/DummyOutput pattern
from tests/test_ui_input.py.
"""

from __future__ import annotations

from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _select(names, default, keys):
    """Run select_provider with the given key sequence and return the result."""
    from wentian.ui.select import select_provider

    with create_pipe_input() as pipe:
        pipe.send_text(keys)
        return select_provider(names, default, input=pipe, output=DummyOutput())


# ===========================================================================
# 1. Default highlighted: Enter on first item → first item
# ===========================================================================


class TestDefaultEnter:
    def test_enter_returns_default(self):
        """names=['a','b','c'], default='a', send '\\r' → 'a'."""
        result = _select(["a", "b", "c"], default="a", keys="\r")
        assert result == "a"


# ===========================================================================
# 2. Arrow navigation
# ===========================================================================


class TestArrowNavigation:
    def test_down_then_enter_returns_b(self):
        """Down arrow then Enter selects second item."""
        result = _select(["a", "b", "c"], default="a", keys="\x1b[B\r")
        assert result == "b"

    def test_down_down_up_then_enter_returns_b(self):
        """Down twice, Up once → second item selected."""
        result = _select(["a", "b", "c"], default="a", keys="\x1b[B\x1b[B\x1b[A\r")
        assert result == "b"


# ===========================================================================
# 3. Ctrl+C cancel → returns default
# ===========================================================================


class TestCtrlCCancel:
    def test_ctrl_c_returns_default(self):
        """Ctrl+C (\\x03) cancels and returns default."""
        result = _select(["a", "b", "c"], default="a", keys="\x03")
        assert result == "a"


# ===========================================================================
# 4. Non-first default: initial highlight on default
# ===========================================================================


class TestDefaultHighlighted:
    def test_default_b_enter_returns_b(self):
        """default='b' → initial highlight on index 1; Enter returns 'b'."""
        result = _select(["a", "b", "c"], default="b", keys="\r")
        assert result == "b"


# ===========================================================================
# 5. Clamping: up at top does not wrap or crash
# ===========================================================================


class TestClampAtTop:
    def test_up_at_top_clamps(self):
        """Up arrow at top (index 0) clamps; Enter still returns first item."""
        result = _select(["a", "b", "c"], default="a", keys="\x1b[A\r")
        assert result == "a"


# ===========================================================================
# 6. Clamping: down at bottom does not wrap or crash
# ===========================================================================


class TestClampAtBottom:
    def test_down_at_bottom_clamps(self):
        """Down arrow at bottom clamps; Enter returns last item."""
        result = _select(["a", "b", "c"], default="c", keys="\x1b[B\r")
        assert result == "c"


# ===========================================================================
# 7. Default not in names → index-0 fallback
# ===========================================================================


class TestDefaultNotInNames:
    def test_enter_returns_names_0_when_default_missing(self):
        """default not in names → initial highlight on index 0; Enter returns names[0]."""
        result = _select(["x", "y", "z"], default="missing", keys="\r")
        assert result == "x"

    def test_ctrl_c_returns_default_string_when_default_missing(self):
        """Ctrl+C with default not in names returns the (missing) default string."""
        result = _select(["x", "y", "z"], default="missing", keys="\x03")
        assert result == "missing"
