"""v0.4 · C15 · F30 (task T48)

Bridge from synchronous provider stream → asyncio world: StreamBridge and call_in_thread.

StreamBridge is the async teaching mirror of :class:`wentian.render._StreamPump` — the thread
pump body and queue ternary protocol (``("event", e)`` / ``("error", exc)`` / ``("end",
None)``) correspond line by line; the only difference is the consumer side switches from
synchronous ``queue.get(timeout=...)`` to ``get_nowait()`` + ``await asyncio.sleep(0.05)``
polling, never blocking the event loop.

Hard constraint (plan R1, do not "improve"): this module intentionally does **not use**
``asyncio.to_thread`` / ``loop.run_in_executor`` / ``loop.call_soon_threadsafe``. Rationale:

- ``asyncio.run()`` joins the default executor on teardown — a blocking ``input()`` stuck
  in the executor will freeze the entire REPL after Ctrl+C;
- sending ``call_soon_threadsafe`` from a hanging thread will race with the event loop
  shutdown process.

A dedicated daemon thread + queue/holder polling will never hold up teardown: if the thread
hangs, let it hang (daemon exits with the process); the event loop side only does
non-blocking polling.

Only imports stdlib and ``wentian.providers.base`` (type annotations); never imports
``wentian.tools`` / rich / prompt_toolkit.
"""

from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import AsyncIterator, Callable, Iterator

from wentian.providers.base import StreamEvent

__all__ = ["StreamBridge", "call_in_thread"]

#: Event loop polling interval (seconds) — ensures Esc/interrupt latency ≤50ms.
_POLL_INTERVAL = 0.05


class StreamBridge:
    """One StreamBridge per provider.stream() call.

    The daemon pump thread feeds events from the synchronous generator into
    :class:`queue.Queue`; the event loop side consumes them asynchronously via
    :meth:`drain`. The pump thread body is fully isomorphic to ``render._StreamPump._run``:
    checks stop flag at event boundaries, legitimately closes the generator on the same
    thread, and each stream has exactly one terminator (``error`` in place of ``end``).
    """

    def __init__(self, events: Iterator[StreamEvent]) -> None:
        self._events = events
        self._queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        """Pump thread body: forward events until stop/end/error (mirrors _StreamPump._run)."""
        try:
            for event in self._events:
                if self._stop.is_set():
                    # Closing the generator on the iteration thread is valid; the event
                    # already pulled is discarded — the consumer has already left.
                    close = getattr(self._events, "close", None)
                    if close is not None:
                        close()
                    return
                self._queue.put(("event", event))
        except BaseException as exc:  # noqa: BLE001 — includes KeyboardInterrupt
            # Each stream has exactly one terminator: error in place of "end".
            self._queue.put(("error", exc))
            return
        self._queue.put(("end", None))

    def stop(self) -> None:
        """Ask the pump thread to exit at the next event boundary."""
        self._stop.set()

    async def drain(
        self, interrupt: threading.Event | None
    ) -> AsyncIterator[StreamEvent]:
        """Asynchronously yield events until end/error; returns early if *interrupt* is set.

        Each iteration first uses ``get_nowait()`` to flush all backlogged events from the
        queue (ensuring full token throughput), and checks interrupt **before** yielding
        each dequeued event — no extra content leaks after Esc; when the queue is empty,
        also checks interrupt first, then ``await asyncio.sleep(0.05)``, keeping interrupt
        latency ≤50ms.
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
    """Run blocking function *fn* on a fresh daemon thread; event loop polls for result every 50ms.

    Exceptions raised by *fn* are re-raised here as-is. Does not use the executor (see
    module docstring R1 constraint): if the thread hangs, the daemon attribute ensures it
    does not hold up ``asyncio.run()`` teardown.
    """
    done = threading.Event()
    holder: dict[str, object] = {}

    def _worker() -> None:
        try:
            holder["result"] = fn(*args)
        except BaseException as exc:  # noqa: BLE001 — propagate to caller for re-raise
            holder["error"] = exc
        finally:
            done.set()

    threading.Thread(target=_worker, daemon=True).start()
    while not done.is_set():
        await asyncio.sleep(_POLL_INTERVAL)
    if "error" in holder:
        raise holder["error"]  # type: ignore[misc]
    return holder["result"]
