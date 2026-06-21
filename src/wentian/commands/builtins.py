"""v0.10 · C89 · F76/F72（任务 T112）— 内置斜杠命令注册表。

提供 ``build_builtin_registry()`` 工厂函数，按顺序注册全部 12 条内置命令
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
# 工厂函数
# ---------------------------------------------------------------------------


def build_builtin_registry() -> CommandRegistry:
    """按顺序注册全部 12 条内置命令，返回 :class:`CommandRegistry`。"""
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

    return reg
