"""v0.10 · C89 · F76/F72（任务 T112）— 内置命令注册表与 handler 单元测试。

TDD 红-绿-重构：先确认因功能缺失而失败，再实现转绿。
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# 假 ctx（记录型）——实现 CommandContext 协议
# ---------------------------------------------------------------------------


class FakeCtx:
    """记录所有调用的假上下文，供测试断言副作用。"""

    def __init__(self) -> None:
        from wentian.permissions.decision import Mode

        self.printed: list[Any] = []
        self.sent: list[str] = []
        self._mode: Mode = Mode.DEFAULT
        self.calls: list[tuple[str, Any]] = []  # (方法名, 参数)

    # --- CommandContext 协议方法 ---

    def print(self, renderable: object) -> None:
        self.printed.append(renderable)

    def send_user_message(self, text: str) -> None:
        self.sent.append(text)
        self.calls.append(("send_user_message", text))

    def get_mode(self):
        return self._mode

    def set_mode(self, mode) -> None:
        self._mode = mode
        self.calls.append(("set_mode", mode))

    def token_usage(self) -> object | None:
        self.calls.append(("token_usage", None))
        return {"input": 100, "output": 50}

    def status_line(self) -> str:
        self.calls.append(("status_line", None))
        return "[状态栏]"

    def memory_summary(self) -> str:
        self.calls.append(("memory_summary", None))
        return "[记忆摘要]"

    def visible_commands(self):
        from wentian.commands.builtins import build_builtin_registry

        return build_builtin_registry().visible()

    def clear_context(self) -> None:
        self.calls.append(("clear_context", None))

    def new_session(self) -> None:
        self.calls.append(("new_session", None))

    def list_sessions(self, *, all_projects: bool) -> None:
        self.calls.append(("list_sessions", all_projects))

    def resume_session(self, sid: str) -> None:
        self.calls.append(("resume_session", sid))

    def switch_provider(self, name: str) -> None:
        self.calls.append(("switch_provider", name))

    def compact_now(self) -> str:
        self.calls.append(("compact_now", None))
        return "[压缩完成]"


# ---------------------------------------------------------------------------
# 辅助：获取注册表与命令
# ---------------------------------------------------------------------------


def _reg():
    from wentian.commands.builtins import build_builtin_registry

    return build_builtin_registry()


def _handler(name: str):
    """通过规范名取 handler。"""
    spec = _reg().lookup(name)
    assert spec is not None, f"找不到命令 {name!r}"
    return spec.handler


# ---------------------------------------------------------------------------
# 1. 注册表结构检查
# ---------------------------------------------------------------------------


def test_registry_has_12_commands():
    """build_builtin_registry().all() == 12 条。"""
    assert len(_reg().all()) == 12


def test_visible_excludes_nothing_special():
    """可见命令：全部 12 条（无 hidden 命令）。"""
    # 根据设计，12 条均可见（provider/exit 均可见）
    assert len(_reg().visible()) == 12


def test_command_types_local():
    """LOCAL 命令：help / status / memory / compact / exit。"""
    from wentian.commands.spec import CommandType

    reg = _reg()
    for name in ("help", "status", "memory", "compact", "exit"):
        spec = reg.lookup(name)
        assert spec is not None, f"{name} 未注册"
        assert spec.type == CommandType.LOCAL, f"{name} 应为 LOCAL，实为 {spec.type}"


def test_command_types_ui_state():
    """UI_STATE 命令：clear / plan / do / permission / session / provider。"""
    from wentian.commands.spec import CommandType

    reg = _reg()
    for name in ("clear", "plan", "do", "permission", "session", "provider"):
        spec = reg.lookup(name)
        assert spec is not None, f"{name} 未注册"
        assert spec.type == CommandType.UI_STATE, (
            f"{name} 应为 UI_STATE，实为 {spec.type}"
        )


def test_command_types_prompt():
    """PROMPT 命令：review。"""
    from wentian.commands.spec import CommandType

    spec = _reg().lookup("review")
    assert spec is not None
    assert spec.type == CommandType.PROMPT


def test_aliases_help():
    """? 和 h 都映射到 help。"""
    reg = _reg()
    help_spec = reg.lookup("help")
    assert reg.lookup("?") is help_spec
    assert reg.lookup("h") is help_spec


def test_aliases_clear():
    """cls → clear。"""
    reg = _reg()
    assert reg.lookup("cls") is reg.lookup("clear")


def test_aliases_memory():
    """mem → memory。"""
    reg = _reg()
    assert reg.lookup("mem") is reg.lookup("memory")


def test_aliases_permission():
    """perm → permission。"""
    reg = _reg()
    assert reg.lookup("perm") is reg.lookup("permission")


def test_aliases_session():
    """sess → session。"""
    reg = _reg()
    assert reg.lookup("sess") is reg.lookup("session")


def test_aliases_exit():
    """quit 和 q → exit。"""
    reg = _reg()
    exit_spec = reg.lookup("exit")
    assert reg.lookup("quit") is exit_spec
    assert reg.lookup("q") is exit_spec


def test_aliases_status():
    """st → status。"""
    reg = _reg()
    assert reg.lookup("st") is reg.lookup("status")


# ---------------------------------------------------------------------------
# 2. LOCAL 命令 handler
# ---------------------------------------------------------------------------


def test_help_calls_print_with_visible_commands():
    """/help handler 调用 ctx.print，参数含 visible_commands 的内容。"""
    ctx = FakeCtx()
    _handler("help")(ctx, "")
    assert len(ctx.printed) == 1
    # 打印内容是字符串（纯文本帮助）
    output = ctx.printed[0]
    assert isinstance(output, str)
    # 应含 help / exit 等命令名
    assert "help" in output
    assert "exit" in output


def test_status_calls_status_line_and_token_usage():
    """/status handler 调用 ctx.status_line() 和 ctx.token_usage()。"""
    ctx = FakeCtx()
    _handler("status")(ctx, "")
    call_names = [c[0] for c in ctx.calls]
    assert "status_line" in call_names
    assert "token_usage" in call_names
    assert len(ctx.printed) >= 1


def test_memory_calls_memory_summary():
    """/memory handler 打印 ctx.memory_summary()。"""
    ctx = FakeCtx()
    _handler("memory")(ctx, "")
    assert any("memory_summary" in c[0] for c in ctx.calls)
    assert "[记忆摘要]" in ctx.printed[0]


def test_compact_calls_compact_now():
    """/compact handler 调用并打印 ctx.compact_now()。"""
    ctx = FakeCtx()
    _handler("compact")(ctx, "")
    assert any("compact_now" in c[0] for c in ctx.calls)
    assert "[压缩完成]" in ctx.printed[0]


def test_exit_returns_true():
    """/exit handler 返回 True（触发 REPL 退出）。"""
    ctx = FakeCtx()
    result = _handler("exit")(ctx, "")
    assert result is True


def test_non_exit_handlers_return_none():
    """help / status / memory / compact 返回 None。"""
    for name in ("help", "status", "memory", "compact"):
        result = _handler(name)(FakeCtx(), "")
        assert result is None, f"{name} 应返回 None，实返 {result!r}"


# ---------------------------------------------------------------------------
# 3. UI_STATE 命令 handler
# ---------------------------------------------------------------------------


def test_plan_sets_plan_mode():
    """/plan 调用 ctx.set_mode(Mode.PLAN)。"""
    from wentian.permissions.decision import Mode

    ctx = FakeCtx()
    _handler("plan")(ctx, "")
    assert ctx._mode == Mode.PLAN
    assert ("set_mode", Mode.PLAN) in ctx.calls


def test_plan_with_args_sends_message():
    """/plan 改造X → 切换 PLAN 模式 + send_user_message('改造X')。"""
    from wentian.permissions.decision import Mode

    ctx = FakeCtx()
    _handler("plan")(ctx, "改造X")
    assert ctx._mode == Mode.PLAN
    assert "改造X" in ctx.sent


def test_plan_no_args_no_send():
    """/plan（无参）不调用 send_user_message。"""
    ctx = FakeCtx()
    _handler("plan")(ctx, "")
    assert len(ctx.sent) == 0


def test_do_sets_default_mode():
    """/do 调用 ctx.set_mode(Mode.DEFAULT)。"""
    from wentian.permissions.decision import Mode

    ctx = FakeCtx()
    ctx._mode = Mode.PLAN  # 先设为其他模式
    _handler("do")(ctx, "")
    assert ctx._mode == Mode.DEFAULT


def test_do_with_args_sends_message():
    """/do 执行Y → 切换 DEFAULT 模式 + send_user_message('执行Y')。"""
    ctx = FakeCtx()
    _handler("do")(ctx, "执行Y")
    assert "执行Y" in ctx.sent


def test_clear_calls_clear_context_and_prints():
    """/clear 调用 ctx.clear_context() 且打印确认文案。"""
    ctx = FakeCtx()
    _handler("clear")(ctx, "")
    assert any("clear_context" in c[0] for c in ctx.calls)
    assert len(ctx.printed) >= 1
    # 打印内容含「清空」语义
    assert any("清空" in str(p) for p in ctx.printed)


def test_permission_no_args_prints_mode():
    """/permission 无参打印当前模式。"""
    from wentian.permissions.decision import Mode

    ctx = FakeCtx()
    ctx._mode = Mode.PLAN
    _handler("permission")(ctx, "")
    # 打印内容含当前模式的字符串
    assert any("plan" in str(p).lower() or "Plan" in str(p) for p in ctx.printed)


def test_permission_with_valid_mode_switches():
    """/permission acceptEdits → ctx.set_mode(Mode.ACCEPT_EDITS)。"""
    from wentian.permissions.decision import Mode

    ctx = FakeCtx()
    _handler("permission")(ctx, "acceptEdits")
    assert ctx._mode == Mode.ACCEPT_EDITS
    assert ("set_mode", Mode.ACCEPT_EDITS) in ctx.calls


def test_permission_with_alias_accept():
    """/permission accept → ctx.set_mode(Mode.ACCEPT_EDITS)（别名）。"""
    from wentian.permissions.decision import Mode

    ctx = FakeCtx()
    _handler("permission")(ctx, "accept")
    assert ctx._mode == Mode.ACCEPT_EDITS


def test_permission_with_invalid_arg_does_not_switch():
    """/permission 乱写 → 不切换，打印提示。"""
    from wentian.permissions.decision import Mode

    ctx = FakeCtx()
    ctx._mode = Mode.DEFAULT
    _handler("permission")(ctx, "乱写")
    assert ctx._mode == Mode.DEFAULT  # 未切换
    # 打印了提示
    assert len(ctx.printed) >= 1


def test_provider_with_name_calls_switch():
    """/provider deepseek → ctx.switch_provider('deepseek')。"""
    ctx = FakeCtx()
    _handler("provider")(ctx, "deepseek")
    assert ("switch_provider", "deepseek") in ctx.calls


def test_provider_no_args_prints_usage():
    """/provider（无参）→ 打印用法提示。"""
    ctx = FakeCtx()
    _handler("provider")(ctx, "")
    assert len(ctx.printed) >= 1
    # 无 switch_provider 调用
    assert not any("switch_provider" in c[0] for c in ctx.calls)


# ---------------------------------------------------------------------------
# 4. session 子命令
# ---------------------------------------------------------------------------


def test_session_new():
    """/session new → ctx.new_session()。"""
    ctx = FakeCtx()
    _handler("session")(ctx, "new")
    assert ("new_session", None) in ctx.calls


def test_session_list():
    """/session list → ctx.list_sessions(all_projects=False)。"""
    ctx = FakeCtx()
    _handler("session")(ctx, "list")
    assert ("list_sessions", False) in ctx.calls


def test_session_list_all():
    """/session list --all → ctx.list_sessions(all_projects=True)。"""
    ctx = FakeCtx()
    _handler("session")(ctx, "list --all")
    assert ("list_sessions", True) in ctx.calls


def test_session_resume():
    """/session resume abc → ctx.resume_session('abc')。"""
    ctx = FakeCtx()
    _handler("session")(ctx, "resume abc")
    assert ("resume_session", "abc") in ctx.calls


def test_session_no_args_prints_usage():
    """/session（无参）→ 打印用法。"""
    ctx = FakeCtx()
    _handler("session")(ctx, "")
    assert len(ctx.printed) >= 1
    # 无会话操作
    session_ops = {"new_session", "list_sessions", "resume_session"}
    assert not any(c[0] in session_ops for c in ctx.calls)


def test_session_unknown_subcommand_prints_usage():
    """/session 乱写 → 打印用法，不触发操作。"""
    ctx = FakeCtx()
    _handler("session")(ctx, "乱写")
    assert len(ctx.printed) >= 1
    session_ops = {"new_session", "list_sessions", "resume_session"}
    assert not any(c[0] in session_ops for c in ctx.calls)


# ---------------------------------------------------------------------------
# 5. PROMPT 命令 handler
# ---------------------------------------------------------------------------


def test_review_sends_message_with_audit_keyword():
    """/review → send_user_message 文案含「审查」。"""
    ctx = FakeCtx()
    _handler("review")(ctx, "")
    assert len(ctx.sent) == 1
    assert "审查" in ctx.sent[0]


def test_review_with_args_includes_args():
    """/review src/foo.py → 文案含参数。"""
    ctx = FakeCtx()
    _handler("review")(ctx, "src/foo.py")
    assert "src/foo.py" in ctx.sent[0]


def test_review_no_side_effects_beyond_send():
    """/review 只调 send_user_message，无其他副作用（无 switch/clear 等）。"""
    ctx = FakeCtx()
    _handler("review")(ctx, "")
    # ctx.calls 只含 send_user_message（FakeCtx.send_user_message 记录调用）
    assert len(ctx.printed) == 0
    # 无会话/provider 操作
    forbidden = {
        "new_session",
        "list_sessions",
        "resume_session",
        "switch_provider",
        "clear_context",
    }
    assert not any(c[0] in forbidden for c in ctx.calls)


# ---------------------------------------------------------------------------
# 6. render_help / parse_mode 辅助函数
# ---------------------------------------------------------------------------


def test_render_help_returns_string():
    """render_help 返回纯文本字符串。"""
    from wentian.commands.builtins import render_help

    specs = _reg().visible()
    result = render_help(specs)
    assert isinstance(result, str)
    assert len(result) > 0


def test_render_help_contains_all_visible_names():
    """render_help 输出含所有可见命令名。"""
    from wentian.commands.builtins import render_help

    specs = _reg().visible()
    result = render_help(specs)
    for spec in specs:
        assert spec.name in result, f"render_help 缺少命令 {spec.name!r}"


def test_parse_mode_valid_values():
    """parse_mode 能正确解析全部合法输入。"""
    from wentian.commands.builtins import parse_mode
    from wentian.permissions.decision import Mode

    assert parse_mode("plan") is Mode.PLAN
    assert parse_mode("PLAN") is Mode.PLAN
    assert parse_mode("default") is Mode.DEFAULT
    assert parse_mode("DEFAULT") is Mode.DEFAULT
    assert parse_mode("acceptEdits") is Mode.ACCEPT_EDITS
    assert parse_mode("ACCEPTEDITS") is Mode.ACCEPT_EDITS
    assert parse_mode("accept") is Mode.ACCEPT_EDITS
    assert parse_mode("ACCEPT") is Mode.ACCEPT_EDITS
    assert parse_mode("bypassPermissions") is Mode.BYPASS
    assert parse_mode("bypass") is Mode.BYPASS
    assert parse_mode("BYPASS") is Mode.BYPASS


def test_parse_mode_invalid_returns_none():
    """parse_mode 对无法解析的字符串返回 None。"""
    from wentian.commands.builtins import parse_mode

    assert parse_mode("乱写") is None
    assert parse_mode("") is None
    assert parse_mode("foo") is None
