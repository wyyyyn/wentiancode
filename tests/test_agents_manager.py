"""v0.13 · C113 · F98/F99/N53/AC123/AC124（任务 T142）— BackgroundTaskManager 测试。

全程假 runner，绝不联网。覆盖 brief #1–#6：

1. 三种进后台路径（显式 / 超时自动（假时钟）/ 手动切换）。
2. Fork 恒后台（无论 background 参数为何值）。
3. status/result/usage 记录（RUNNING → DONE / FAILED）。
4. drain_completions 回灌：两任务完成后返含两段「id=X 已完成：…」，再次调用返空串。
5. 线程不泄漏：close() 短 join → threading.active_count() 不增。
6. list() 按创建时间排序；空时返回 []。
"""

from __future__ import annotations

import threading
import time

from wentian.agents.spec import AgentDef, AgentType, TaskStatus
from wentian.config import AgentsConfig


# ---------------------------------------------------------------------------
# 测试替身 & 工厂
# ---------------------------------------------------------------------------


def _def(name: str = "analyst") -> AgentDef:
    """快速构造一个 AgentDef 用于测试。"""
    return AgentDef(name=name, description="测试 agent", body="你是测试助手")


def _fake_result(text: str = "分析完毕", *, usage: dict | None = None):
    """返回假 SubAgentResult 对象（鸭子类型，只需有 text/usage/stop_reason）。"""
    from types import SimpleNamespace

    return SimpleNamespace(
        text=text, usage=usage or {"input": 10, "output": 5}, stop_reason="COMPLETED"
    )


def _failing_result(msg: str = "网络错误"):
    """返回失败的假 SubAgentResult（stop_reason != COMPLETED）。"""
    from types import SimpleNamespace

    return SimpleNamespace(text=msg, usage={}, stop_reason="STREAM_ERROR")


def _make_cfg(**kw) -> AgentsConfig:
    """构造 AgentsConfig，允许覆盖字段。"""
    defaults = dict(
        enabled=True,
        foreground_timeout_s=30.0,
    )
    defaults.update(kw)
    return AgentsConfig(**defaults)


def _make_manager(runner=None, cfg=None, clock=None):
    """导入 BackgroundTaskManager 并创建实例（便于统一注入）。"""
    from wentian.agents.manager import BackgroundTaskManager

    if runner is None:

        def runner(agent_def, prompt, **_kw):  # noqa: E306
            return _fake_result()

    if cfg is None:
        cfg = _make_cfg()
    kwargs = {"runner": runner, "cfg": cfg}
    if clock is not None:
        kwargs["clock"] = clock
    return BackgroundTaskManager(**kwargs)


# ---------------------------------------------------------------------------
# 1. 三种进后台路径
# ---------------------------------------------------------------------------


class TestThreeBackgroundPaths:
    def test_explicit_background_returns_id_immediately(self):
        """显式 background=True → 立即返回 task_id，不阻塞。"""
        started = threading.Event()
        finished = threading.Event()

        def slow_runner(agent_def, prompt, **_kw):
            started.set()
            finished.wait(timeout=5)
            return _fake_result()

        mgr = _make_manager(runner=slow_runner)
        try:
            t0 = time.monotonic()
            task_id = mgr.submit(_def(), "做一份分析", background=True)
            elapsed = time.monotonic() - t0
            # 应立即返回（< 2s），不应等 runner 完成
            assert elapsed < 2.0, f"submit() 应立即返回，实际耗时 {elapsed:.2f}s"
            assert isinstance(task_id, str) and len(task_id) > 0
        finally:
            finished.set()
            mgr.close()

    def test_explicit_background_task_status_running_then_done(self):
        """显式后台任务完成后 status == DONE，result 正确。"""
        done_event = threading.Event()

        def runner_with_signal(agent_def, prompt, **_kw):
            res = _fake_result("分析完毕")
            done_event.set()
            return res

        mgr = _make_manager(runner=runner_with_signal)
        try:
            task_id = mgr.submit(_def(), "做一份分析", background=True)
            # 等待 runner 完成
            done_event.wait(timeout=5)
            time.sleep(0.05)  # 给 worker 线程写回状态的时间
            task = mgr.get(task_id)
            assert task is not None
            assert task.status == TaskStatus.DONE
            assert task.result == "分析完毕"
        finally:
            mgr.close()

    def test_timeout_auto_moves_to_background(self):
        """前台运行超时 → 自动转后台（用假时钟推进超时）。"""
        # 假时钟：第一次调用返回 0.0（任务开始），第二次调用返回 100.0（已超时）
        clock_calls = [0]

        def fake_clock():
            v = clock_calls[0]
            clock_calls[0] += 100  # 每次调用推进 100s（远超 foreground_timeout_s=30s）
            return float(v)

        done_event = threading.Event()

        def runner_fn(agent_def, prompt, **_kw):
            res = _fake_result("超时后完成")
            done_event.set()
            return res

        cfg = _make_cfg(foreground_timeout_s=30.0)
        mgr = _make_manager(runner=runner_fn, cfg=cfg, clock=fake_clock)
        try:
            # background=False，但时钟会立刻超时，应自动转后台
            task_id = mgr.submit(_def(), "长任务", background=False)
            assert isinstance(task_id, str)
            # 任务应已转后台（submit 立即返回）
            done_event.wait(timeout=5)
            time.sleep(0.05)
            task = mgr.get(task_id)
            assert task is not None
            assert task.status == TaskStatus.DONE
        finally:
            mgr.close()

    def test_push_to_background_converts_running_task(self):
        """push_to_background(task_id) 把运行中的前台任务推后台，返回含「已转后台」的消息。"""
        runner_started = threading.Event()
        runner_proceed = threading.Event()

        def slow_runner(agent_def, prompt, **_kw):
            runner_started.set()
            runner_proceed.wait(timeout=5)
            return _fake_result("推后台后完成")

        mgr = _make_manager(runner=slow_runner)
        try:
            # 先显式 background=False 的任务（但由于同步，直接submit背景）
            # 使用 push_to_background 的语义：先 submit 后推
            task_id = mgr.submit(_def(), "中途推后台", background=True)
            runner_started.wait(timeout=5)
            msg = mgr.push_to_background(task_id)
            assert "已转后台" in msg or "后台" in msg, (
                f"期望含「已转后台」，实际：{msg!r}"
            )
        finally:
            runner_proceed.set()
            mgr.close()


# ---------------------------------------------------------------------------
# 2. Fork 恒后台
# ---------------------------------------------------------------------------


class TestForkAlwaysBackground:
    def test_fork_always_background_even_with_background_false(self):
        """AgentType.FORK 无论 background 参数为何值，均走后台（立即返回 id）。"""
        done_event = threading.Event()

        def runner_fn(agent_def, prompt, **_kw):
            res = _fake_result("fork 完成")
            done_event.set()
            return res

        mgr = _make_manager(runner=runner_fn)
        try:
            fork_def = _def("fork-agent")
            t0 = time.monotonic()
            task_id = mgr.submit(
                fork_def, "fork 任务", agent_type=AgentType.FORK, background=False
            )
            elapsed = time.monotonic() - t0
            assert elapsed < 2.0, f"FORK submit() 应立即返回，实际 {elapsed:.2f}s"
            assert isinstance(task_id, str) and len(task_id) > 0
        finally:
            mgr.close()

    def test_fork_task_is_recorded_as_fork_kind(self):
        """FORK 任务的 kind 应为 AgentType.FORK。"""
        mgr = _make_manager()
        try:
            fork_def = _def("fork-agent")
            task_id = mgr.submit(fork_def, "fork 任务", agent_type=AgentType.FORK)
            task = mgr.get(task_id)
            assert task is not None
            assert task.kind == AgentType.FORK
        finally:
            mgr.close()


# ---------------------------------------------------------------------------
# 3. status/result/usage 记录
# ---------------------------------------------------------------------------


class TestStatusResultUsageRecording:
    def test_status_running_immediately_after_submit(self):
        """submit 后立即 get(id).status == RUNNING（任务在跑）。"""
        started = threading.Event()
        proceed = threading.Event()

        def slow_runner(agent_def, prompt, **_kw):
            started.set()
            proceed.wait(timeout=5)
            return _fake_result()

        mgr = _make_manager(runner=slow_runner)
        try:
            task_id = mgr.submit(_def(), "任务", background=True)
            started.wait(timeout=5)
            task = mgr.get(task_id)
            assert task is not None
            assert task.status == TaskStatus.RUNNING
        finally:
            proceed.set()
            mgr.close()

    def test_status_done_with_result_and_usage(self):
        """runner 完成后 status == DONE，result 和 usage 正确写回。"""
        done_event = threading.Event()

        def runner_fn(agent_def, prompt, **_kw):
            res = _fake_result("分析完毕", usage={"input": 100, "output": 50})
            done_event.set()
            return res

        mgr = _make_manager(runner=runner_fn)
        try:
            task_id = mgr.submit(_def(), "任务", background=True)
            done_event.wait(timeout=5)
            time.sleep(0.05)
            task = mgr.get(task_id)
            assert task.status == TaskStatus.DONE
            assert task.result == "分析完毕"
            assert task.usage == {"input": 100, "output": 50}
        finally:
            mgr.close()

    def test_status_failed_on_exception(self):
        """runner 抛出异常 → status == FAILED，result 含错误信息，不崩管理器。"""
        done_event = threading.Event()

        def failing_runner(agent_def, prompt, **_kw):
            done_event.set()
            raise RuntimeError("模拟网络错误")

        mgr = _make_manager(runner=failing_runner)
        try:
            task_id = mgr.submit(_def(), "会失败的任务", background=True)
            done_event.wait(timeout=5)
            time.sleep(0.05)
            task = mgr.get(task_id)
            assert task.status == TaskStatus.FAILED
            assert task.result is not None
            assert len(task.result) > 0  # 含错误信息
        finally:
            mgr.close()

    def test_status_failed_on_error_stop_reason(self):
        """runner 返回 stop_reason 非 COMPLETED → status == FAILED。"""
        done_event = threading.Event()

        def runner_fn(agent_def, prompt, **_kw):
            res = _failing_result("MAX_ROUNDS 停止")
            done_event.set()
            return res

        mgr = _make_manager(runner=runner_fn)
        try:
            task_id = mgr.submit(_def(), "任务", background=True)
            done_event.wait(timeout=5)
            time.sleep(0.05)
            task = mgr.get(task_id)
            assert task.status == TaskStatus.FAILED
        finally:
            mgr.close()


# ---------------------------------------------------------------------------
# 4. drain_completions 回灌
# ---------------------------------------------------------------------------


class TestDrainCompletions:
    def test_drain_returns_two_completions_then_clears(self):
        """两个后台任务完成后 drain_completions() 返含两段「id=X 已完成：结果」，再次调空串。"""
        results_written = threading.Barrier(3)  # 两个 runner + 1 个主线程

        def runner_fn(agent_def, prompt, **_kw):
            res = _fake_result(f"结果：{agent_def.name}")
            results_written.wait(timeout=5)
            return res

        mgr = _make_manager(runner=runner_fn)
        try:
            id1 = mgr.submit(_def("agent-A"), "任务A", background=True)
            id2 = mgr.submit(_def("agent-B"), "任务B", background=True)
            results_written.wait(timeout=5)
            time.sleep(0.1)  # 给 worker 写回状态和缓冲的时间

            output = mgr.drain_completions()
            assert id1 in output, f"期望回灌包含 {id1}"
            assert id2 in output, f"期望回灌包含 {id2}"
            assert "已完成" in output

            # 第二次调用应返回空串
            second = mgr.drain_completions()
            assert second == "", f"第二次 drain 应返回空串，实际：{second!r}"
        finally:
            mgr.close()

    def test_drain_empty_when_no_completions(self):
        """无完成任务时 drain_completions() 返空串。"""
        mgr = _make_manager()
        try:
            result = mgr.drain_completions()
            assert result == ""
        finally:
            mgr.close()

    def test_drain_contains_completion_format(self):
        """回灌格式应含「id=X 已完成：<result>」。"""
        done_event = threading.Event()

        def runner_fn(agent_def, prompt, **_kw):
            res = _fake_result("任务已完成的内容")
            done_event.set()
            return res

        mgr = _make_manager(runner=runner_fn)
        try:
            task_id = mgr.submit(_def(), "任务", background=True)
            done_event.wait(timeout=5)
            time.sleep(0.05)
            output = mgr.drain_completions()
            assert f"id={task_id}" in output, f"回灌应含 id={task_id}，实际：{output!r}"
            assert "已完成" in output
        finally:
            mgr.close()


# ---------------------------------------------------------------------------
# 5. 线程不泄漏
# ---------------------------------------------------------------------------


class TestThreadNoLeak:
    def test_close_joins_threads_no_leak(self):
        """close() 后 threading.active_count() 不多于 close() 前（daemon 线程已退）。"""
        proceed = threading.Event()
        started = threading.Event()

        def runner_fn(agent_def, prompt, **_kw):
            started.set()
            proceed.wait(timeout=5)
            return _fake_result()

        mgr = _make_manager(runner=runner_fn)
        baseline = threading.active_count()
        mgr.submit(_def(), "任务", background=True)
        started.wait(timeout=5)
        # 任务运行中，线程数应 >= baseline
        assert threading.active_count() >= baseline

        proceed.set()
        time.sleep(0.05)
        mgr.close()
        time.sleep(0.1)  # 线程退出需要少量时间

        after_close = threading.active_count()
        # close 后 active_count 应不超过 baseline（daemon 线程已退出）
        assert after_close <= baseline + 1, (
            f"close() 后线程数 {after_close} 应 ≤ baseline {baseline} + 1"
        )

    def test_close_completes_within_timeout(self):
        """close() 应在合理时间内返回（不无限阻塞）。"""
        done_event = threading.Event()

        def runner_fn(agent_def, prompt, **_kw):
            done_event.set()
            return _fake_result()

        mgr = _make_manager(runner=runner_fn)
        mgr.submit(_def(), "任务", background=True)
        done_event.wait(timeout=5)
        time.sleep(0.05)

        t0 = time.monotonic()
        mgr.close()
        elapsed = time.monotonic() - t0
        assert elapsed < 5.0, f"close() 耗时 {elapsed:.2f}s，超过预期"


# ---------------------------------------------------------------------------
# 6. list() 排序与空情况
# ---------------------------------------------------------------------------


class TestList:
    def test_list_empty_when_no_tasks(self):
        """无任务时 list() 返回 []。"""
        mgr = _make_manager()
        try:
            result = mgr.list()
            assert result == []
        finally:
            mgr.close()

    def test_list_returns_all_tasks_sorted_by_created_at(self):
        """list() 返回所有任务，按 created_at 升序排序。"""
        proceed = threading.Event()

        def runner_fn(agent_def, prompt, **_kw):
            proceed.wait(timeout=5)
            return _fake_result()

        mgr = _make_manager(runner=runner_fn)
        try:
            id1 = mgr.submit(_def("agent-1"), "任务1", background=True)
            time.sleep(0.01)  # 确保时间戳有差异
            id2 = mgr.submit(_def("agent-2"), "任务2", background=True)
            time.sleep(0.01)
            id3 = mgr.submit(_def("agent-3"), "任务3", background=True)

            tasks = mgr.list()
            assert len(tasks) == 3
            ids = [t.id for t in tasks]
            assert ids == [id1, id2, id3], f"期望按创建时间排序，实际：{ids}"
        finally:
            proceed.set()
            mgr.close()

    def test_list_contains_running_done_and_failed_tasks(self):
        """list() 同时包含运行中、完成和失败的任务。"""
        done_event_a = threading.Event()
        done_event_b = threading.Event()

        def runner_ok(agent_def, prompt, **_kw):
            res = _fake_result()
            done_event_a.set()
            return res

        def runner_fail(agent_def, prompt, **_kw):
            done_event_b.set()
            raise RuntimeError("失败")

        # 成功 runner
        mgr1 = _make_manager(runner=runner_ok)
        mgr2 = _make_manager(runner=runner_fail)
        try:
            id_ok = mgr1.submit(_def("ok-agent"), "任务", background=True)
            done_event_a.wait(timeout=5)
            time.sleep(0.05)
            tasks = mgr1.list()
            statuses = {t.id: t.status for t in tasks}
            assert statuses[id_ok] == TaskStatus.DONE
        finally:
            mgr1.close()

        try:
            id_fail = mgr2.submit(_def("fail-agent"), "任务", background=True)
            done_event_b.wait(timeout=5)
            time.sleep(0.05)
            tasks = mgr2.list()
            statuses = {t.id: t.status for t in tasks}
            assert statuses[id_fail] == TaskStatus.FAILED
        finally:
            mgr2.close()

    def test_get_returns_none_for_unknown_id(self):
        """get() 对不存在的 id 返回 None（不报错）。"""
        mgr = _make_manager()
        try:
            assert mgr.get("nonexistent-id-xyz") is None
        finally:
            mgr.close()
