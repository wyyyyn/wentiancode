"""v0.2 · C1 · F13（任务 T16，T27 改版：Claude Code 式布局）

Startup banner for WentianCode — pure function, no side effects.

Layout mirrors Claude Code's header: a pixel mascot on the left rendered
with half-block characters, three info lines on the right, no panel box.

The mascot（文天小人）: a cinnabar pixel figure wearing an ink-dark 方巾
(scholar's square cap) — same pixel language as Claude's face, own identity.
Pixel grid rows are packed two-per-character-row using "▀" (upper half
block: fg = top pixel, bg = bottom pixel), so the icon stays crisp at
terminal cell resolution. Non-TTY consoles strip color but keep the glyph
structure, which is what the offline tests assert.
"""

from __future__ import annotations

from rich.table import Table
from rich.text import Text

__all__ = ["build_banner"]

# ---------------------------------------------------------------------------
# Pixel mascot — 11 × 6 pixel grid, two pixels per character row.
# 0 = transparent · 1 = body (cinnabar) · 2 = eye (ink) · 3 = cap (ink-dark)
# ---------------------------------------------------------------------------

_PIXELS: list[list[int]] = [
    [0, 0, 3, 3, 3, 3, 3, 3, 3, 0, 0],  # 方巾顶
    [0, 3, 3, 3, 3, 3, 3, 3, 3, 3, 0],  # 方巾檐
    [0, 0, 1, 1, 1, 1, 1, 1, 1, 0, 0],  # 面部上沿
    [1, 0, 1, 2, 1, 1, 1, 2, 1, 0, 1],  # 双目 + 两侧仪
    [0, 0, 1, 1, 1, 1, 1, 1, 1, 0, 0],  # 面部下沿
    [0, 0, 1, 1, 0, 0, 0, 1, 1, 0, 0],  # 双足
]

_COLORS = {
    1: "#C84B31",  # 朱砂
    2: "#14110F",  # 目
    3: "#2B3140",  # 墨色方巾
}


def _render_pixels(grid: list[list[int]]) -> Text:
    """Pack a pixel grid into half-block characters (two pixel rows per line)."""
    icon = Text()
    for top_row, bottom_row in zip(grid[0::2], grid[1::2]):
        if icon.plain:
            icon.append("\n")
        for top, bottom in zip(top_row, bottom_row):
            if not top and not bottom:
                icon.append(" ")
            elif top and bottom:
                icon.append("▀", style=f"{_COLORS[top]} on {_COLORS[bottom]}")
            elif top:
                icon.append("▀", style=_COLORS[top])
            else:
                icon.append("▄", style=_COLORS[bottom])
    return icon


def build_banner(
    *,
    version: str,
    provider_name: str,
    model: str,
    session_id: str,
    resumed: bool,
) -> Table:
    """Return the WentianCode startup banner (pixel mascot + info lines).

    v0.2 · C1 · F13（任务 T27 改版）— Claude Code 式两列布局，无边框面板。

    Parameters
    ----------
    version:       Semver string, e.g. ``"0.2.0"``.
    provider_name: Human-readable provider name from ``Provider.name``.
    model:         Model identifier string; may be empty.
    session_id:    Short ID of the current session.
    resumed:       ``True`` when restoring an existing session.
    """
    info = Text()
    info.append("文天 WentianCode", style="bold")
    info.append(f" v{version}", style="dim")
    info.append("\n")
    if model:
        info.append(provider_name, style="#C84B31")
        info.append(" · ", style="dim")
        info.append(model)
    else:
        info.append(provider_name, style="#C84B31")
    info.append("\n")
    label = "已恢复" if resumed else "新会话"
    info.append(f"{label} ", style="dim")
    info.append(session_id, style="italic dim")

    grid = Table.grid(padding=(0, 2))
    grid.add_column(no_wrap=True)
    grid.add_column()
    grid.add_row(_render_pixels(_PIXELS), info)
    return grid
