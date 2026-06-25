"""Background memory extraction orchestration — daemon fire-and-forget.

v0.9 · C58 · F67/N31 (task T105)

``MemoryRunner`` wires :mod:`memory.extractor` + :mod:`memory.store` onto the
REPL's ``COMPLETED`` round hook (the REPL wiring itself is wave four — this
module only exposes a clean interface). Each :meth:`submit` spins a **daemon
``threading.Thread`` (fire-and-forget)** that:

- builds a **fresh provider instance** via the injected ``provider_factory`` —
  the conversation provider object is never shared across threads (N31);
- runs ``extract → write_note / upsert_index`` for ``add``/``update`` decisions
  (``skip`` is ignored), writing the INDEX through the store's lock (serialized,
  N31);
- **swallows every exception to stderr** — extraction never crashes the main
  thread, never interrupts the session, never touches the conversation
  ``messages`` (it reads the list handed in, writes only the memory dir).

:meth:`submit` returns immediately (it never blocks the REPL input loop).
:meth:`close` joins live workers on a short timeout for ``/exit`` — it never
blocks process exit because the workers are daemons; an unfinished worker is
simply abandoned (no leak: daemon threads don't keep the interpreter alive).

No asyncio anywhere — the extraction stream is consumed synchronously inside the
worker thread, mirroring v0.8 ``summarizer``.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from datetime import datetime, timezone

from wentian.memory import extractor
from wentian.memory.store import MemoryConfig, MemoryStore
from wentian.providers.base import Message

__all__ = ["MemoryRunner"]


class MemoryRunner:
    """Background daemon orchestrator for per-round memory extraction."""

    def __init__(
        self,
        *,
        provider_factory: Callable[[], object],
        store: MemoryStore,
        cfg: MemoryConfig,
        session_id: str,
    ) -> None:
        #: builds a *fresh* provider per worker (never shares the chat provider)
        self._provider_factory = provider_factory
        self._store = store
        self._cfg = cfg
        self._session_id = session_id
        self._threads: list[threading.Thread] = []
        self._threads_lock = threading.Lock()

    # -- public API -------------------------------------------------------

    def submit(self, recent_messages: list[Message]) -> None:
        """Fire-and-forget a background extraction for *recent_messages*.

        Returns immediately. A no-op when ``cfg.enabled`` is false (the whole
        chain is off — equivalent to v0.8). Never raises.
        """
        if not self._cfg.enabled:
            return
        # snapshot the list so the worker never aliases the live conversation,
        # and capture the factory *now* so a later reassignment can't race the
        # worker reading it.
        snapshot = list(recent_messages)
        factory = self._provider_factory
        thread = threading.Thread(
            target=self._run_extraction,
            args=(snapshot, factory),
            name="wentian-memory-extract",
            daemon=True,
        )
        with self._threads_lock:
            self._prune_dead_locked()
            self._threads.append(thread)
        thread.start()

    def close(self, timeout: float = 2.0) -> None:
        """Join live worker threads on a short *timeout* (for ``/exit``).

        Never blocks past ``timeout`` per thread; unfinished daemon workers are
        abandoned (they cannot keep the interpreter alive). Idempotent.
        """
        with self._threads_lock:
            threads = list(self._threads)
        for thread in threads:
            if thread.is_alive():
                thread.join(timeout=timeout)
        with self._threads_lock:
            self._prune_dead_locked()

    # -- internals --------------------------------------------------------

    def _prune_dead_locked(self) -> None:
        """Drop finished threads (caller holds ``_threads_lock``)."""
        self._threads = [t for t in self._threads if t.is_alive()]

    def _run_extraction(
        self, recent_messages: list[Message], factory: Callable[[], object]
    ) -> None:
        """Worker body: build provider → extract → persist; swallow all errors."""
        try:
            provider = factory()
            existing = self._store.read_indexes_for_injection()
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            decisions = extractor.extract(
                provider,
                recent_messages,
                existing,
                source_session=self._session_id,
                now=now,
            )
            for decision in decisions:
                if decision.action == "skip":
                    continue
                self._store.write_note(decision.note)
                self._store.upsert_index(
                    decision.note,
                    action=decision.action,
                    summary=decision.summary,
                )
        except Exception as exc:  # noqa: BLE001 - background must never crash
            print(
                f"[memory] background extraction failed, skipped: {exc!r}",
                file=sys.stderr,
            )
