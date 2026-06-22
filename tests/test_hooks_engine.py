"""v0.12 · C98 · F78/F79/F82/F83（任务 T122）— HookEngine 单元测试。

TDD 红-绿-重构：先确认因模块缺失而失败，再实现转绿。

测试覆盖（全部 T122 RED 用例）：
1. fire 非拦截：命中动作执行 / 条件不命中跳过 / prompt 动作进 _pending / drain_injections 取出清空
2. once：fire 两次 → 仅第一次执行
3. background：后台动作投 daemon 线程、fire 立即返回不阻塞
4. pretool 拦截：exit2 → 返回原因；exit0 → None；多规则首个 exit2 短路
5. pretool fail-open：脚本缺失 / 超时 / 退出码非 0 非 2 → None（记日志，不拦）
6. 软化：任一动作抛异常 → fire/pretool 不冒泡
7. close：后台线程 close() 短 join 不卡
"""

from __future__ import annotations

import stat
import time
from typing import Any

from wentian.hooks.spec import (
    HookEvent,
    HookRule,
    HttpAction,
    PromptAction,
    ShellAction,
    SubAgentAction,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_rule(
    event: HookEvent,
    action: Any,
    *,
    condition: Any = None,
    once: bool = False,
    background: bool = False,
) -> HookRule:
    return HookRule(
        event=event,
        action=action,
        condition=condition,
        once=once,
        background=background,
    )


def _shell_echo(text: str, *, timeout: int = 5) -> ShellAction:
    return ShellAction(command=f"echo {text!r}", timeout=timeout)


# ---------------------------------------------------------------------------
# 1. fire 非拦截事件
# ---------------------------------------------------------------------------


class TestFireBasic:
    def test_fire_runs_matched_shell_action(self, tmp_path: Any) -> None:
        """SessionStart 规则命中 → run_shell 被执行（exit_code 0 无异常）。"""
        from wentian.hooks.engine import HookEngine

        marker = tmp_path / "fired.txt"
        rule = _make_rule(
            HookEvent.SESSION_START,
            ShellAction(command=f"touch {marker}", timeout=5),
        )
        engine = HookEngine([rule])
        engine.fire(HookEvent.SESSION_START, {"session_id": "s1"})

        assert marker.exists()

    def test_fire_skips_unmatched_event(self, tmp_path: Any) -> None:
        """规则注册 SESSION_START，fire SESSION_END → 不执行。"""
        from wentian.hooks.engine import HookEngine

        marker = tmp_path / "fired.txt"
        rule = _make_rule(
            HookEvent.SESSION_START,
            ShellAction(command=f"touch {marker}", timeout=5),
        )
        engine = HookEngine([rule])
        engine.fire(HookEvent.SESSION_END, {})

        assert not marker.exists()

    def test_fire_skips_condition_mismatch(self, tmp_path: Any) -> None:
        """规则含条件 tool_name==Bash，fire with tool_name=Other → 跳过。"""
        from wentian.hooks.engine import HookEngine
        from wentian.hooks.spec import Clause, Condition, Match

        marker = tmp_path / "fired.txt"
        cond = Condition(match=Match.ALL, clauses=(Clause("tool_name", "Bash"),))
        rule = _make_rule(
            HookEvent.POST_TOOL_USE,
            ShellAction(command=f"touch {marker}", timeout=5),
            condition=cond,
        )
        engine = HookEngine([rule])
        engine.fire(HookEvent.POST_TOOL_USE, {"tool_name": "Other"})

        assert not marker.exists()

    def test_fire_condition_match_runs_action(self, tmp_path: Any) -> None:
        """条件命中时正常执行。"""
        from wentian.hooks.engine import HookEngine
        from wentian.hooks.spec import Clause, Condition, Match

        marker = tmp_path / "fired.txt"
        cond = Condition(match=Match.ALL, clauses=(Clause("tool_name", "Bash"),))
        rule = _make_rule(
            HookEvent.POST_TOOL_USE,
            ShellAction(command=f"touch {marker}", timeout=5),
            condition=cond,
        )
        engine = HookEngine([rule])
        engine.fire(HookEvent.POST_TOOL_USE, {"tool_name": "Bash"})

        assert marker.exists()

    def test_prompt_action_accumulates_pending(self) -> None:
        """PromptAction 产出追加进 _pending，drain_injections 取出并清空。"""
        from wentian.hooks.engine import HookEngine

        rule = _make_rule(
            HookEvent.SESSION_START,
            PromptAction(text="injected text for {session_id}"),
        )
        engine = HookEngine([rule])
        engine.fire(HookEvent.SESSION_START, {"session_id": "abc"})

        result = engine.drain_injections()
        assert "injected text for abc" in result

        # drain clears the buffer
        assert engine.drain_injections() == ""

    def test_drain_injections_empty_when_no_prompt(self) -> None:
        """无 prompt 动作时 drain_injections 返回空串。"""
        from wentian.hooks.engine import HookEngine

        engine = HookEngine([])
        assert engine.drain_injections() == ""

    def test_multiple_prompt_actions_joined(self) -> None:
        """多个 prompt 动作产出用换行连接。"""
        from wentian.hooks.engine import HookEngine

        rule1 = _make_rule(HookEvent.SESSION_START, PromptAction(text="line1"))
        rule2 = _make_rule(HookEvent.SESSION_START, PromptAction(text="line2"))
        engine = HookEngine([rule1, rule2])
        engine.fire(HookEvent.SESSION_START, {})

        result = engine.drain_injections()
        assert "line1" in result
        assert "line2" in result


# ---------------------------------------------------------------------------
# 2. once 执行控制
# ---------------------------------------------------------------------------


class TestOnce:
    def test_once_rule_fires_only_first_time(self, tmp_path: Any) -> None:
        """once=True 规则 fire 两次 → 仅第一次执行。"""
        from wentian.hooks.engine import HookEngine

        counter_file = tmp_path / "count.txt"
        counter_file.write_text("0")
        # Use a shell command to increment a counter
        script = tmp_path / "inc.sh"
        script.write_text(f"c=$(cat {counter_file}); echo $((c+1)) > {counter_file}\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        rule = _make_rule(
            HookEvent.SESSION_START,
            ShellAction(command=f"bash {script}", timeout=5),
            once=True,
        )
        engine = HookEngine([rule])
        engine.fire(HookEvent.SESSION_START, {})
        engine.fire(HookEvent.SESSION_START, {})

        count = int(counter_file.read_text().strip())
        assert count == 1, f"Expected 1 execution, got {count}"

    def test_once_false_fires_every_time(self, tmp_path: Any) -> None:
        """once=False（默认）规则 fire 两次 → 执行两次。"""
        from wentian.hooks.engine import HookEngine

        counter_file = tmp_path / "count.txt"
        counter_file.write_text("0")
        script = tmp_path / "inc.sh"
        script.write_text(f"c=$(cat {counter_file}); echo $((c+1)) > {counter_file}\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        rule = _make_rule(
            HookEvent.SESSION_START,
            ShellAction(command=f"bash {script}", timeout=5),
            once=False,
        )
        engine = HookEngine([rule])
        engine.fire(HookEvent.SESSION_START, {})
        engine.fire(HookEvent.SESSION_START, {})

        count = int(counter_file.read_text().strip())
        assert count == 2, f"Expected 2 executions, got {count}"


# ---------------------------------------------------------------------------
# 3. background daemon thread
# ---------------------------------------------------------------------------


class TestBackground:
    def test_background_fire_returns_immediately(self, tmp_path: Any) -> None:
        """background=True 动作投 daemon 线程，fire 立即返回（主线程不阻塞）。"""
        from wentian.hooks.engine import HookEngine

        marker = tmp_path / "done.txt"

        # Slow action: sleep briefly then touch marker
        rule = _make_rule(
            HookEvent.SESSION_START,
            ShellAction(command=f"sleep 0.3 && touch {marker}", timeout=5),
            background=True,
        )
        engine = HookEngine([rule])

        t0 = time.monotonic()
        engine.fire(HookEvent.SESSION_START, {})
        elapsed = time.monotonic() - t0

        # fire should return well before the 0.3s sleep finishes
        assert elapsed < 0.2, f"fire blocked for {elapsed:.3f}s, expected <0.2s"
        assert not marker.exists(), "marker should not exist yet (background)"

        # Clean up — wait briefly for the background thread
        engine.close()

    def test_background_action_eventually_executes(self, tmp_path: Any) -> None:
        """后台动作在 close() join 后确认已执行。"""
        from wentian.hooks.engine import HookEngine

        marker = tmp_path / "bg_done.txt"
        rule = _make_rule(
            HookEvent.SESSION_START,
            ShellAction(command=f"touch {marker}", timeout=5),
            background=True,
        )
        engine = HookEngine([rule])
        engine.fire(HookEvent.SESSION_START, {})
        # Give thread time to run then close
        time.sleep(0.3)
        engine.close()

        assert marker.exists()

    def test_background_thread_is_daemon(self) -> None:
        """后台线程是 daemon=True（不阻止进程退出）。"""
        from wentian.hooks.engine import HookEngine

        # Use a shell command that sets an event after starting
        rule = _make_rule(
            HookEvent.SESSION_START,
            ShellAction(command="sleep 10", timeout=15),
            background=True,
        )
        engine = HookEngine([rule])
        engine.fire(HookEvent.SESSION_START, {})

        # Check that the engine tracks threads and they are daemons
        time.sleep(0.05)
        assert len(engine._threads) > 0
        for t in engine._threads:
            assert t.daemon, "background thread must be daemon"

        engine.close()


# ---------------------------------------------------------------------------
# 4. pretool 拦截
# ---------------------------------------------------------------------------


class TestPretool:
    def test_pretool_exit2_returns_reason_from_stderr(self, tmp_path: Any) -> None:
        """PreToolUse shell exit 2 with stderr reason → pretool 返回 stderr 文本。"""
        from wentian.hooks.engine import HookEngine

        script = tmp_path / "deny.sh"
        script.write_text("echo 'blocked by policy' >&2; exit 2\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        rule = _make_rule(
            HookEvent.PRE_TOOL_USE,
            ShellAction(command=f"bash {script}", timeout=5),
        )
        engine = HookEngine([rule])
        reason = engine.pretool({"tool_name": "Bash"})

        assert reason is not None
        assert "blocked by policy" in reason

    def test_pretool_exit2_returns_stdout_when_stderr_empty(
        self, tmp_path: Any
    ) -> None:
        """exit 2 + stderr 空 → 取 stdout 作为拒绝原因。"""
        from wentian.hooks.engine import HookEngine

        script = tmp_path / "deny_stdout.sh"
        script.write_text("echo 'deny stdout reason'; exit 2\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        rule = _make_rule(
            HookEvent.PRE_TOOL_USE,
            ShellAction(command=f"bash {script}", timeout=5),
        )
        engine = HookEngine([rule])
        reason = engine.pretool({"tool_name": "Bash"})

        assert reason is not None
        assert "deny stdout reason" in reason

    def test_pretool_exit0_returns_none(self, tmp_path: Any) -> None:
        """PreToolUse shell exit 0 → pretool 返回 None（不拦截）。"""
        from wentian.hooks.engine import HookEngine

        rule = _make_rule(
            HookEvent.PRE_TOOL_USE,
            ShellAction(command="exit 0", timeout=5),
        )
        engine = HookEngine([rule])
        reason = engine.pretool({"tool_name": "Bash"})

        assert reason is None

    def test_pretool_multiple_rules_first_exit2_short_circuits(
        self, tmp_path: Any
    ) -> None:
        """多规则按声明顺序，首个 exit2 短路，不执行后续规则。"""
        from wentian.hooks.engine import HookEngine

        marker = tmp_path / "second_ran.txt"
        deny_script = tmp_path / "deny.sh"
        deny_script.write_text("echo 'first blocked' >&2; exit 2\n")
        deny_script.chmod(deny_script.stat().st_mode | stat.S_IEXEC)

        rule1 = _make_rule(
            HookEvent.PRE_TOOL_USE,
            ShellAction(command=f"bash {deny_script}", timeout=5),
        )
        rule2 = _make_rule(
            HookEvent.PRE_TOOL_USE,
            ShellAction(command=f"touch {marker}; exit 2", timeout=5),
        )
        engine = HookEngine([rule1, rule2])
        reason = engine.pretool({"tool_name": "Bash"})

        assert "first blocked" in (reason or "")
        assert not marker.exists(), "second rule should not have run (short-circuit)"

    def test_pretool_no_rules_returns_none(self) -> None:
        """无 PreToolUse 规则 → pretool 返回 None。"""
        from wentian.hooks.engine import HookEngine

        engine = HookEngine([])
        assert engine.pretool({"tool_name": "Bash"}) is None

    def test_pretool_non_pretooluse_rules_ignored(self) -> None:
        """只有 SESSION_START 规则（非 PreToolUse）→ pretool 返回 None。"""
        from wentian.hooks.engine import HookEngine

        rule = _make_rule(
            HookEvent.SESSION_START,
            ShellAction(command="exit 2", timeout=5),
        )
        engine = HookEngine([rule])
        assert engine.pretool({"tool_name": "Bash"}) is None

    def test_pretool_prompt_action_side_effect_no_block(self) -> None:
        """非 shell 动作（PromptAction）在 PreToolUse 作副作用，不阻拦（返回 None）。"""
        from wentian.hooks.engine import HookEngine

        rule = _make_rule(
            HookEvent.PRE_TOOL_USE,
            PromptAction(text="side effect injection"),
        )
        engine = HookEngine([rule])
        reason = engine.pretool({"tool_name": "Bash"})

        # Non-shell actions cannot block
        assert reason is None
        # But the prompt was buffered
        assert "side effect injection" in engine.drain_injections()

    def test_pretool_condition_mismatch_skips_rule(self, tmp_path: Any) -> None:
        """条件不命中的 PreToolUse 规则被跳过。"""
        from wentian.hooks.engine import HookEngine
        from wentian.hooks.spec import Clause, Condition, Match

        cond = Condition(match=Match.ALL, clauses=(Clause("tool_name", "Bash"),))
        rule = _make_rule(
            HookEvent.PRE_TOOL_USE,
            ShellAction(command="exit 2", timeout=5),
            condition=cond,
        )
        engine = HookEngine([rule])
        # Different tool_name → condition mismatch → not blocked
        assert engine.pretool({"tool_name": "Other"}) is None


# ---------------------------------------------------------------------------
# 5. pretool fail-open
# ---------------------------------------------------------------------------


class TestPretoolFailOpen:
    def test_missing_script_fail_open(self, tmp_path: Any) -> None:
        """shell 脚本不存在 → fail-open（返回 None），不抛异常。"""
        from wentian.hooks.engine import HookEngine

        rule = _make_rule(
            HookEvent.PRE_TOOL_USE,
            ShellAction(command="/nonexistent/script_xyz.sh", timeout=5),
        )
        engine = HookEngine([rule])
        # Should not raise; fail-open
        reason = engine.pretool({"tool_name": "Bash"})
        assert reason is None

    def test_timeout_fail_open(self, tmp_path: Any) -> None:
        """shell 命令超时 → fail-open（返回 None），不抛。"""
        from wentian.hooks.engine import HookEngine

        rule = _make_rule(
            HookEvent.PRE_TOOL_USE,
            ShellAction(command="sleep 10; exit 2", timeout=1),
        )
        engine = HookEngine([rule])
        reason = engine.pretool({"tool_name": "Bash"})
        assert reason is None

    def test_exit_code_other_than_0_or_2_fail_open(self, tmp_path: Any) -> None:
        """退出码非 0 非 2（如 1）→ fail-open（返回 None），不拦截。"""
        from wentian.hooks.engine import HookEngine

        rule = _make_rule(
            HookEvent.PRE_TOOL_USE,
            ShellAction(command="exit 1", timeout=5),
        )
        engine = HookEngine([rule])
        reason = engine.pretool({"tool_name": "Bash"})
        assert reason is None

    def test_fail_open_logs_warning(self, tmp_path: Any, caplog: Any) -> None:
        """超时时应记录日志（fail-open 不静默）。"""
        import logging

        from wentian.hooks.engine import HookEngine

        rule = _make_rule(
            HookEvent.PRE_TOOL_USE,
            ShellAction(command="sleep 10", timeout=1),
        )
        engine = HookEngine([rule])
        with caplog.at_level(logging.DEBUG, logger="wentian.hooks"):
            engine.pretool({"tool_name": "Bash"})

        # Should have some log record about the failure
        assert len(caplog.records) > 0


# ---------------------------------------------------------------------------
# 6. 软化：动作异常被吞
# ---------------------------------------------------------------------------


class TestSoftening:
    def test_fire_swallows_action_exception(self, monkeypatch: Any) -> None:
        """动作函数抛异常 → fire 不冒泡。"""
        from wentian.hooks import actions
        from wentian.hooks.engine import HookEngine

        def _raise(*a: Any, **kw: Any) -> None:
            raise RuntimeError("boom from action")

        monkeypatch.setattr(actions, "run_shell", _raise)

        rule = _make_rule(
            HookEvent.SESSION_START,
            ShellAction(command="echo hi", timeout=5),
        )
        engine = HookEngine([rule])
        # Must not raise
        engine.fire(HookEvent.SESSION_START, {})

    def test_pretool_swallows_action_exception(self, monkeypatch: Any) -> None:
        """pretool 中动作抛异常 → fail-open，返回 None，不冒泡。"""
        from wentian.hooks import actions
        from wentian.hooks.engine import HookEngine

        def _raise(*a: Any, **kw: Any) -> None:
            raise RuntimeError("boom in pretool")

        monkeypatch.setattr(actions, "run_shell", _raise)

        rule = _make_rule(
            HookEvent.PRE_TOOL_USE,
            ShellAction(command="exit 2", timeout=5),
        )
        engine = HookEngine([rule])
        # Must not raise; must return None (fail-open)
        reason = engine.pretool({"tool_name": "Bash"})
        assert reason is None

    def test_fire_http_exception_swallowed(self, monkeypatch: Any) -> None:
        """HttpAction 失败（返回 None）→ fire 不冒泡。"""
        from wentian.hooks import actions
        from wentian.hooks.engine import HookEngine

        def _fail(*a: Any, **kw: Any) -> None:
            raise OSError("network error")

        monkeypatch.setattr(actions, "call_http", _fail)

        rule = _make_rule(
            HookEvent.SESSION_START,
            HttpAction(url="http://localhost:9999/", timeout=1),
        )
        engine = HookEngine([rule])
        # Must not raise
        engine.fire(HookEvent.SESSION_START, {})

    def test_fire_subagent_exception_swallowed(self, monkeypatch: Any) -> None:
        """SubAgentAction 失败 → fire 不冒泡（真测引擎软化路径）。"""
        from wentian.hooks import actions
        from wentian.hooks.engine import HookEngine

        def _raise(*a: Any, **kw: Any) -> None:
            raise RuntimeError("subagent boom")

        # 引擎现在调用 run_subagent_action（带 manager=），monkeypatch 必须指向它，
        # 否则 setattr 不生效、本测沦为假阳性（review fix）。
        monkeypatch.setattr(actions, "run_subagent_action", _raise)

        rule = _make_rule(
            HookEvent.SESSION_START,
            SubAgentAction(prompt="do something"),
        )
        engine = HookEngine([rule])
        # 若移除引擎 _execute_action_soft 的 try/except，此处将抛 RuntimeError。
        engine.fire(HookEvent.SESSION_START, {})


# ---------------------------------------------------------------------------
# 7. close() — 短 join 后台线程
# ---------------------------------------------------------------------------


class TestClose:
    def test_close_joins_background_threads_without_hanging(self) -> None:
        """close() 短 join 后台线程后返回（不卡）。"""
        from wentian.hooks.engine import HookEngine

        rule = _make_rule(
            HookEvent.SESSION_START,
            ShellAction(command="sleep 0.1", timeout=5),
            background=True,
        )
        engine = HookEngine([rule])
        engine.fire(HookEvent.SESSION_START, {})

        t0 = time.monotonic()
        engine.close()
        elapsed = time.monotonic() - t0

        # close should not hang beyond a reasonable bound
        assert elapsed < 5.0, f"close() took {elapsed:.2f}s — too long"

    def test_close_idempotent(self) -> None:
        """close() 可多次调用不抛。"""
        from wentian.hooks.engine import HookEngine

        engine = HookEngine([])
        engine.close()
        engine.close()

    def test_close_no_threads_is_noop(self) -> None:
        """无后台线程时 close() 是空操作，不抛。"""
        from wentian.hooks.engine import HookEngine

        rule = _make_rule(
            HookEvent.SESSION_START,
            PromptAction(text="no background"),
            background=False,
        )
        engine = HookEngine([rule])
        engine.fire(HookEvent.SESSION_START, {})
        engine.close()  # Should be instant


# ---------------------------------------------------------------------------
# 8. 规则声明顺序保持
# ---------------------------------------------------------------------------


class TestDeclarationOrder:
    def test_fire_respects_declaration_order(self, tmp_path: Any) -> None:
        """多条规则按声明顺序执行（第一条写 first，第二条写 second）。"""
        from wentian.hooks.engine import HookEngine

        order_file = tmp_path / "order.txt"
        rule1 = _make_rule(
            HookEvent.SESSION_START,
            ShellAction(command=f"echo first >> {order_file}", timeout=5),
        )
        rule2 = _make_rule(
            HookEvent.SESSION_START,
            ShellAction(command=f"echo second >> {order_file}", timeout=5),
        )
        engine = HookEngine([rule1, rule2])
        engine.fire(HookEvent.SESSION_START, {})

        lines = order_file.read_text().strip().splitlines()
        assert lines == ["first", "second"], f"Wrong order: {lines}"

    def test_by_event_index_correct(self) -> None:
        """_by_event 按事件分组正确。"""
        from wentian.hooks.engine import HookEngine

        r1 = _make_rule(HookEvent.SESSION_START, PromptAction(text="a"))
        r2 = _make_rule(HookEvent.SESSION_END, PromptAction(text="b"))
        r3 = _make_rule(HookEvent.SESSION_START, PromptAction(text="c"))
        engine = HookEngine([r1, r2, r3])

        assert len(engine._by_event.get(HookEvent.SESSION_START, [])) == 2
        assert len(engine._by_event.get(HookEvent.SESSION_END, [])) == 1


# ---------------------------------------------------------------------------
# 9. drain_injections 边界
# ---------------------------------------------------------------------------


class TestDrainInjections:
    def test_drain_returns_joined_and_clears(self) -> None:
        """多条 prompt 用 \\n 连接；drain 后 _pending 清空。"""
        from wentian.hooks.engine import HookEngine

        r1 = _make_rule(HookEvent.SESSION_START, PromptAction(text="alpha"))
        r2 = _make_rule(HookEvent.SESSION_START, PromptAction(text="beta"))
        engine = HookEngine([r1, r2])
        engine.fire(HookEvent.SESSION_START, {})

        result = engine.drain_injections()
        assert result == "alpha\nbeta"
        assert engine.drain_injections() == ""

    def test_drain_single_item_no_newline(self) -> None:
        """单条 prompt → 无多余换行。"""
        from wentian.hooks.engine import HookEngine

        rule = _make_rule(HookEvent.SESSION_START, PromptAction(text="solo"))
        engine = HookEngine([rule])
        engine.fire(HookEvent.SESSION_START, {})

        result = engine.drain_injections()
        assert result == "solo"
