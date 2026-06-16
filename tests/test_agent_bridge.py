"""v0.4 · C15 · F30（任务 T48）— StreamBridge 与 call_in_thread 的测试。

StreamBridge 是 render._StreamPump 的异步教学镜像：同样的守护线程泵体 +
("event", e) / ("error", exc) / ("end", None) 队列三元协议，但消费侧换成
async drain（asyncio.sleep 轮询，不阻塞事件循环）。

测试约定：纯 pytest，async 部分用 ``asyncio.run(...)`` 包裹，不引
pytest-asyncio。脚本化同步生成器扮演 provider.stream()。任何测试都不允许
挂死：阻塞生成器留在守护线程上自然悬挂（不 join），墙钟时间显式断言。
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from wentian.agent.bridge import StreamBridge, call_in_thread
from wentian.providers.base import Done, TextDelta, ThinkingDelta


# ---------------------------------------------------------------------------
# 辅助：在事件循环里收集 drain 的产出
# ---------------------------------------------------------------------------


async def _collect(bridge, interrupt, stop_after=None):
    """收集 drain 产出；若 *stop_after* 给定，收满该数量后置位 interrupt。"""
    collected = []
    async for event in bridge.drain(interrupt):
        collected.append(event)
        if stop_after is not None and len(collected) >= stop_after:
            interrupt.set()
    return collected


# ---------------------------------------------------------------------------
# StreamBridge
# ---------------------------------------------------------------------------


class TestStreamBridgeDrain:
    """drain 正常路径：原序透传、interrupt=None。"""

    def test_three_events_in_order_with_none_interrupt(self):
        """三事件生成器 → drain(None) 按原序产出全部三个事件后结束。"""
        events = [ThinkingDelta("想"), TextDelta("答案"), Done(None)]

        def gen():
            yield from events

        collected = asyncio.run(_collect(StreamBridge(gen()), None))

        assert collected == events


class TestStreamBridgeInterrupt:
    """drain 中断路径：预先置位 / 中途置位都不得泄漏后续事件。"""

    def test_preset_interrupt_yields_nothing(self):
        """interrupt 预先置位 → drain 立即返回，零事件产出。"""

        def gen():
            yield TextDelta("不该出现")
            yield Done(None)

        interrupt = threading.Event()
        interrupt.set()

        start = time.monotonic()
        collected = asyncio.run(_collect(StreamBridge(gen()), interrupt))
        elapsed = time.monotonic() - start

        assert collected == []
        assert elapsed < 1.0

    def test_mid_stream_interrupt_stops_further_events(self):
        """收到两个事件后置位 interrupt → 即使后续事件已入队也不再产出。"""
        events = [TextDelta("一"), TextDelta("二"), TextDelta("三"), Done(None)]

        def gen():
            yield from events

        interrupt = threading.Event()
        collected = asyncio.run(_collect(StreamBridge(gen()), interrupt, stop_after=2))

        assert collected == events[:2]


class TestStreamBridgeError:
    """drain 错误路径：生成器异常原样重抛。"""

    def test_generator_error_reraised_from_drain(self):
        """生成器中途抛 RuntimeError → drain 重抛同一异常。"""

        def gen():
            yield TextDelta("x")
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            asyncio.run(_collect(StreamBridge(gen()), None))


class TestStreamBridgeBlockingGenerator:
    """阻塞生成器：泵线程挂死也不能拖住事件循环（守护线程遗留，不 join）。"""

    def test_interrupt_unblocks_drain_while_generator_hangs(self):
        """生成器产出两事件后永久 wait → drain 拿到两事件、interrupt 置位
        后及时正常返回，悬挂的泵线程留作守护线程不影响退出。"""
        hang = threading.Event()  # 永不置位 → 生成器挂死

        def gen():
            yield TextDelta("前")
            yield TextDelta("半")
            hang.wait()  # 模拟网络永久阻塞
            yield Done(None)  # pragma: no cover — 永远到不了

        interrupt = threading.Event()

        start = time.monotonic()
        collected = asyncio.run(_collect(StreamBridge(gen()), interrupt, stop_after=2))
        elapsed = time.monotonic() - start

        assert collected == [TextDelta("前"), TextDelta("半")]
        assert elapsed < 1.5


# ---------------------------------------------------------------------------
# call_in_thread
# ---------------------------------------------------------------------------


class TestCallInThread:
    """call_in_thread：独立守护线程跑阻塞函数，返回值/异常透出。"""

    def test_returns_slow_function_result(self):
        """慢函数（sleep 0.1s）的返回值原样透出。"""

        def slow(a, b):
            time.sleep(0.1)
            return a + b

        result = asyncio.run(call_in_thread(slow, 40, 2))

        assert result == 42

    def test_reraises_function_exception(self):
        """函数抛 ValueError → call_in_thread 处重抛同一异常。"""

        def boom():
            raise ValueError("bad input")

        with pytest.raises(ValueError, match="bad input"):
            asyncio.run(call_in_thread(boom))
