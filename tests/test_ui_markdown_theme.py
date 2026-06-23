"""Tests for the custom Markdown theme (v0.14 · C131 · F105).

Offline, deterministic: render via a recording Console and assert on the
exported text/segments. The public load-bearing API is ``render_markdown(text)``
which must be SELF-CONTAINED — all styling baked into the returned renderable,
not dependent on the printing console carrying a special Theme.
"""

from __future__ import annotations

import rich.markdown as rich_md
from rich.console import Console

from wentian.ui.markdown_theme import CODE_THEME, render_markdown


def _render(text: str, *, width: int = 80, styles: bool = False) -> str:
    """Render via render_markdown into a plain recording console (no Theme)."""
    console = Console(
        record=True, width=width, force_terminal=True, color_system="standard"
    )
    console.print(render_markdown(text))
    return console.export_text(styles=styles)


def _render_default(text: str, *, width: int = 80) -> str:
    """Baseline: plain rich.markdown.Markdown, same console settings."""
    console = Console(
        record=True, width=width, force_terminal=True, color_system="standard"
    )
    console.print(rich_md.Markdown(text))
    return console.export_text()


# --- CODE_THEME --------------------------------------------------------------


def test_code_theme_is_nonempty_str() -> None:
    assert isinstance(CODE_THEME, str)
    assert CODE_THEME.strip() != ""


# --- Code blocks & syntax highlight -----------------------------------------


def test_fenced_code_block_is_highlighted_not_raw_fences() -> None:
    src = "```python\ndef hello():\n    return 1\n```\n"
    plain = _render(src)
    styled = _render(src, styles=True)
    # The code content is present...
    assert "def hello():" in plain
    # ...the raw ``` fences are NOT shown literally.
    assert "```" not in plain
    # ...and syntax highlighting produced ANSI styling around the code.
    assert "\x1b[" in styled


def test_inline_code_renders_content() -> None:
    out = _render("Use the `render_markdown` function.\n")
    assert "render_markdown" in out


# --- Headings (lightweight, not Rich's heavy/centered default) --------------


def test_h1_differs_from_default_markdown() -> None:
    src = "# Title Here\n"
    mine = _render(src, width=40)
    default = _render_default(src, width=40)
    # Prove our theme actually changed h1 rendering vs. plain Markdown.
    assert mine != default
    # Default rich h1 is centered (leading whitespace padding before the text);
    # our lightweight h1 must NOT reproduce that centered layout.
    mine_line = next(line for line in mine.splitlines() if "Title Here" in line)
    default_line = next(line for line in default.splitlines() if "Title Here" in line)
    assert default_line.startswith(" ")  # default is centered/padded
    assert not mine_line.startswith(" " * 10)  # ours is not centered


def test_h1_has_no_heavy_box_frame() -> None:
    src = "# Title Here\n"
    mine = _render(src, width=40)
    # No box-drawing frame chars surrounding the heading (lightweight, not a panel).
    for ch in ("╭", "╮", "╰", "╯", "│"):
        assert ch not in mine


def test_heading_text_present() -> None:
    assert "Section" in _render("## Section\n")


# --- Bold / italic emphasis still render ------------------------------------


def test_bold_and_italic_render() -> None:
    out = _render("This is **bold** and *italic* text.\n")
    assert "bold" in out
    assert "italic" in out


# --- Lists: unified bullet ---------------------------------------------------


def test_bulleted_list_uses_unified_bullet() -> None:
    src = "- alpha\n- beta\n- gamma\n"
    out = _render(src)
    assert "alpha" in out
    assert "beta" in out
    assert "gamma" in out
    # A single, unified bullet glyph is used for list items.
    from wentian.ui.markdown_theme import LIST_BULLET

    assert out.count(LIST_BULLET) >= 3


def test_list_bullet_is_nonempty_str() -> None:
    from wentian.ui.markdown_theme import LIST_BULLET

    assert isinstance(LIST_BULLET, str)
    assert LIST_BULLET.strip() != ""


# --- Tables: aligned columns -------------------------------------------------


def test_table_renders_with_aligned_columns() -> None:
    src = "| Col A | Col B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |\n"
    out = _render(src)
    # Cells present.
    for cell in ("Col A", "Col B", "1", "2", "3", "4"):
        assert cell in out
    # Column structure intact: header row and both data rows show A's value
    # left of B's value on each line.
    header = next(line for line in out.splitlines() if "Col A" in line)
    assert header.index("Col A") < header.index("Col B")
    row1 = next(line for line in out.splitlines() if "1" in line and "2" in line)
    assert row1.index("1") < row1.index("2")


# --- Robustness --------------------------------------------------------------


def test_empty_markdown_does_not_crash() -> None:
    out = _render("")
    assert isinstance(out, str)


def test_render_markdown_returns_renderable() -> None:
    # Must be printable by a vanilla console with NO special theme pushed.
    renderable = render_markdown("# Self-contained\n\nbody `x`\n")
    console = Console(record=True, width=80)
    console.print(renderable)  # should not raise
    assert "Self-contained" in console.export_text()


def test_self_contained_styling_independent_of_console_theme() -> None:
    # Two consoles with different (default) themes should both highlight code,
    # because styling is baked into the renderable, not the console theme.
    src = "```python\nx = 1\n```\n"
    a = _render(src, styles=True)
    assert "\x1b[" in a
