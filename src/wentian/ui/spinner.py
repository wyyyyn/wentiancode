"""v0.2 · C5 · F17（任务 T20，T28 改版：像素猫动画）

WaitingSpinner — animated elapsed-seconds indicator shown while waiting
for the first streamed token from the model.

T28: the waiting Live shows the blinking pixel cat（render_block，多行）;
the streaming-phase line under the Markdown uses the single-line text
face frames ``=^_^=`` / ``=-_-=``（render_line）. Both share the blink
rhythm from :func:`wentian.ui.mascot.pick_frame`.

Usage (TTY)::

    spinner = WaitingSpinner(console)
    spinner.start()
    # ... blocking LLM call ...
    spinner.stop()

On non-TTY consoles start()/stop() are no-ops for output but still record
t0 so that ``elapsed`` and ``render_line()`` remain usable in tests.
"""

from __future__ import annotations

import time
from typing import Callable

from rich.console import Console
from rich.live import Live
from rich.text import Text

from wentian.ui.mascot import (
    PIXEL_BLINK,
    PIXEL_OPEN,
    TEXT_FACES,
    pick_frame,
    render_mascot,
)

__all__ = ["WaitingSpinner"]


class WaitingSpinner:
    """Animated waiting indicator with elapsed-seconds counter.

    Parameters
    ----------
    console:
        Rich Console to render into.
    clock:
        Zero-argument callable returning a float timestamp (seconds).
        Defaults to :func:`time.monotonic`.  Inject a fake clock in tests.
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
        """Record t0; on TTY also start a Rich Live block.

        Safe to call repeatedly: any previous Live is stopped first, so a
        double start() never leaks an orphaned Live (which would keep its
        refresh thread alive and hijack stdout via redirect_io).
        """
        self.stop()
        self._t0 = self._clock()
        if self._console.is_terminal:
            self._live = Live(
                get_renderable=self.render_block,
                console=self._console,
                transient=True,
                refresh_per_second=8,
            )
            self._live.start()

    def stop(self) -> None:
        """Stop the Live block if running (idempotent, safe before start)."""
        if self._live is not None:
            self._live.stop()
            self._live = None

    def render_line(self) -> Text:
        """Return the streaming-phase single line（v0.2 · C5 · F17 / T28）.

        Format: ``=^_^= 构思中… (<N>s)`` styled dim; the text cat face
        blinks on the shared mascot rhythm.
        """
        elapsed = self.elapsed
        face = TEXT_FACES[pick_frame(elapsed)]
        seconds = int(elapsed)
        return Text(f"{face} 构思中… ({seconds}s)", style="dim")

    def render_block(self) -> Text:
        """Return the waiting-phase block（v0.2 · C5 · F17 / T28）.

        Multi-line blinking pixel cat with the timer line underneath.
        """
        elapsed = self.elapsed
        grid = PIXEL_BLINK if pick_frame(elapsed) else PIXEL_OPEN
        block = render_mascot(grid)
        block.append("\n")
        block.append(f"构思中… ({int(elapsed)}s)", style="dim")
        return block

    @property
    def elapsed(self) -> float:
        """Seconds since :meth:`start` was called; ``0.0`` if not yet started."""
        if self._t0 is None:
            return 0.0
        return self._clock() - self._t0
