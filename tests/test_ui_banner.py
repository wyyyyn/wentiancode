"""Tests for wentian.ui.banner.build_banner (T16 / C1 / F13).

TDD: these tests are written first against a not-yet-existing module.
Expected initial state: ImportError / AttributeError → all tests FAIL (red).
"""

from __future__ import annotations

import pytest
from rich.console import Console


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _render(panel) -> str:
    """Render a Rich renderable to a string using a fixed-width recording console."""
    console = Console(record=True, width=80)
    console.print(panel)
    return console.export_text()


# ---------------------------------------------------------------------------
# 1. New session banner contains expected fields
# ---------------------------------------------------------------------------

def test_banner_new_session_contains_expected_fields():
    """build_banner(resumed=False) output must contain version, provider, model,
    session id, '新会话', and the rounded-box corner char '╭'."""
    from wentian.ui.banner import build_banner  # noqa: PLC0415

    panel = build_banner(
        version="0.2.0",
        provider_name="sf",
        model="Qwen3-235B-A22B",
        session_id="abc-123",
        resumed=False,
    )
    text = _render(panel)

    assert "0.2.0" in text, "version must appear in banner"
    assert "sf" in text, "provider_name must appear in banner"
    assert "Qwen3-235B-A22B" in text, "model must appear in banner"
    assert "abc-123" in text, "session_id must appear in banner"
    assert "新会话" in text, "新会话 label must appear for new sessions"
    assert "╭" in text, "rounded box corner ╭ must appear (box.ROUNDED)"


# ---------------------------------------------------------------------------
# 2. Resumed session banner contains '已恢复'
# ---------------------------------------------------------------------------

def test_banner_resumed_session_contains_resumed_label():
    """build_banner(resumed=True) output must contain '已恢复', not '新会话'."""
    from wentian.ui.banner import build_banner  # noqa: PLC0415

    panel = build_banner(
        version="0.2.0",
        provider_name="sf",
        model="Qwen3-235B-A22B",
        session_id="xyz-456",
        resumed=True,
    )
    text = _render(panel)

    assert "已恢复" in text, "已恢复 label must appear for resumed sessions"
    assert "xyz-456" in text, "session_id must appear in resumed banner"
    assert "新会话" not in text, "新会话 must NOT appear when resumed=True"


# ---------------------------------------------------------------------------
# 3. Empty model → no trailing colon after provider name
# ---------------------------------------------------------------------------

def test_banner_empty_model_no_trailing_colon():
    """When model='', the output must not contain 'sf:' (provider name + colon)."""
    from wentian.ui.banner import build_banner  # noqa: PLC0415

    panel = build_banner(
        version="0.2.0",
        provider_name="sf",
        model="",
        session_id="no-model-456",
        resumed=False,
    )
    text = _render(panel)

    assert "sf" in text, "provider_name must still appear when model is empty"
    assert "sf:" not in text, "must not have trailing colon when model is empty"
