"""v0.2 · C1/C5 · F13/F17（任务 T28）

文天像素猫 mascot — 以 ``=^_^=`` 为骨架：尖耳、眯眼、两侧胡须、``_`` 嘴。

横幅取静态睁眼帧；等待动画在睁眼/眨眼两帧间轮换（约每 2 秒眨一次）。
像素栅格用半块字符打包：每个字符行装两行像素，"▀" 的 fg=上像素、
bg=下像素（见 banner.py 的渲染说明）。非 TTY 下 rich 剥色、留字形。
"""

from __future__ import annotations

from rich.text import Text

__all__ = ["PIXEL_OPEN", "PIXEL_BLINK", "TEXT_FACES", "pick_frame", "render_mascot"]

# ---------------------------------------------------------------------------
# 像素帧 — 13 × 6。0 透明 · 1 体色（朱砂）· 2 眼/嘴（墨）· 3 胡须（灰墨）
# ---------------------------------------------------------------------------

def _grid(rows: list[str]) -> list[list[int]]:
    return [[int(ch) if ch != "." else 0 for ch in row] for row in rows]


PIXEL_OPEN: list[list[int]] = _grid([
    ".11.......11.",   # 耳尖
    ".111.....111.",   # 耳
    ".11211111211.",   # 头顶 + 眼上半（睁眼为竖长眼）
    "3.121111121.3",   # 眼下半 + 外侧胡须
    "3311122211133",   # 胡须 = = + 嘴 _
    "..111111111..",   # 下巴
])

PIXEL_BLINK: list[list[int]] = _grid([
    ".11.......11.",
    ".111.....111.",
    ".11111111111.",   # 眨眼：眼上半收起
    "3.121111121.3",   # 只剩下半 → 眯眼
    "3311122211133",
    "..111111111..",
])

_COLORS = {
    1: "#C84B31",  # 朱砂
    2: "#14110F",  # 墨（眼/嘴）
    3: "#8A8F98",  # 灰墨（胡须）
}

# 流式期单行文本帧（与像素帧同节奏眨眼）
TEXT_FACES: tuple[str, str] = ("=^_^=", "=-_-=")


def pick_frame(elapsed: float) -> int:
    """眨眼节奏：每 2 秒周期里最后半秒闭眼（0=睁，1=眨）。"""
    return 1 if int(elapsed * 2) % 4 == 3 else 0


def render_mascot(grid: list[list[int]]) -> Text:
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
