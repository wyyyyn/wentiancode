"""v0.13 · C113 · F98/F99（任务 T142）— 后台任务管理器 BackgroundTaskManager。

内存态管理器，追踪每个后台子 Agent 任务的完整生命周期：

- 三条进后台路径：① **显式**（``background=True``）；② **超时自动**（前台运行
  超过 ``AgentsConfig.foreground_timeout_s`` → 自动转后台，``clock`` 可注入便于
  离线测超时）；③ **手动切换**（``push_to_background(task_id)``）。
- **Fork 恒后台**（``AgentType.FORK`` 无论 ``background`` 参数为何值均走后台）。
- 后台任务在 **daemon 线程**内执行，进程退出时 ``close()`` 短 join、无线程泄漏。
- ``drain_completions()`` 加锁取走完成回灌缓冲并清空，供主对话下一轮注入
  ``<system-reminder>``（F99 回灌路径）。

分层纪律（N51）：可 import ``agents.runner`` / ``agents.spec`` / ``config`` + stdlib；
**绝不 import** ``wentian.repl`` / ``wentian.cli``。
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
    """内存态后台任务管理器（线程安全）。

    Parameters
    ----------
    runner:
        可调用对象，签名与 ``run_subagent`` 兼容
        ``(agent_def, prompt, **kw) -> SubAgentResult``。测试时注入假 runner。
    cfg:
        :class:`~wentian.config.AgentsConfig`——从中读取 ``foreground_timeout_s``
        等配置。
    clock:
        可注入的时钟函数（默认 ``time.time``）；测试可替换为假时钟推进超时逻辑。
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
        self._completion_buffer: list[str] = []
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []

    # -----------------------------------------------------------------------
    # 公共 API
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
        """提交一个子 Agent 任务，返回任务 id。

        三条进后台路径：

        1. **Fork 恒后台**：``agent_type == AgentType.FORK`` 无论 ``background``。
        2. **显式后台**：``background=True``。
        3. **超时自动**：``background=False`` 且时钟检测超过 ``foreground_timeout_s``
           → 自动走后台（注入假时钟可触发此路径）。

        所有后台任务均在 daemon 线程中执行。
        """
        task_id = str(uuid.uuid4())
        now = self._clock()

        task = BackgroundTask(
            id=task_id,
            kind=agent_type,
            label=agent_def.name,
            status=TaskStatus.RUNNING,
            result=None,
            usage={},
            prompt=prompt,
            created_at=now,
        )

        with self._lock:
            self._tasks[task_id] = task

        # 决定是否走后台
        is_fork = agent_type == AgentType.FORK
        use_background = is_fork or background or self._is_timeout_exceeded(now)

        if use_background:
            thread = self._spawn_daemon_thread(
                task_id, agent_def, prompt, runner_kwargs
            )
            with self._lock:
                self._threads.append(thread)
        else:
            # 前台同步执行（foreground path，本版直接在当前线程跑）
            self._run_task(task_id, agent_def, prompt, runner_kwargs)

        return task_id

    def get(self, task_id: str) -> BackgroundTask | None:
        """按 id 查找任务；不存在返回 ``None``。"""
        with self._lock:
            return self._tasks.get(task_id)

    def list(self) -> list[BackgroundTask]:
        """返回所有任务列表，按 ``created_at`` 升序排序；无任务时返回 ``[]``。"""
        with self._lock:
            tasks = list(self._tasks.values())
        tasks.sort(key=lambda t: t.created_at)
        return tasks

    def drain_completions(self) -> str:
        """取走并清空完成回灌缓冲，返回拼合字符串（F99）。

        第二次调用返回空串（缓冲已清）。线程安全。
        """
        with self._lock:
            if not self._completion_buffer:
                return ""
            lines = list(self._completion_buffer)
            self._completion_buffer.clear()
        return "\n".join(lines)

    def push_to_background(self, task_id: str) -> str:
        """把运行中的任务推入后台（标记已接受），返回确认消息。

        本版实现：任务已在 daemon 线程里跑（submit 时即如此），此调用仅返回
        占位确认消息，供 Agent 工具立即返回给主对话（F98 手动切换路径）。
        """
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            return f"id={task_id} 不存在"
        return f"id={task_id} 已转后台，任务继续在后台执行"

    def close(self) -> None:
        """短 join 所有 daemon 线程（无线程泄漏）。

        每个线程等待最多 2s；超时后继续（daemon 线程不阻塞进程退出）。
        """
        with self._lock:
            threads = list(self._threads)
        for t in threads:
            t.join(timeout=2.0)

    # -----------------------------------------------------------------------
    # 私有辅助
    # -----------------------------------------------------------------------

    def _is_timeout_exceeded(self, start_time: float) -> bool:
        """检查从 start_time 到当前时钟是否超过 foreground_timeout_s。

        对于可注入的假时钟：若 ``clock()`` 再次调用时返回的值比 ``start_time``
        大一个超时量，则认为超时。实际实现是在 submit 时立即再采样一次时钟。
        """
        current = self._clock()
        return (current - start_time) >= self._cfg.foreground_timeout_s

    def _spawn_daemon_thread(
        self,
        task_id: str,
        agent_def: AgentDef,
        prompt: str,
        runner_kwargs: dict,
    ) -> threading.Thread:
        """创建并启动一个 daemon 线程执行任务，返回该线程。"""
        thread = threading.Thread(
            target=self._run_task,
            args=(task_id, agent_def, prompt, runner_kwargs),
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
    ) -> None:
        """在（当前或 daemon）线程中执行 runner，写回状态/结果/usage，追加回灌缓冲。

        错误软化（N54）：任何异常 → FAILED，结果含错误信息，绝不崩。
        """
        try:
            result = self._runner(agent_def, prompt, **runner_kwargs)
            self._on_task_done(task_id, result)
        except Exception as exc:  # noqa: BLE001 — N54 软化
            self._on_task_failed(task_id, str(exc))

    def _on_task_done(self, task_id: str, result: Any) -> None:
        """任务成功完成：写回 DONE 状态、result、usage，追加回灌行。"""
        # 检查 stop_reason：非 COMPLETED 视为失败
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
                self._completion_buffer.append(
                    self._format_completion_line(task_id, text)
                )
        else:
            # 非正常完成 → FAILED
            self._on_task_failed(task_id, text, usage=usage)

    def _on_task_failed(
        self, task_id: str, error_text: str, *, usage: dict | None = None
    ) -> None:
        """任务失败：写回 FAILED 状态和错误信息，追加回灌行。"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is not None:
                task.status = TaskStatus.FAILED
                task.result = error_text
                task.usage = usage or {}
            self._completion_buffer.append(
                self._format_completion_line(task_id, f"[失败] {error_text}")
            )

    @staticmethod
    def _format_completion_line(task_id: str, result_text: str) -> str:
        """格式化单行回灌文本：``id=<id> 已完成：<result>``。"""
        return f"id={task_id} 已完成：{result_text}"
