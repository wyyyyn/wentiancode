"""v0.10 · C89 · F76/F72（任务 T112）— 内置斜杠命令注册表。
v0.13 · C116 · F101（任务 T144）— 新增 /agents 命令（共 13 条）。

提供 ``build_builtin_registry()`` 工厂函数，按顺序注册全部 13 条内置命令
及其 handler；提供 ``render_help`` / ``parse_mode`` 两个辅助函数。

分层铁律：纯叶子模块，只 import 同包 spec/registry + wentian.permissions.decision.Mode。
绝不 import rich / prompt_toolkit / 任何 provider / repl / agent。
"""

from __future__ import annotations

from wentian.commands.registry import CommandRegistry
from wentian.commands.spec import CommandSpec, CommandType
from wentian.permissions.decision import Mode

__all__ = ["build_builtin_registry", "render_help", "parse_mode"]

# 各模式关键词映射（小写）→ Mode
_MODE_MAP: dict[str, Mode] = {
    "default": Mode.DEFAULT,
    "acceptedits": Mode.ACCEPT_EDITS,
    "accept": Mode.ACCEPT_EDITS,
    "plan": Mode.PLAN,
    "bypasspermissions": Mode.BYPASS,
    "bypass": Mode.BYPASS,
}


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def render_help(specs: list[CommandSpec]) -> str:
    """产出纯文本多行帮助（每行命令名 + summary，简单对齐）。

    不产 Rich 对象——Rich 渲染留 REPL 侧。
    """
    if not specs:
        return "（无可用命令）"
    max_name_len = max(len(s.name) for s in specs)
    lines: list[str] = []
    for spec in specs:
        aliases_hint = ""
        if spec.aliases:
            aliases_hint = f" ({', '.join(spec.aliases)})"
        lines.append(f"  /{spec.name:<{max_name_len}}{aliases_hint}  {spec.summary}")
    return "\n".join(lines)


def parse_mode(arg: str) -> Mode | None:
    """将字符串大小写不敏感地映射到 Mode；无法解析返回 None。

    支持：
    - ``"plan"``
    - ``"default"``
    - ``"acceptEdits"`` / ``"accept"``
    - ``"bypassPermissions"`` / ``"bypass"``
    """
    return _MODE_MAP.get(arg.strip().lower())


# ---------------------------------------------------------------------------
# Handler 自由函数（仅调 ctx.*，不碰具体实现类）
# ---------------------------------------------------------------------------


def _h_help(ctx, args: str) -> bool | None:  # noqa: ANN001
    """显示可见命令帮助列表。"""
    ctx.print(render_help(ctx.visible_commands()))
    return None


def _h_status(ctx, args: str) -> bool | None:  # noqa: ANN001
    """显示当前状态栏与上轮 token 用量。"""
    status = ctx.status_line()
    usage = ctx.token_usage()
    lines = [status]
    if usage is not None:
        lines.append(f"上轮 token 用量：{usage}")
    ctx.print("\n".join(lines))
    return None


def _h_memory(ctx, args: str) -> bool | None:  # noqa: ANN001
    """显示长期记忆摘要。"""
    ctx.print(ctx.memory_summary())
    return None


def _h_compact(ctx, args: str) -> bool | None:  # noqa: ANN001
    """触发上下文压缩。"""
    ctx.print(f"[dim]{ctx.compact_now()}[/dim]")
    return None


def _h_clear(ctx, args: str) -> bool | None:  # noqa: ANN001
    """清空当前对话上下文。"""
    ctx.clear_context()
    ctx.print("已清空当前对话上下文。")
    return None


def _h_plan(ctx, args: str) -> bool | None:  # noqa: ANN001
    """切换到 plan 模式；有参数则同时发送该消息。"""
    ctx.set_mode(Mode.PLAN)
    ctx.print("已进入计划模式（只读工具）。用 /do 退出")
    if args:
        ctx.send_user_message(args)
    return None


def _h_do(ctx, args: str) -> bool | None:  # noqa: ANN001
    """切换回 default 模式；有参数则同时发送该消息。"""
    ctx.set_mode(Mode.DEFAULT)
    ctx.print("已退出计划模式，恢复全部工具")
    if args:
        ctx.send_user_message(args)
    return None


def _h_permission(ctx, args: str) -> bool | None:  # noqa: ANN001
    """查询 / 切换权限模式。

    无参：打印当前模式；有参：尝试解析并切换，无法解析则打印可选档位提示。
    """
    stripped = args.strip()
    if not stripped:
        ctx.print(f"当前权限模式：{ctx.get_mode().value}")
        return None
    mode = parse_mode(stripped)
    if mode is None:
        options = ", ".join(m.value for m in Mode)
        ctx.print(f"无法识别模式 {stripped!r}。可选档位：{options}")
        return None
    ctx.set_mode(mode)
    ctx.print(f"已切换权限模式：{mode.name}")
    return None


def _h_session(ctx, args: str) -> bool | None:  # noqa: ANN001
    """会话管理子命令路由：new / list [--all] / resume <id>。"""
    parts = args.strip().split()
    if not parts:
        ctx.print("用法：/session new | list [--all] | resume <id>")
        return None
    sub = parts[0].lower()
    if sub == "new":
        ctx.new_session()
    elif sub == "list":
        all_projects = "--all" in args
        ctx.list_sessions(all_projects=all_projects)
    elif sub == "resume":
        if len(parts) < 2:
            ctx.print("用法：/session resume <id>")
        else:
            ctx.resume_session(parts[1])
    else:
        ctx.print("用法：/session new | list [--all] | resume <id>")
    return None


def _h_provider(ctx, args: str) -> bool | None:  # noqa: ANN001
    """切换 AI 提供商；无参则打印用法提示。"""
    name = args.strip()
    if not name:
        ctx.print("用法：/provider <provider-name>")
        return None
    ctx.switch_provider(name)
    return None


def _h_review(ctx, args: str) -> bool | None:  # noqa: ANN001
    """向 AI 发送代码审查请求。"""
    target = args.strip() or "全部未提交的改动"
    ctx.send_user_message(f"请审查未提交的改动：{target}…")
    return None


def _h_exit(ctx, args: str) -> bool | None:  # noqa: ANN001
    """退出 REPL。"""
    return True


# ---------------------------------------------------------------------------
# /agents 辅助格式化函数（v0.13 · C116 · F101）
# ---------------------------------------------------------------------------

import time as _time  # noqa: E402 — stdlib only，分层铁律允许


def _fmt_elapsed(created_at: float) -> str:
    """把 created_at 时间戳转换为相对时间字符串（如「3 分钟前」）。"""
    delta = _time.time() - created_at
    if delta < 60:
        return f"{int(delta)} 秒前"
    if delta < 3600:
        return f"{int(delta // 60)} 分钟前"
    if delta < 86400:
        return f"{int(delta // 3600)} 小时前"
    return f"{int(delta // 86400)} 天前"


def _fmt_usage(usage: dict) -> str:
    """将 usage 字典格式化为可读字符串；空字典返回「-」。"""
    if not usage:
        return "-"
    parts = []
    if "input_tokens" in usage:
        parts.append(f"in={usage['input_tokens']}")
    if "output_tokens" in usage:
        parts.append(f"out={usage['output_tokens']}")
    if not parts:
        # 通用回退
        parts = [f"{k}={v}" for k, v in usage.items()]
    return " ".join(parts)


def _format_task(task) -> str:  # noqa: ANN001
    """将单条后台任务格式化为单行列表行（id / label / status / 相对时间 / 用量）。

    v0.13 · C116 · F101
    """
    status_str = (
        task.status.value if hasattr(task.status, "value") else str(task.status)
    )
    elapsed = _fmt_elapsed(task.created_at)
    usage_str = _fmt_usage(task.usage) if task.status.value != "running" else "-"
    return f"  {task.id}  {task.label}  [{status_str}]  {elapsed}  token:{usage_str}"


def _format_task_detail(task) -> str:  # noqa: ANN001
    """将单条后台任务格式化为详情输出（含 result 全文 + 用量）。

    v0.13 · C116 · F101
    """
    status_str = (
        task.status.value if hasattr(task.status, "value") else str(task.status)
    )
    lines = [
        f"任务 id：{task.id}",
        f"名称：{task.label}",
        f"状态：{status_str}",
        f"创建：{_fmt_elapsed(task.created_at)}",
        f"用量：{_fmt_usage(task.usage)}",
        "---",
        f"结果：\n{task.result}",
    ]
    return "\n".join(lines)


def _h_agents(ctx, args: str) -> bool | None:  # noqa: ANN001
    """列出后台 Agent 任务（无参），或查询指定任务详情（有参 id）。

    v0.13 · C116 · F101/AC126 · N50
    """
    mgr = getattr(ctx, "agents_manager", lambda: None)()
    if mgr is None:
        ctx.print("agents 未启用")
        return None

    arg = args.strip()
    if not arg:
        # 无参：列出所有任务
        tasks = mgr.list()
        if not tasks:
            ctx.print("（无后台任务）")
            return None
        lines = ["后台 Agent 任务列表："]
        for t in tasks:
            lines.append(_format_task(t))
        ctx.print("\n".join(lines))
        return None

    # 有参：按 id 查询详情
    task = mgr.get(arg)
    if task is None:
        ctx.print(f"未找到任务：{arg}")
        return None

    from wentian.agents.spec import TaskStatus  # noqa: PLC0415 — lazy import，分层允许

    if task.status == TaskStatus.RUNNING:
        ctx.print(f"任务 {arg} 运行中，result 尚不可用")
        return None

    ctx.print(_format_task_detail(task))
    return None


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------


def build_builtin_registry() -> CommandRegistry:
    """按顺序注册全部 13 条内置命令，返回 :class:`CommandRegistry`。

    v0.13 · C116 · F101：新增第 13 条 /agents（LOCAL 类）。
    """
    reg = CommandRegistry()

    reg.register(
        CommandSpec(
            name="help",
            summary="显示可用命令列表",
            usage="/help",
            type=CommandType.LOCAL,
            handler=_h_help,
            aliases=("?", "h"),
        )
    )
    reg.register(
        CommandSpec(
            name="status",
            summary="显示当前状态栏与上轮 token 用量",
            usage="/status",
            type=CommandType.LOCAL,
            handler=_h_status,
            aliases=("st",),
        )
    )
    reg.register(
        CommandSpec(
            name="memory",
            summary="显示长期记忆摘要",
            usage="/memory",
            type=CommandType.LOCAL,
            handler=_h_memory,
            aliases=("mem",),
        )
    )
    reg.register(
        CommandSpec(
            name="compact",
            summary="触发上下文压缩（节省 token）",
            usage="/compact",
            type=CommandType.LOCAL,
            handler=_h_compact,
        )
    )
    reg.register(
        CommandSpec(
            name="clear",
            summary="清空当前对话上下文（保留会话 id）",
            usage="/clear",
            type=CommandType.UI_STATE,
            handler=_h_clear,
            aliases=("cls",),
        )
    )
    reg.register(
        CommandSpec(
            name="plan",
            summary="切换到 plan 模式（可选：附带要规划的内容）",
            usage="/plan [内容]",
            type=CommandType.UI_STATE,
            handler=_h_plan,
            arg_hint="[内容]",
        )
    )
    reg.register(
        CommandSpec(
            name="do",
            summary="切换回 default 模式（可选：附带要执行的内容）",
            usage="/do [内容]",
            type=CommandType.UI_STATE,
            handler=_h_do,
            arg_hint="[内容]",
        )
    )
    reg.register(
        CommandSpec(
            name="permission",
            summary="查询 / 切换权限模式（default/acceptEdits/plan/bypassPermissions）",
            usage="/permission [模式]",
            type=CommandType.UI_STATE,
            handler=_h_permission,
            aliases=("perm",),
            arg_hint="[模式]",
        )
    )
    reg.register(
        CommandSpec(
            name="session",
            summary="会话管理：new / list [--all] / resume <id>",
            usage="/session new | list [--all] | resume <id>",
            type=CommandType.UI_STATE,
            handler=_h_session,
            aliases=("sess",),
            arg_hint="new|list|resume <id>",
        )
    )
    reg.register(
        CommandSpec(
            name="provider",
            summary="切换 AI 提供商",
            usage="/provider <provider-name>",
            type=CommandType.UI_STATE,
            handler=_h_provider,
            arg_hint="<provider-name>",
        )
    )
    reg.register(
        CommandSpec(
            name="review",
            summary="请 AI 审查未提交的改动",
            usage="/review [文件或范围]",
            type=CommandType.PROMPT,
            handler=_h_review,
            arg_hint="[文件或范围]",
        )
    )
    reg.register(
        CommandSpec(
            name="exit",
            summary="退出 WentianCode",
            usage="/exit",
            type=CommandType.LOCAL,
            handler=_h_exit,
            aliases=("quit", "q"),
        )
    )
    reg.register(
        CommandSpec(
            name="agents",
            summary="列出后台 Agent 任务；/agents <id> 查看结果全文+用量",
            usage="/agents [id]",
            type=CommandType.LOCAL,
            handler=_h_agents,
            arg_hint="[id]",
        )
    )

    return reg
