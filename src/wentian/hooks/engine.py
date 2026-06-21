"""v0.12 · C98 · F78/F79/F82/F83（任务 T122）— Hook 引擎。

按事件索引规则、统一触发与拦截、执行控制、失败软化、注入累积、hook 日志。

对外接口：
  HookEngine(rules, *, logger=None)
    .fire(event, context)           — 非拦截事件入口
    .pretool(context) -> str|None   — 拦截入口（同步）
    .drain_injections() -> str      — 取出并清空 _pending 缓冲
    .close()                        — 短 join 后台线程

分层铁律（N41）：
  仅 import stdlib（logging / threading）+ 同包 spec / conditions / actions。
  零 agent / repl / providers / tools / rich / prompt_toolkit 依赖。
  事件上下文为纯 dict，由装配层构造后喂入。
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Optional

import wentian.hooks.actions as _actions
from wentian.hooks.conditions import evaluate
from wentian.hooks.spec import (
    HookEvent,
    HookRule,
    HttpAction,
    PromptAction,
    ShellAction,
    SubAgentAction,
)

__all__ = ["HookEngine"]

_DEFAULT_CLOSE_TIMEOUT = 1.0  # seconds per thread join on close()


class HookEngine:
    """统一 hook 触发与拦截引擎。

    构造：``HookEngine(rules, *, logger=None)``

    - ``_by_event``：规则按 HookEvent 分组，保持声明顺序。
    - ``_pending``：PromptAction 产出的注入文本缓冲（drain_injections 取走）。
    - ``_fired_once``：``once=True`` 规则已触发的 ``id(rule)`` 集合。
    - ``_threads``：后台 daemon 线程跟踪列表，供 close() join。
    """

    def __init__(
        self,
        rules: list[HookRule],
        *,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._rules = rules
        self._logger = logger or logging.getLogger("wentian.hooks")

        # 按事件分组，保持声明顺序
        self._by_event: dict[HookEvent, list[HookRule]] = defaultdict(list)
        for rule in rules:
            self._by_event[rule.event].append(rule)

        self._pending: list[str] = []
        self._fired_once: set[int] = set()
        self._threads: list[threading.Thread] = []
        self._threads_lock = threading.Lock()

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def fire(self, event: HookEvent, context: dict) -> None:  # type: ignore[type-arg]
        """非拦截事件入口。

        对本事件的每条规则：
          - 条件不命中 → 跳过
          - once=True 且已触发 → 跳过
          - background=True → 投 daemon 线程 fire-and-forget
          - 否则同步执行
        全程 try/except → 日志，绝不向调用方抛。
        """
        for rule in self._by_event.get(event, []):
            if not self._should_fire(rule, context):
                continue
            if rule.background:
                self._fire_in_background(rule, context)
            else:
                self._execute_action_soft(rule, context)

    def pretool(self, context: dict) -> Optional[str]:  # type: ignore[type-arg]
        """拦截入口（同步）。

        按声明顺序遍历 PreToolUse 规则：
          - 条件不命中 → 跳过
          - ShellAction：run_shell
            - exit_code == 2 → 返回拒绝原因（stderr 优先，空则 stdout），短路
            - exit_code == 0 → 继续（不拦截）
            - 其他（timed_out / 非 0 非 2 / 异常 / None）→ fail-open（记日志，继续）
          - 非 ShellAction → 执行副作用 / 注入（prompt 进 _pending），不拦截
        全程 try/except → fail-open；无规则拦截 → None。
        """
        for rule in self._by_event.get(HookEvent.PRE_TOOL_USE, []):
            if not evaluate(rule.condition, context):
                continue
            try:
                result = self._dispatch_action_for_pretool(rule, context)
                if result is not None:
                    # result is a deny reason string
                    return result
            except Exception as exc:  # noqa: BLE001
                self._logger.warning(
                    "pretool: action raised exception (fail-open): %s", exc
                )
        return None

    def drain_injections(self) -> str:
        """取出并清空 _pending 缓冲，供 request_decorator 注入 <system-reminder>。

        返回 ``"\\n".join(_pending)``；若缓冲为空返回 ``""``。
        """
        if not self._pending:
            return ""
        result = "\n".join(self._pending)
        self._pending.clear()
        return result

    def close(self) -> None:
        """短 join 后台线程（每条最多等 _DEFAULT_CLOSE_TIMEOUT 秒），不卡退出。"""
        with self._threads_lock:
            threads = list(self._threads)
        for t in threads:
            try:
                t.join(timeout=_DEFAULT_CLOSE_TIMEOUT)
            except Exception as exc:  # noqa: BLE001
                self._logger.debug("close: thread join error (ignored): %s", exc)

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _should_fire(self, rule: HookRule, context: dict) -> bool:  # type: ignore[type-arg]
        """检查条件命中 + once 未触发。"""
        if not evaluate(rule.condition, context):
            return False
        if rule.once and id(rule) in self._fired_once:
            return False
        return True

    def _mark_once(self, rule: HookRule) -> None:
        """若 rule.once=True，标记已触发。"""
        if rule.once:
            self._fired_once.add(id(rule))

    def _execute_action_soft(
        self,
        rule: HookRule,
        context: dict,  # type: ignore[type-arg]
    ) -> None:
        """同步执行 rule.action，全程软化（try/except → 日志，不抛）。"""
        try:
            self._dispatch_action(rule.action, context)
            self._mark_once(rule)
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("fire: action raised exception (softened): %s", exc)

    def _fire_in_background(
        self,
        rule: HookRule,
        context: dict,  # type: ignore[type-arg]
    ) -> None:
        """将动作投入 daemon 线程（fire-and-forget），追踪线程供 close() join。"""

        def _run() -> None:
            try:
                self._dispatch_action(rule.action, context)
                self._mark_once(rule)
            except Exception as exc:  # noqa: BLE001
                self._logger.warning(
                    "fire[background]: action raised exception (softened): %s", exc
                )

        t = threading.Thread(target=_run, daemon=True)
        with self._threads_lock:
            self._threads.append(t)
        t.start()

    def _dispatch_action(
        self,
        action: object,
        context: dict,  # type: ignore[type-arg]
    ) -> None:
        """按动作类型分派，处理 PromptAction 产出（追加 _pending）。"""
        if isinstance(action, ShellAction):
            _actions.run_shell(action, context)
        elif isinstance(action, PromptAction):
            text = _actions.inject_prompt(action, context)
            self._pending.append(text)
        elif isinstance(action, HttpAction):
            _actions.call_http(action, context)
        elif isinstance(action, SubAgentAction):
            _actions.run_subagent(action, context)
        else:
            self._logger.warning("dispatch: unknown action type %s", type(action))

    def _dispatch_action_for_pretool(
        self,
        rule: HookRule,
        context: dict,  # type: ignore[type-arg]
    ) -> Optional[str]:
        """在 pretool 上下文执行 rule.action。

        - ShellAction：返回拒绝原因字符串（exit 2）或 None（放行 / fail-open）。
        - 其他动作：执行副作用（prompt → _pending），返回 None（不拦截）。
        """
        action = rule.action
        if isinstance(action, ShellAction):
            return self._run_shell_for_pretool(action, context)  # type: ignore[arg-type]
        else:
            # Non-shell actions: side-effect only, cannot block
            self._dispatch_action(action, context)
            return None

    def _run_shell_for_pretool(
        self,
        action: ShellAction,
        context: dict,  # type: ignore[type-arg]
    ) -> Optional[str]:
        """执行 ShellAction 并按 exit code 决定是否拦截。

        - exit_code == 2 → 返回拒绝原因（stderr 优先，空则 stdout）
        - exit_code == 0 → None（不拦截，继续）
        - timed_out / None（run_shell 失败）/ 其他 exit_code → fail-open（None）+ 记日志
        """
        result = _actions.run_shell(action, context)

        if result is None:
            self._logger.warning(
                "pretool: run_shell returned None (fail-open): command=%r",
                action.command,
            )
            return None

        if result.timed_out:
            self._logger.warning(
                "pretool: shell timed out (fail-open): command=%r",
                action.command,
            )
            return None

        if result.exit_code == 2:
            # Deny: stderr preferred; fall back to stdout; strip whitespace
            reason = (result.stderr or result.stdout).strip()
            return reason if reason else "blocked by hook"

        if result.exit_code == 0:
            return None  # pass-through

        # Any other exit code (e.g. 1, 127) → fail-open
        self._logger.warning(
            "pretool: unexpected exit_code=%d (fail-open): command=%r",
            result.exit_code,
            action.command,
        )
        return None
