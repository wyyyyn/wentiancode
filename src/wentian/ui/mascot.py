"""v0.2 · C1/C5 · F13/F17 (task T28, T29 revision: plain text face)

Wentian mascot — the ``=^_^=`` itself.

T29 decision: dropped pixel art. Half-block character pixels distort severely across
different terminal fonts (2026-06-11 user screenshot: cat rendered as a crab in
Terminal.app), text face is stable in any monospace font.
Banner uses the static open-eye frame; wait/streaming timer row alternates between
open-eye and blink frames (approximately once every 2 seconds).
"""

from __future__ import annotations

__all__ = ["TEXT_FACES", "FACE_STYLE", "pick_frame"]

# Open-eye / blink two frames, fixed-width (5 chars), no distortion in any monospace font
TEXT_FACES: tuple[str, str] = ("=^_^=", "=-_-=")

# Vermilion red — shared by banner and timer row
FACE_STYLE = "bold #C84B31"


def pick_frame(elapsed: float) -> int:
    """Blink rhythm: eyes closed for the last half-second of each 2-second cycle (0=open, 1=blink)."""
    return 1 if int(elapsed * 2) % 4 == 3 else 0
