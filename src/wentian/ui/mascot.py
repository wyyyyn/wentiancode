"""v0.2 · C1/C5 · F13/F17（任务 T28，T29 改版：纯文本脸）

文天 mascot — 就是 ``=^_^=`` 本体。

T29 决策：弃像素画。半块字符像素在不同终端字体下严重变形（2026-06-11
用户截图：Terminal.app 下猫渲染成了螃蟹），文本脸在任何等宽字体下都稳定。
横幅取静态睁眼帧；等待/流式计时行在睁眼与眨眼两帧间轮换（约每 2 秒眨一次）。
"""

from __future__ import annotations

__all__ = ["TEXT_FACES", "FACE_STYLE", "pick_frame"]

# 睁眼 / 眨眼 两帧，等宽（5 字符），任何等宽字体下不变形
TEXT_FACES: tuple[str, str] = ("=^_^=", "=-_-=")

# 朱砂色——横幅与计时行共用
FACE_STYLE = "bold #C84B31"


def pick_frame(elapsed: float) -> int:
    """眨眼节奏：每 2 秒周期里最后半秒闭眼（0=睁，1=眨）。"""
    return 1 if int(elapsed * 2) % 4 == 3 else 0
