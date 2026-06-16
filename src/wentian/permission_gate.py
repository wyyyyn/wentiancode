"""v0.6 · C37 · F48（任务 T77）

Permission-gate constructor — the **assembly layer** that turns the pure
permission pipeline into the duck-typed ``permission_gate(call) -> ToolOutcome
| None`` the :class:`~wentian.agent.loop.AgentLoop` consumes (C36 contract:
``None`` = allow, non-``None`` = a fully-formed refusal outcome).

This module lives **outside** the ``permissions/`` pure package on purpose: it
imports across layers (permissions + tools + ui) so the pure package stays
clean (zero tools/ui/rich imports). The AgentLoop never imports this — it only
receives the closure.

Gate logic (C37):

1. ``tool = registry.get(call.name)``; from the tool's category / friendly_name
   / command_arg / path_args, extract command & paths from ``call.arguments``.
2. Unregistered tool / unparseable arguments → **safe default** (treat as
   command_exec, never silently allow; AC55/N16).
3. ``decision = pipeline.decide(friendly, category, mode=get_mode(),
   command, paths)``.
4. ``ALLOW`` → return ``None`` (pass through to executor).
5. ``DENY`` (blacklist / sandbox / rule) → return a formed ``ToolOutcome``
   (``is_error=True``, ``denied=False``).
6. ``ASK`` → ``choice = await ask(call, decision)``:
   - ALLOW_ONCE → ``None``;
   - ALLOW_ALWAYS → persist an exact rule (``on_allow_always``) + ``None``;
   - DENY → formed ``ToolOutcome`` (``is_error=True``, ``denied=True``).

The gate constructs the refusal outcome itself (it knows the source) so the
loop stays free of any permission vocabulary.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from wentian.permissions.decision import Category, Decision, Verdict
from wentian.tools.executor import ToolOutcome
from wentian.ui.confirm import Choice

__all__ = ["build_permission_gate"]


def _extract(tool: Any, arguments: dict | None) -> tuple[str | None, tuple[str, ...]]:
    """从 arguments 抽取 (command, paths)，按 tool 的 command_arg / path_args。

    tool 为 None（未注册）或 arguments 不可解析 → (None, ())，由调用方按命令执行类
    安全默认处理（绝不静默放行，AC55）。
    """
    if tool is None or not isinstance(arguments, dict):
        return None, ()

    command: str | None = None
    command_arg = getattr(tool, "command_arg", None)
    if command_arg:
        value = arguments.get(command_arg)
        if isinstance(value, str):
            command = value

    paths: list[str] = []
    for arg_name in getattr(tool, "path_args", ()) or ():
        value = arguments.get(arg_name)
        if isinstance(value, str):
            paths.append(value)
    return command, tuple(paths)


def build_permission_gate(
    *,
    pipeline: Any,
    registry: Any,
    ask: Callable[[Any, Decision], Awaitable[Choice]],
    get_mode: Callable[[], Any],
    on_allow_always: Callable[[str, str, bool], None] | None = None,
) -> Callable[[Any], Awaitable[ToolOutcome | None]]:
    """构造 async ``gate(call) -> ToolOutcome | None``（C37）。

    Parameters
    ----------
    pipeline:
        :class:`~wentian.permissions.pipeline.PermissionPipeline` (duck-typed:
        only ``decide(**kwargs) -> Decision`` is used).
    registry:
        Tool registry (duck-typed: ``get(name) -> tool | None``).
    ask:
        ``async ask(call, decision) -> Choice`` — the human-in-the-loop prompt.
        May raise :class:`~wentian.ui.confirm.Cancelled`; propagated to caller.
    get_mode:
        Callable returning the current :class:`~wentian.permissions.decision.Mode`.
    on_allow_always:
        Optional ``(friendly, target, is_path) -> None`` hook invoked when the
        user picks ALLOW_ALWAYS, to persist the exact rule + add it to the live
        in-memory ruleset. ``target`` is the command string (is_path=False) or
        the project-relative path (is_path=True).
    """

    async def gate(call: Any) -> ToolOutcome | None:
        tool = registry.get(call.name)
        command, paths = _extract(tool, getattr(call, "arguments", None))

        if tool is not None:
            category = getattr(tool, "category", Category.COMMAND_EXEC)
            friendly = getattr(tool, "friendly_name", call.name)
        else:
            # 安全默认（AC55/N16）：未注册工具按命令执行类（最严）处理。
            category = Category.COMMAND_EXEC
            friendly = call.name

        decision: Decision = pipeline.decide(
            friendly=friendly,
            category=category,
            mode=get_mode(),
            command=command,
            paths=paths,
        )

        if decision.verdict is Verdict.ALLOW:
            return None

        if decision.verdict is Verdict.DENY:
            return _refuse(call, decision.reason, denied=False)

        # ASK → 人在回路。
        choice = await ask(call, decision)
        if choice is Choice.ALLOW_ONCE:
            return None
        if choice is Choice.ALLOW_ALWAYS:
            if on_allow_always is not None:
                is_path = category in (Category.READ_ONLY, Category.FILE_WRITE)
                target = (paths[0] if paths else "") if is_path else (command or "")
                on_allow_always(friendly, target, is_path)
            return None
        # Choice.DENY —— 人在回路拒绝。
        reason = decision.reason or "用户拒绝了本次工具调用。"
        return _refuse(call, reason, denied=True)

    return gate


def _refuse(call: Any, reason: str, *, denied: bool) -> ToolOutcome:
    """构造成形的拒绝结果（鸭子兼容 ToolOutcome；C36 回灌契约）。"""
    return ToolOutcome(
        call_id=call.id,
        name=call.name,
        content=reason or "工具调用被拒绝。",
        is_error=True,
        denied=denied,
    )
