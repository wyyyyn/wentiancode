"""v0.2 · C6 · F18（任务 T24）— EscListener 真实终端 Esc 监听测试。

通过 pty.openpty() 模拟真实终端：slave 端交给 EscListener，
master 端写入按键字节。覆盖：

1. 裸 Esc → Event 置位
2. 方向键转义序列（\\x1b[A）→ 不误触发
3. termios 进出后完整还原
4. with 体内抛异常 → termios 仍还原
5. 非 TTY fd → 降级（__enter__ 返回 None，__exit__ no-op）
6. 跨轮复用：每次 __enter__ 产生全新 Event
"""

from __future__ import annotations

import os
import pty
import termios
import time

import pytest

from wentian.ui.interrupt import EscListener


def _normalized(attrs: list) -> list:
    """清除 lflag 中的 PENDIN 位后返回副本。

    Darwin 内核在 tcsetattr 重新启用 ICANON 时会无条件置位 PENDIN
    （瞬态状态位，下次 read 即清除），它不是我们设置的终端配置，
    比较还原结果时应忽略。
    """
    out = list(attrs)
    out[3] = out[3] & ~termios.PENDIN  # index 3 = lflag
    return out


@pytest.fixture()
def pty_pair():
    master, slave = pty.openpty()
    yield master, slave
    os.close(master)
    os.close(slave)


def test_esc_sets_event(pty_pair):
    master, slave = pty_pair
    with EscListener(fd=slave) as ev:
        assert ev is not None
        os.write(master, b"\x1b")
        assert ev.wait(timeout=1.0) is True


def test_arrow_key_does_not_trigger(pty_pair):
    master, slave = pty_pair
    with EscListener(fd=slave) as ev:
        os.write(master, b"\x1b[A")  # arrow up
        time.sleep(0.3)
        assert ev.is_set() is False


def test_termios_restored_after_exit(pty_pair):
    master, slave = pty_pair
    snapshot = termios.tcgetattr(slave)
    with EscListener(fd=slave):
        pass
    assert _normalized(termios.tcgetattr(slave)) == _normalized(snapshot)


def test_termios_restored_on_exception(pty_pair):
    master, slave = pty_pair
    snapshot = termios.tcgetattr(slave)
    with pytest.raises(RuntimeError):
        with EscListener(fd=slave):
            raise RuntimeError("boom")
    assert _normalized(termios.tcgetattr(slave)) == _normalized(snapshot)


def test_non_tty_fd_degrades_gracefully():
    r, w = os.pipe()
    try:
        listener = EscListener(fd=r)
        with listener as ev:
            assert ev is None
        # __exit__ no-op, no exception — reaching here is the assertion
    finally:
        os.close(r)
        os.close(w)


def test_reentry_fresh_event_per_enter(pty_pair):
    master, slave = pty_pair
    listener = EscListener(fd=slave)

    with listener as ev1:
        os.write(master, b"\x1b")
        assert ev1.wait(timeout=1.0) is True

    with listener as ev2:
        assert ev2 is not ev1
        assert ev2.is_set() is False
        os.write(master, b"\x1b")
        assert ev2.wait(timeout=1.0) is True
