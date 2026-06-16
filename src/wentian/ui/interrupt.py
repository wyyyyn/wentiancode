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
    """默认监听器：__enter__ 返回 None（render 走直接路径）；__exit__ no-op。

    非 TTY / 测试路径使用；不创建线程，不产生任何中断语义。
    """

    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> None:
        return None


class EscListener:
    """v0.2 · C6 · F18（任务 T24）— 真实终端 Esc 监听器。

    ``__enter__`` 把终端切到 cbreak（保留 ISIG，Ctrl+C 仍发 SIGINT），
    启动守护线程逐字节读 stdin：

    - 裸 ``\\x1b``（50ms 内无后续字节）→ 置位本轮 Event 并退出线程；
    - 转义序列（方向键等 ``\\x1b[...``）→ 整段丢弃，不误触发；
    - 其他按键 → 丢弃（echo 关闭，不污染 Live 区域）。

    ``__exit__`` 停线程、冲洗输入缓冲并还原 termios——任何路径
    （包括 with 体内异常）都必须还原。fd 非 TTY 时整体降级：
    ``__enter__`` 返回 ``None``（render 走直接路径），``__exit__`` no-op。
    可跨轮复用：每次 ``__enter__`` 都创建全新 Event 与线程。
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
            # 非 TTY（管道 / 重定向）：降级为直接路径，不监听。
            self._saved = None
            self._active_fd = None
            return None
        try:
            tty.setcbreak(fd)
        except termios.error:
            # setcbreak 半途失败：还原已保存的状态后降级。
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
            return None  # 降级模式：__enter__ 未做任何改动
        fd = self._active_fd
        try:
            if self._stop is not None:
                self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=0.3)
        finally:
            # 无论线程是否按时退出，都必须还原终端状态。
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
                return  # fd 已被关闭等异常情形：静默退出
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
                        os.read(fd, 16)  # 转义序列（方向键等）整段丢弃
                    except OSError:
                        return
                    continue
                event.set()
                return
            # 其他字节：丢弃（cbreak 下 echo 已关，按键不会污染 Live）
