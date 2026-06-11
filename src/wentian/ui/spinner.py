"""v0.2 · C5 · F17（任务 T20）

WaitingSpinner — animated elapsed-seconds indicator shown while waiting
for the first streamed token from the model.

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

    FRAMES: str = "✻✺✹✸✷✶"

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
                get_renderable=self.render_line,
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
        """Return the current spinner line as a Rich Text object.

        Format: ``<frame> 构思中… (<N>s)`` styled dim.
        """
        elapsed = self.elapsed
        frame_index = int(elapsed * 4) % len(self.FRAMES)
        frame = self.FRAMES[frame_index]
        seconds = int(elapsed)
        return Text(f"{frame} 构思中… ({seconds}s)", style="dim")

    @property
    def elapsed(self) -> float:
        """Seconds since :meth:`start` was called; ``0.0`` if not yet started."""
        if self._t0 is None:
            return 0.0
        return self._clock() - self._t0
