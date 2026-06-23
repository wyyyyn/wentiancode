"""v0.14 · C130 · F103/F104

ThinkingAnimation — dedicated "thinking" indicator that replaces visible
chain-of-thought (F103). While the model is in its extended-thinking phase we
play a braille spinner + ``🧠 思考中…(Ns)`` timer in a cool/calm color,
visually distinct from the warm 朱砂 waiting mascot ``=^_^=`` (F17). The raw
reasoning text is never shown. When thinking ends, a single persistent
breadcrumb ``💭 思考 Ns`` is left in scrollback (F104).

Reuses the three-phase Live mutual-exclusion pattern of
:class:`wentian.ui.spinner.WaitingSpinner`: on a TTY ``start()`` opens a
transient Rich Live, ``stop()`` tears it down (idempotent), and a double
``start()`` never leaks an orphaned Live.

Usage (TTY)::

    anim = ThinkingAnimation(console)
    anim.start()
    # ... consume ThinkingDelta events (content discarded) ...
    anim.stop()
    console.print(anim.render_breadcrumb())  # leaves 💭 思考 Ns

On non-TTY consoles start()/stop() are no-ops for output but still record t0
so that ``elapsed`` / ``render_line`` / ``render_breadcrumb`` stay usable in
tests.
"""

from __future__ import annotations

import time
from typing import Callable

from rich.console import Console
from rich.live import Live
from rich.text import Text

__all__ = ["BRAILLE_FRAMES", "THINK_STYLE", "ThinkingAnimation", "pick_braille_frame"]

# 盲文 spinner 帧——冷色调思考态专用，与吉祥物 =^_^= 在符号上区分（F103）
BRAILLE_FRAMES: tuple[str, ...] = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧")

# 冷色（钢蓝 / slate-blue, xterm SteelBlue3 区域）——与吉祥物朱砂 #C84B31 在
# 色彩上视觉区分（F103：思考态冷色 vs 等待态朱砂暖色）。
THINK_STYLE: str = "bold #5F87AF"


def pick_braille_frame(elapsed: float) -> int:
    """Index into :data:`BRAILLE_FRAMES`; advances roughly every 0.1s.

    ``int(elapsed * 10) % len(BRAILLE_FRAMES)`` — so 0.0→0, 0.1→1, … 0.7→7,
    then wraps (0.8→0).
    """
    return int(elapsed * 10) % len(BRAILLE_FRAMES)


class ThinkingAnimation:
    """Thinking-phase indicator: braille spinner + elapsed-seconds timer.

    Parameters
    ----------
    console:
        Rich Console to render into.
    clock:
        Zero-argument callable returning a float timestamp (seconds).
        Defaults to :func:`time.monotonic`. Inject a fake clock in tests.
    """

    def __init__(
        self,
        console: Console,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._console = console
        self._clock = clock
        self._t0: float | None = None
        self._live: Live | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Record t0; on TTY also open a transient Rich Live.

        Safe to call repeatedly: any previous Live is stopped first, so a
        double start() never leaks an orphaned Live (which would keep its
        refresh thread alive and hijack stdout via redirect_io). Mirrors
        :meth:`WaitingSpinner.start`.
        """
        self.stop()
        self._t0 = self._clock()
        if self._console.is_terminal:
            self._live = Live(
                get_renderable=self.render_line,
                console=self._console,
                transient=True,
                refresh_per_second=10,
            )
            self._live.start()

    def stop(self) -> None:
        """Stop the Live if running (idempotent, safe before start)."""
        if self._live is not None:
            self._live.stop()
            self._live = None

    def render_line(self) -> Text:
        """Return the thinking-phase status line (F104).

        Format: ``<braille> 🧠 思考中… (<N>s)`` styled :data:`THINK_STYLE`;
        the braille frame comes from :func:`pick_braille_frame`. Pure status —
        contains no chain-of-thought content.
        """
        elapsed = self.elapsed
        frame = BRAILLE_FRAMES[pick_braille_frame(elapsed)]
        seconds = int(elapsed)
        return Text(f"{frame} 🧠 思考中… ({seconds}s)", style=THINK_STYLE)

    def render_breadcrumb(self) -> Text:
        """Return the persistent end-of-thinking breadcrumb (F104).

        Format: ``💭 思考 <N>s`` styled ``dim`` — a single-line trace left in
        scrollback marking that the model did think, and for how long. ``N`` is
        the thinking duration (:attr:`elapsed`).
        """
        seconds = int(self.elapsed)
        return Text(f"💭 思考 {seconds}s", style="dim")

    @property
    def elapsed(self) -> float:
        """Seconds since :meth:`start` was called; ``0.0`` if not yet started."""
        if self._t0 is None:
            return 0.0
        return self._clock() - self._t0
