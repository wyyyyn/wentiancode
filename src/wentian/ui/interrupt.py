"""v0.2 · C6 · F18 (task T23) — InterruptListener protocol and default null implementation.

REPL holds an :class:`InterruptListener` and enters it around each chat
round; the value yielded by ``__enter__`` is passed straight through to
``Renderer.render_stream(interrupt=...)``:

- A real listener (EscListener, T24) yields a ``threading.Event`` that is
  set when the user presses Esc → render consumes the stream through the
  interruptible pump path.
- :class:`NullListener` (default — non-TTY / tests) yields ``None`` →
  render keeps the v0.1 direct-iteration path: no pump thread, fully
  deterministic, zero overhead.

stdlib only (threading / typing). NO prompt_toolkit import here — repl.py
imports this module and must stay prompt_toolkit-free (layering rule).
"""

from __future__ import annotations

import os
import select
import sys
import termios
import threading
import tty
from typing import Protocol

__all__ = ["EscListener", "InterruptListener", "NullListener"]


class InterruptListener(Protocol):
    """Context manager handing out the interrupt Event for one chat round.

    ``__enter__`` returns the ``threading.Event`` to watch during the
    round, or ``None`` to request the direct (non-pump) render path.
    ``__exit__`` releases whatever the listener holds (key hooks, etc.)
    and must run on every path, including provider errors.
    """

    def __enter__(self) -> threading.Event | None: ...

    def __exit__(self, *exc: object) -> None: ...


class NullListener:
    """Default listener: __enter__ returns None (render takes the direct path); __exit__ is a no-op.

    Used for non-TTY / test paths; creates no threads, produces no interrupt semantics.
    """

    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> None:
        return None


class EscListener:
    """v0.2 · C6 · F18 (task T24) — real terminal Esc listener.

    ``__enter__`` switches the terminal to cbreak mode (preserves ISIG, Ctrl+C still sends SIGINT),
    and starts a daemon thread reading stdin byte by byte:

    - Bare ``\\x1b`` (no subsequent byte within 50ms) → sets the current round's Event and exits the thread;
    - Escape sequences (arrow keys etc. ``\\x1b[...``) → discarded entirely, no false trigger;
    - Other keystrokes → discarded (echo disabled, does not pollute the Live area).

    ``__exit__`` stops the thread, flushes the input buffer, and restores termios — on every path
    (including exceptions inside the with body) restoration is mandatory. If fd is not a TTY,
    the whole thing degrades gracefully:
    ``__enter__`` returns ``None`` (render takes the direct path), ``__exit__`` is a no-op.
    Reusable across rounds: each ``__enter__`` creates a fresh Event and thread.
    """

    def __init__(self, fd: int | None = None) -> None:
        self._fd = fd
        self._saved: list | None = None
        self._stop: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._active_fd: int | None = None

    def __enter__(self) -> threading.Event | None:
        fd = self._fd if self._fd is not None else sys.stdin.fileno()
        try:
            self._saved = termios.tcgetattr(fd)
        except termios.error:
            # Non-TTY (pipe / redirect): degrade to direct path, no listening.
            self._saved = None
            self._active_fd = None
            return None
        try:
            tty.setcbreak(fd)
        except termios.error:
            # setcbreak failed midway: restore saved state then degrade.
            termios.tcsetattr(fd, termios.TCSADRAIN, self._saved)
            self._saved = None
            self._active_fd = None
            return None
        self._active_fd = fd
        event = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._watch,
            args=(fd, event, self._stop),
            daemon=True,
        )
        self._thread.start()
        return event

    def __exit__(self, *exc: object) -> None:
        if self._active_fd is None:
            return None  # Degraded mode: __enter__ made no changes
        fd = self._active_fd
        try:
            if self._stop is not None:
                self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=0.3)
        finally:
            # Whether or not the thread exits on time, terminal state must be restored.
            self._thread = None
            self._stop = None
            self._active_fd = None
            try:
                termios.tcflush(fd, termios.TCIFLUSH)
            finally:
                if self._saved is not None:
                    termios.tcsetattr(fd, termios.TCSADRAIN, self._saved)
                    self._saved = None
        return None

    @staticmethod
    def _watch(fd: int, event: threading.Event, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                r, _, _ = select.select([fd], [], [], 0.1)
            except (OSError, ValueError):
                return  # fd has been closed or similar exceptional condition: exit silently
            if not r:
                continue
            try:
                data = os.read(fd, 1)
            except OSError:
                return
            if data == b"\x1b":
                try:
                    r2, _, _ = select.select([fd], [], [], 0.05)
                except (OSError, ValueError):
                    return
                if r2:
                    try:
                        os.read(fd, 16)  # Escape sequence (arrow keys etc.) discarded entirely
                    except OSError:
                        return
                    continue
                event.set()
                return
            # Other bytes: discard (echo is off under cbreak, keystrokes won't pollute the Live area)
