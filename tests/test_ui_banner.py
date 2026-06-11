"""Tests for wentian.ui.banner.build_banner (T16→T27 / C1 / F13).

v0.2 · C1 · F13（任务 T27 改版）：横幅改为 Claude Code 式布局——
左侧像素小人 icon（半块字符像素画），右侧信息行，无边框面板。
"""

from __future__ import annotations

from rich.console import Console


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _render(renderable) -> str:
    """Render a Rich renderable to a string using a fixed-width recording console."""
    console = Console(record=True, width=80)
    console.print(renderable)
    return console.export_text()


# ---------------------------------------------------------------------------
# 1. New session banner contains expected fields, pixel icon, and NO panel box
# ---------------------------------------------------------------------------

def test_banner_new_session_contains_expected_fields():
    from wentian.ui.banner import build_banner  # noqa: PLC0415

    banner = build_banner(
        version="0.2.0",
        provider_name="sf",
        model="Qwen3-235B-A22B",
        session_id="abc-123",
        resumed=False,
    )
    text = _render(banner)

    assert "WentianCode" in text, "program name must appear"
    assert "文天" in text, "Chinese name must appear"
    assert "0.2.0" in text, "version must appear in banner"
    assert "sf" in text, "provider_name must appear in banner"
    assert "Qwen3-235B-A22B" in text, "model must appear in banner"
    assert "abc-123" in text, "session_id must appear in banner"
    assert "新会话" in text, "新会话 label must appear for new sessions"


def test_banner_has_text_face_and_no_panel_box():
    """F13 T29 改版：icon 即 =^_^= 文本本体，无任何边框/像素字符。"""
    from wentian.ui.banner import build_banner  # noqa: PLC0415

    banner = build_banner(
        version="0.2.0",
        provider_name="sf",
        model="m",
        session_id="abc-123",
        resumed=False,
    )
    text = _render(banner)

    assert "=^_^=" in text, "text cat face must appear as the icon"
    assert "╭" not in text, "no rounded panel border (Claude Code layout)"
    assert "▀" not in text and "▄" not in text, "no pixel-art chars (T29: text face only)"


# ---------------------------------------------------------------------------
# 2. Resumed session banner contains '已恢复'
# ---------------------------------------------------------------------------

def test_banner_resumed_session_contains_resumed_label():
    from wentian.ui.banner import build_banner  # noqa: PLC0415

    banner = build_banner(
        version="0.2.0",
        provider_name="sf",
        model="Qwen3-235B-A22B",
        session_id="xyz-456",
        resumed=True,
    )
    text = _render(banner)

    assert "已恢复" in text, "已恢复 label must appear for resumed sessions"
    assert "xyz-456" in text, "session_id must appear in resumed banner"
    assert "新会话" not in text, "新会话 must NOT appear when resumed=True"


# ---------------------------------------------------------------------------
# 3. Empty model → provider shown alone, no dangling separator
# ---------------------------------------------------------------------------

def test_banner_empty_model_no_dangling_separator():
    from wentian.ui.banner import build_banner  # noqa: PLC0415

    banner = build_banner(
        version="0.2.0",
        provider_name="sf",
        model="",
        session_id="no-model-456",
        resumed=False,
    )
    text = _render(banner)

    assert "sf" in text, "provider_name must still appear when model is empty"
    assert "sf ·" not in text, "no dangling separator when model is empty"
    assert "sf:" not in text, "no trailing colon when model is empty"
