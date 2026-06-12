"""v0.4 · C15 · F30（任务 T48）

同步 provider 流 → asyncio 世界的桥接：StreamBridge 与 call_in_thread。

StreamBridge 是 :class:`wentian.render._StreamPump` 的异步教学镜像——线程
泵体与队列三元协议（``("event", e)`` / ``("error", exc)`` / ``("end",
None)``）逐行对应，只是消费侧从同步 ``queue.get(timeout=...)`` 换成
``get_nowait()`` + ``await asyncio.sleep(0.05)`` 轮询，绝不阻塞事件循环。

硬约束（plan R1，勿"改进"）：本模块刻意 **不用** ``asyncio.to_thread`` /
``loop.run_in_executor`` / ``loop.call_soon_threadsafe``。理由：

- ``asyncio.run()`` 收尾时会 join 默认 executor——一个停在 executor 里的
  阻塞 ``input()`` 会让 Ctrl+C 之后的 REPL 整个冻死；
- 从悬挂线程发 ``call_soon_threadsafe`` 会与事件循环关闭过程竞态。

专用守护线程 + queue/holder 轮询永远不会拖住 teardown：线程挂死就让它
挂着（daemon 随进程退出），事件循环侧只做非阻塞轮询。

只 import stdlib 与 ``wentian.providers.base``（类型标注）；绝不 import
``wentian.tools`` / rich / prompt_toolkit。
"""

from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import AsyncIterator, Callable, Iterator

from wentian.providers.base import StreamEvent

__all__ = ["StreamBridge", "call_in_thread"]

#: 事件循环侧轮询间隔（秒）——保证 Esc/中断延迟 ≤50ms。
_POLL_INTERVAL = 0.05


class StreamBridge:
    """每次 provider.stream() 调用对应一个 StreamBridge。

    守护泵线程把同步生成器的事件灌进 :class:`queue.Queue`，事件循环侧用
    :meth:`drain` 异步消费。泵线程体与 ``render._StreamPump._run`` 完全
    同构：事件边界检查停止旗、合法地同线程 close 生成器、每条流恰好一个
    终结项（``error`` 替代 ``end``）。
    """

    def __init__(self, events: Iterator[StreamEvent]) -> None:
        self._events = events
        self._queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        """泵线程体：转发事件直到 stop/end/error（镜像 _StreamPump._run）。"""
        try:
            for event in self._events:
                if self._stop.is_set():
                    # 迭代线程内 close 生成器是合法的；已拉出的这个事件
                    # 直接丢弃——消费者已经离场。
                    close = getattr(self._events, "close", None)
                    if close is not None:
                        close()
                    return
                self._queue.put(("event", event))
        except BaseException as exc:  # noqa: BLE001 — 含 KeyboardInterrupt
            # 每条流恰好一个终结项：错误替代 "end"。
            self._queue.put(("error", exc))
            return
        self._queue.put(("end", None))

    def stop(self) -> None:
        """请泵线程在下一个事件边界退出。"""
        self._stop.set()

    async def drain(
        self, interrupt: threading.Event | None
    ) -> AsyncIterator[StreamEvent]:
        """异步产出事件直到 end/error，*interrupt* 置位则提前返回。

        每轮先用 ``get_nowait()`` 把队列里已积压的事件全部吐出（保证整段
        token 吞吐），并在 yield 每个已出队事件 **之前** 检查 interrupt——
        Esc 之后不漏出任何多余内容；队列空时同样先查 interrupt，再
        ``await asyncio.sleep(0.05)``，中断延迟 ≤50ms。
        """
        while True:
            try:
                kind, payload = self._queue.get_nowait()
            except queue.Empty:
                if interrupt is not None and interrupt.is_set():
                    self.stop()
                    return
                await asyncio.sleep(_POLL_INTERVAL)
                continue
            if kind == "event":
                if interrupt is not None and interrupt.is_set():
                    self.stop()
                    return
                yield payload  # type: ignore[misc]
            elif kind == "error":
                raise payload  # type: ignore[misc]
            else:  # "end"
                return


async def call_in_thread(fn: Callable[..., object], /, *args: object) -> object:
    """在全新守护线程上运行阻塞函数 *fn*，事件循环侧每 50ms 轮询结果。

    *fn* 抛出的异常在此处原样重抛。不走 executor（见模块 docstring 的
    R1 约束）：线程若挂死，daemon 属性保证它不拖住 ``asyncio.run()``
    teardown。
    """
    done = threading.Event()
    holder: dict[str, object] = {}

    def _worker() -> None:
        try:
            holder["result"] = fn(*args)
        except BaseException as exc:  # noqa: BLE001 — 透传给调用方重抛
            holder["error"] = exc
        finally:
            done.set()

    threading.Thread(target=_worker, daemon=True).start()
    while not done.is_set():
        await asyncio.sleep(_POLL_INTERVAL)
    if "error" in holder:
        raise holder["error"]  # type: ignore[misc]
    return holder["result"]
