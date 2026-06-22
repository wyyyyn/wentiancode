"""v0.13 · C113 · F98②/F99（review-fix T142）— 后台任务管理器 BackgroundTaskManager。

内存态管理器，追踪每个后台子 Agent 任务的完整生命周期：

- 三条进后台路径：① **显式**（``background=True``）；② **超时自动**（``run_foreground``
  以**真实墙钟**有界等待 ``AgentsConfig.foreground_timeout_s``——阈值内完成则前台
  同步返结果，超阈值未完则留在后台继续、调用方拿到 task id「已转后台」）；
  ③ **手动切换**（``push_to_background``，本版**延后**，仅预留接口、未挂中断通道）。
- **Fork 恒后台**（``AgentType.FORK`` 无论 ``background`` 参数为何值均走后台）。
- 后台任务在 **daemon 线程**内执行，进程退出时 ``close()`` 短 join、无线程泄漏。
- ``drain_completions()`` 加锁取走完成回灌缓冲并清空，供主对话下一轮注入
  ``<system-reminder>``（F99 回灌路径）。

**进后台与回灌的耦合规则（防双报）**：每个任务带一个 ``backgrounded`` 标志（锁
保护）。worker 收束时**只有该标志为 True 才把完成行追加进回灌缓冲**——前台同步
返回的任务（标志 False）不进缓冲，避免「既同步返结果又回灌」的双报。``submit``
的显式后台 / Fork 任务从一开始标志即 True；``run_foreground`` 的任务初始 False，
仅当有界等待超时、调用方在锁内确认其仍 RUNNING 时才翻成 True。

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
        可注入的时钟函数（默认 ``time.time``），**仅**用于 ``BackgroundTask.created_at``
        时间戳——**不再**参与任何超时判定（超时已改由 ``run_foreground`` 的真实墙钟
        有界等待 ``done_event.wait(...)`` 实现，详见 F98②）。
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
        # task_id → 是否「应回灌」：worker 收束时仅当此标志 True 才写完成行进缓冲。
        # 锁保护；防「前台同步返结果 + 又回灌」的双报（见模块 docstring）。
        self._backgrounded: dict[str, bool] = {}
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
        """提交一个**从一开始就走后台**的子 Agent 任务，返回任务 id（立即返回）。

        两类进后台：

        1. **Fork 恒后台**：``agent_type == AgentType.FORK`` 无论 ``background``。
        2. **显式后台**：``background=True``。

        这些任务的 ``backgrounded`` 标志从一开始即 True，在 daemon 线程内执行，
        调用 runner 时传 ``background=True``（接 F97 第三层后台工具过滤），收束后把
        完成行追加进回灌缓冲（F99）。

        注：``background=False`` 的**前台带超时**语义已不再走 ``submit``——改用
        :meth:`run_foreground`（真实墙钟有界等待 → 超时转后台）。
        """
        task_id = self._make_task(agent_def, prompt, agent_type)
        # 显式后台 / Fork：从一开始就标记应回灌（恒后台）。
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
        """前台运行子 Agent，以**真实墙钟**有界等待 ``foreground_timeout_s``（F98②）。

        返回 ``(result_text_or_None, task_id, backgrounded)``：

        - 阈值内完成 → ``(task.result, task_id, False)``（前台同步返回；worker 见
          标志 False 故**未**写回灌缓冲——不双报）。
        - 超阈值未完 → ``(None, task_id, True)``（留后台继续，收束后经回灌 drain）。

        无竞态设计（worker-先完成 vs 调用方-先超时 两种交错都正确、且不双报）：
        worker 在 ``self._lock`` 内写状态、并**仅当本任务标志为 True 时**才追加回灌行，
        然后 ``done_event.set()``。调用方 ``done_event.wait(timeout)`` 后：

        - 若返回 ``True``（已完成）→ 前台返结果（worker 当时见标志 False、未回灌）。
        - 若超时 → **在锁内复检** task.status：
          - 已非 RUNNING（在 wait 超时与取锁之间刚好完成的竞态）→ 它已作为前台收束、
            标志仍 False、未回灌 → 返结果。
          - 仍 RUNNING → 翻 ``backgrounded=True`` 返「已转后台」；尚在跑的 worker
            收束时会在锁内见 True → 追加回灌行 → 下一轮 drain。
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
                # 前台收束（或 wait 超时与取锁间隙刚好完成的竞态）：
                # worker 当时见 backgrounded=False，绝未回灌 → 同步返结果。
                result_text = task.result if task is not None else None
                return result_text, task_id, False
            # 超时且仍 RUNNING：翻标志转后台；worker 收束时将见 True → 回灌。
            self._backgrounded[task_id] = True
        return None, task_id, True

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
        """**预留接口**（F98 路径③「手动切换」——本版延后，未挂中断通道）。

        F98 路径③ 指运行期用户**按键**把前台子 Agent 推后台，需接 v0.4 interrupt
        通道才能在跑动中切走前台任务。本版**不挂键**（spec.md「不做」明确记录），因此
        本方法**尚未接到任何中断来源**——它不会真正中断一个正在前台等待的 ``run_foreground``
        调用（那条路径的超时转后台由 ②「真实墙钟有界等待」覆盖）。保留此方法与其返回
        行为，仅作未来接通的占位入口；当前仅校验 id 存在并回占位确认消息。
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

    def _make_task(
        self, agent_def: AgentDef, prompt: str, agent_type: AgentType
    ) -> str:
        """登记一个 RUNNING 任务，返回新 id（``created_at`` 取注入时钟）。"""
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
        """创建并启动一个 daemon 线程执行任务，返回该线程。"""
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
        """在 daemon 线程中执行 runner，写回状态/结果/usage；按标志门控回灌。

        - ``runner_background`` 决定传给 runner 的 ``background`` 值（接 F97 第三层
          后台工具过滤）：``submit`` 显式后台 / Fork 传 True；``run_foreground`` 传
          False（前台起步用前台工具集）。
        - ``done_event``（``run_foreground`` 注入）在写回完成后 ``set()``，供调用方
          的有界等待感知收束。**无论成功或失败都 set**（finally）。

        错误软化（N54）：任何异常 → FAILED，结果含错误信息，绝不崩。
        """
        try:
            result = self._runner(
                agent_def, prompt, background=runner_background, **runner_kwargs
            )
            self._on_task_done(task_id, result)
        except Exception as exc:  # noqa: BLE001 — N54 软化
            self._on_task_failed(task_id, str(exc))
        finally:
            if done_event is not None:
                done_event.set()

    def _on_task_done(self, task_id: str, result: Any) -> None:
        """任务成功完成：写回 DONE 状态、result、usage；标志为 True 才追加回灌行。"""
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
                # 仅当本任务应回灌（后台 / 已转后台）才写缓冲，防双报。
                if self._backgrounded.get(task_id):
                    self._completion_buffer.append(
                        self._format_completion_line(task_id, text)
                    )
        else:
            # 非正常完成 → FAILED
            self._on_task_failed(task_id, text, usage=usage)

    def _on_task_failed(
        self, task_id: str, error_text: str, *, usage: dict | None = None
    ) -> None:
        """任务失败：写回 FAILED 状态和错误信息；标志为 True 才追加回灌行。"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is not None:
                task.status = TaskStatus.FAILED
                task.result = error_text
                task.usage = usage or {}
            # 仅当本任务应回灌（后台 / 已转后台）才写缓冲，防双报。
            if self._backgrounded.get(task_id):
                self._completion_buffer.append(
                    self._format_completion_line(task_id, f"[失败] {error_text}")
                )

    @staticmethod
    def _format_completion_line(task_id: str, result_text: str) -> str:
        """格式化单行回灌文本：``id=<id> 已完成：<result>``。"""
        return f"id={task_id} 已完成：{result_text}"
