"""v0.2 · C6 · F18（任务 T23）— InterruptListener 协议与默认空实现。

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

import threading
from typing import Protocol

__all__ = ["InterruptListener", "NullListener"]


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
    """默认监听器：__enter__ 返回 None（render 走直接路径）；__exit__ no-op。

    非 TTY / 测试路径使用；不创建线程，不产生任何中断语义。
    """

    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> None:
        return None
