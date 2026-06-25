"""v0.13 · C113 · F98②/F99 (review-fix T142) — background task manager BackgroundTaskManager.

In-memory manager that tracks the complete lifecycle of each background sub-Agent task:

- Three paths into the background: ① **explicit** (``background=True``); ② **auto-timeout**
  (``run_foreground`` uses a **real wall-clock** bounded wait of
  ``AgentsConfig.foreground_timeout_s`` — completes within threshold returns result
  synchronously in the foreground; exceeds threshold without completing stays in the background
  and the caller receives the task id with "moved to background");
  ③ **manual switch** (``push_to_background``, **deferred** in this version, reserved
  interface only, no interrupt channel attached).
- **Fork is always background** (``AgentType.FORK`` always runs in the background regardless
  of the ``background`` parameter).
- Background tasks execute in **daemon threads**; ``close()`` does a short join on process
  exit — no thread leaks.
- ``drain_completions()`` acquires the lock, removes and clears the completion feed-back
  buffer, for the main conversation to inject ``<system-reminder>`` in the next turn
  (F99 feed-back path).

**Coupling rule between backgrounding and feed-back (preventing double-reporting)**: each task
carries a ``backgrounded`` flag (lock-protected). When a worker finishes, **only if that flag
is True will it append the completion line to the feed-back buffer** — tasks returned
synchronously from the foreground (flag False) do not enter the buffer, avoiding the
double-report of "both returned synchronously and fed back". Tasks explicitly submitted to
the background or Fork tasks via ``submit`` have the flag set to True from the start;
tasks from ``run_foreground`` start as False, and are only flipped to True when the bounded
wait times out and the caller confirms the task is still RUNNING while holding the lock.

Layering discipline (N51): may import ``agents.runner`` / ``agents.spec`` / ``config`` + stdlib;
**must never import** ``wentian.repl`` / ``wentian.cli``.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from wentian.agents.spec import AgentDef, AgentType, BackgroundTask, TaskStatus
from wentian.config import AgentsConfig

__all__ = ["BackgroundTaskManager"]


class BackgroundTaskManager:
    """In-memory background task manager (thread-safe).

    Parameters
    ----------
    runner:
        Callable compatible with the ``run_subagent`` signature:
        ``(agent_def, prompt, **kw) -> SubAgentResult``. Inject a fake runner for testing.
    cfg:
        :class:`~wentian.config.AgentsConfig` — used to read ``foreground_timeout_s``
        and other settings.
    clock:
        Injectable clock function (default ``time.time``), used **only** for the
        ``BackgroundTask.created_at`` timestamp — **no longer** participates in any timeout
        determination (timeouts are now handled by ``run_foreground``'s real wall-clock
        bounded wait ``done_event.wait(...)``, see F98②).
    """

    def __init__(
        self,
        runner: Callable[..., Any],
        cfg: AgentsConfig,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._runner = runner
        self._cfg = cfg
        self._clock = clock

        self._tasks: dict[str, BackgroundTask] = {}
        # task_id → whether it "should be fed back": when worker finishes, only if this flag
        # is True will it write the completion line to the buffer.
        # Lock-protected; prevents double-reporting of "returned synchronously from foreground
        # + also fed back" (see module docstring).
        self._backgrounded: dict[str, bool] = {}
        self._completion_buffer: list[str] = []
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def submit(
        self,
        agent_def: AgentDef,
        prompt: str,
        *,
        background: bool = False,
        agent_type: AgentType = AgentType.DEFINITION,
        **runner_kwargs: Any,
    ) -> str:
        """Submit a sub-Agent task that **runs in the background from the start**, returning a task id (returns immediately).

        Two types of background tasks:

        1. **Fork is always background**: ``agent_type == AgentType.FORK`` regardless of ``background``.
        2. **Explicit background**: ``background=True``.

        These tasks have their ``backgrounded`` flag set to True from the start, execute in a
        daemon thread, are called with ``background=True`` passed to the runner (connected to
        F97's third-layer background tool filter), and after completion append the completion
        line to the feed-back buffer (F99).

        Note: the **foreground with timeout** semantics of ``background=False`` no longer goes
        through ``submit`` — use :meth:`run_foreground` instead (real wall-clock bounded wait
        → moves to background on timeout).
        """
        task_id = self._make_task(agent_def, prompt, agent_type)
        # Explicit background / Fork: mark as should-feed-back from the start (always background).
        with self._lock:
            self._backgrounded[task_id] = True

        thread = self._spawn_daemon_thread(
            task_id,
            agent_def,
            prompt,
            runner_kwargs,
            runner_background=True,
        )
        with self._lock:
            self._threads.append(thread)

        return task_id

    def run_foreground(
        self,
        agent_def: AgentDef,
        prompt: str,
        **runner_kwargs: Any,
    ) -> tuple[str | None, str, bool]:
        """Run a sub-Agent in the foreground with a **real wall-clock** bounded wait of ``foreground_timeout_s`` (F98②).

        Returns ``(result_text_or_None, task_id, backgrounded)``:

        - Completed within threshold → ``(task.result, task_id, False)`` (synchronous foreground
          return; worker saw flag False so it did **not** write to the feed-back buffer —
          no double-reporting).
        - Exceeded threshold without completion → ``(None, task_id, True)`` (stays in background
          to continue, drained via feed-back after completion).

        Race-free design (worker-completes-first vs caller-times-out-first: both interleavings
        are correct and no double-reporting): the worker writes state inside ``self._lock`` and
        **only appends the feed-back line when this task's flag is True**, then calls
        ``done_event.set()``. After the caller's ``done_event.wait(timeout)``:

        - If returns ``True`` (completed) → return result from foreground (worker saw flag False
          at that point, did not feed back).
        - If timed out → **re-check task.status inside the lock**:
          - Already not RUNNING (a race where completion happened between the wait timeout and
            acquiring the lock) → it already finished as a foreground task, flag is still False,
            not fed back → return result.
          - Still RUNNING → flip ``backgrounded=True`` and return "moved to background"; the
            still-running worker will see True inside the lock when it finishes → append
            feed-back line → drain in the next turn.
        """
        task_id = self._make_task(agent_def, prompt, AgentType.DEFINITION)
        done_event = threading.Event()
        with self._lock:
            self._backgrounded[task_id] = False

        thread = threading.Thread(
            target=self._run_task,
            args=(task_id, agent_def, prompt, runner_kwargs),
            kwargs={"runner_background": False, "done_event": done_event},
            name=f"fg-task-{task_id[:8]}",
            daemon=True,
        )
        with self._lock:
            self._threads.append(thread)
        thread.start()

        completed = done_event.wait(self._cfg.foreground_timeout_s)

        with self._lock:
            task = self._tasks.get(task_id)
            if completed or (task is not None and task.status != TaskStatus.RUNNING):
                # Foreground completion (or race where task completed between wait timeout
                # and acquiring the lock): worker saw backgrounded=False at that point,
                # definitely did not feed back → return result synchronously.
                result_text = task.result if task is not None else None
                return result_text, task_id, False
            # Timed out and still RUNNING: flip flag to move to background; worker will see
            # True when it finishes → feed back.
            self._backgrounded[task_id] = True
        return None, task_id, True

    def get(self, task_id: str) -> BackgroundTask | None:
        """Look up a task by id; returns ``None`` if not found."""
        with self._lock:
            return self._tasks.get(task_id)

    def list(self) -> list[BackgroundTask]:
        """Return a list of all tasks sorted in ascending order by ``created_at``; returns ``[]`` if no tasks."""
        with self._lock:
            tasks = list(self._tasks.values())
        tasks.sort(key=lambda t: t.created_at)
        return tasks

    def drain_completions(self) -> str:
        """Remove and clear the completion feed-back buffer, returning the joined string (F99).

        A second call returns an empty string (buffer already cleared). Thread-safe.
        """
        with self._lock:
            if not self._completion_buffer:
                return ""
            lines = list(self._completion_buffer)
            self._completion_buffer.clear()
        return "\n".join(lines)

    def push_to_background(self, task_id: str) -> str:
        """**Reserved interface** (F98 path ③ "manual switch" — deferred in this version, no interrupt channel attached).

        F98 path ③ refers to the user **pressing a key** at runtime to push a foreground
        sub-Agent to the background, which requires connecting the v0.4 interrupt channel to
        switch away from the foreground task while it is running. This version **does not
        attach the key** (explicitly recorded as "not doing" in spec.md), so this method
        **has not yet been connected to any interrupt source** — it will not truly interrupt a
        ``run_foreground`` call that is waiting in the foreground (that path's
        timeout-to-background is covered by ② "real wall-clock bounded wait"). This method
        and its return behavior are retained as a placeholder entry point for future connection;
        currently it only validates that the id exists and returns a placeholder confirmation
        message.
        """
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            return f"id={task_id} does not exist"
        return f"id={task_id} moved to background, task continues executing in the background"

    def close(self) -> None:
        """Short join all daemon threads (no thread leaks).

        Each thread waits at most 2s; continues after timeout (daemon threads do not block
        process exit).
        """
        with self._lock:
            threads = list(self._threads)
        for t in threads:
            t.join(timeout=2.0)

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _make_task(
        self, agent_def: AgentDef, prompt: str, agent_type: AgentType
    ) -> str:
        """Register a RUNNING task and return a new id (``created_at`` uses the injected clock)."""
        task_id = str(uuid.uuid4())
        task = BackgroundTask(
            id=task_id,
            kind=agent_type,
            label=agent_def.name,
            status=TaskStatus.RUNNING,
            result=None,
            usage={},
            prompt=prompt,
            created_at=self._clock(),
        )
        with self._lock:
            self._tasks[task_id] = task
        return task_id

    def _spawn_daemon_thread(
        self,
        task_id: str,
        agent_def: AgentDef,
        prompt: str,
        runner_kwargs: dict,
        *,
        runner_background: bool,
    ) -> threading.Thread:
        """Create and start a daemon thread to execute the task, returning the thread."""
        thread = threading.Thread(
            target=self._run_task,
            args=(task_id, agent_def, prompt, runner_kwargs),
            kwargs={"runner_background": runner_background},
            name=f"bg-task-{task_id[:8]}",
            daemon=True,
        )
        thread.start()
        return thread

    def _run_task(
        self,
        task_id: str,
        agent_def: AgentDef,
        prompt: str,
        runner_kwargs: dict,
        *,
        runner_background: bool,
        done_event: threading.Event | None = None,
    ) -> None:
        """Execute the runner in a daemon thread, write back status/result/usage; gate feed-back by flag.

        - ``runner_background`` determines the ``background`` value passed to the runner
          (connected to F97's third-layer background tool filter): ``submit`` explicit
          background / Fork passes True; ``run_foreground`` passes False (foreground tool
          set for foreground start).
        - ``done_event`` (injected by ``run_foreground``) calls ``set()`` after writing
          completion back, so the caller's bounded wait can detect completion. **Set
          regardless of success or failure** (finally).

        Error softening (N54): any exception → FAILED, result contains error info, never crashes.
        """
        try:
            result = self._runner(
                agent_def, prompt, background=runner_background, **runner_kwargs
            )
            self._on_task_done(task_id, result)
        except Exception as exc:  # noqa: BLE001 — N54 softening
            self._on_task_failed(task_id, str(exc))
        finally:
            if done_event is not None:
                done_event.set()

    def _on_task_done(self, task_id: str, result: Any) -> None:
        """Task completed successfully: write back DONE status, result, usage; only append feed-back line if flag is True."""
        # Check stop_reason: treat anything other than COMPLETED as failure
        stop_reason = getattr(result, "stop_reason", "COMPLETED")
        text = getattr(result, "text", str(result))
        usage = getattr(result, "usage", {})

        if stop_reason == "COMPLETED":
            with self._lock:
                task = self._tasks.get(task_id)
                if task is not None:
                    task.status = TaskStatus.DONE
                    task.result = text
                    task.usage = usage or {}
                # Only write to buffer if this task should be fed back (background / moved to
                # background), to prevent double-reporting.
                if self._backgrounded.get(task_id):
                    self._completion_buffer.append(
                        self._format_completion_line(task_id, text)
                    )
        else:
            # Abnormal completion → FAILED
            self._on_task_failed(task_id, text, usage=usage)

    def _on_task_failed(
        self, task_id: str, error_text: str, *, usage: dict | None = None
    ) -> None:
        """Task failed: write back FAILED status and error info; only append feed-back line if flag is True."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is not None:
                task.status = TaskStatus.FAILED
                task.result = error_text
                task.usage = usage or {}
            # Only write to buffer if this task should be fed back (background / moved to
            # background), to prevent double-reporting.
            if self._backgrounded.get(task_id):
                self._completion_buffer.append(
                    self._format_completion_line(task_id, f"[FAILED] {error_text}")
                )

    @staticmethod
    def _format_completion_line(task_id: str, result_text: str) -> str:
        """Format a single feed-back line: ``id=<id> completed: <result>``."""
        return f"id={task_id} completed: {result_text}"
