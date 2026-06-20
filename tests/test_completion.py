"""v0.10 · C90 · F75（任务 T113）— CommandCompleter 与 PromptInput 补全接线测试。

测试策略：
- 直接用 prompt_toolkit.document.Document 构造文档快照，无需真实终端。
- get_completions(doc, None) 收集为 list 断言。
- input 接线用 create_pipe_input + DummyOutput 构造离线 PromptInput。
"""

from __future__ import annotations

from prompt_toolkit.document import Document

from wentian.commands.registry import CommandRegistry
from wentian.commands.spec import CommandSpec, CommandType


# ---------------------------------------------------------------------------
# 辅助：构造最小测试 registry
# ---------------------------------------------------------------------------


def _noop(ctx, args):  # noqa: ANN001
    return None


def _make_small_registry() -> CommandRegistry:
    """返回含 3 个可见命令 + 1 个隐藏命令的小型注册表。

    可见命令：help / session / status（按此顺序注册）
    隐藏命令：_debug（hidden=True）
    """
    reg = CommandRegistry()

    reg.register(
        CommandSpec(
            name="help",
            summary="显示可用命令列表",
            usage="/help",
            type=CommandType.LOCAL,
            handler=_noop,
        )
    )
    reg.register(
        CommandSpec(
            name="session",
            summary="会话管理：new / list / resume",
            usage="/session new|list|resume",
            type=CommandType.UI_STATE,
            handler=_noop,
        )
    )
    reg.register(
        CommandSpec(
            name="status",
            summary="显示当前状态栏",
            usage="/status",
            type=CommandType.LOCAL,
            handler=_noop,
        )
    )
    reg.register(
        CommandSpec(
            name="_debug",
            summary="内部调试命令（隐藏）",
            usage="/_debug",
            type=CommandType.LOCAL,
            handler=_noop,
            hidden=True,
        )
    )
    return reg


# ===========================================================================
# 1. 前缀命中 "/se" → 含 session，start_position=-2，display_meta 正确
# ===========================================================================


class TestPrefixMatch:
    def test_slash_se_matches_session(self):
        """'/se' 前缀补全应命中 session；start_position==-2、display_meta 含 summary。"""
        from wentian.ui.completion import CommandCompleter

        reg = _make_small_registry()
        completer = CommandCompleter(reg)

        doc = Document("/se")
        results = list(completer.get_completions(doc, None))

        names = [c.text for c in results]
        assert "session" in names, f"期望命中 session，实际候选：{names}"

        match = next(c for c in results if c.text == "session")
        assert match.start_position == -2, (
            f"start_position 应为 -2（替换 'se'），实际：{match.start_position}"
        )
        assert "会话管理" in str(match.display_meta), (
            f"display_meta 应含 summary，实际：{match.display_meta!r}"
        )

    def test_display_is_slash_prefixed(self):
        """display 应为 '/session'（含斜杠前缀）。"""
        from wentian.ui.completion import CommandCompleter

        reg = _make_small_registry()
        completer = CommandCompleter(reg)

        doc = Document("/se")
        results = list(completer.get_completions(doc, None))
        match = next(c for c in results if c.text == "session")
        # display 是 FormattedText 或字符串；转字符串后验证
        display_str = str(match.display)
        assert "/session" in display_str, (
            f"display 应含 /session，实际：{display_str!r}"
        )


# ===========================================================================
# 2. "/" → 全部可见命令（不含隐藏）
# ===========================================================================


class TestSlashAlone:
    def test_slash_alone_returns_all_visible(self):
        """'/' 应返回全部可见命令（help / session / status），不含 _debug。"""
        from wentian.ui.completion import CommandCompleter

        reg = _make_small_registry()
        completer = CommandCompleter(reg)

        doc = Document("/")
        results = list(completer.get_completions(doc, None))

        names = [c.text for c in results]
        assert set(names) == {"help", "session", "status"}, (
            f"可见命令应为 help/session/status，实际：{names}"
        )

    def test_slash_alone_start_position_zero(self):
        """'/' 后空前缀时 start_position 应为 0（前缀长度为 0）。"""
        from wentian.ui.completion import CommandCompleter

        reg = _make_small_registry()
        completer = CommandCompleter(reg)

        doc = Document("/")
        results = list(completer.get_completions(doc, None))
        for c in results:
            assert c.start_position == 0, (
                f"空前缀 start_position 应为 0，实际：{c.start_position}"
            )


# ===========================================================================
# 3. 无命中 "/zzz" → 零候选
# ===========================================================================


class TestNoMatch:
    def test_zzz_returns_empty(self):
        """'/zzz' 无命中，应返回空列表。"""
        from wentian.ui.completion import CommandCompleter

        reg = _make_small_registry()
        completer = CommandCompleter(reg)

        doc = Document("/zzz")
        results = list(completer.get_completions(doc, None))
        assert results == [], f"期望零候选，实际：{results}"


# ===========================================================================
# 4. 隐藏命令不出现在候选
# ===========================================================================


class TestHiddenExcluded:
    def test_hidden_not_in_completions(self):
        """隐藏命令 _debug 即使前缀命中也不出现在补全候选中。"""
        from wentian.ui.completion import CommandCompleter

        reg = _make_small_registry()
        completer = CommandCompleter(reg)

        # 前缀 "_de" 匹配 _debug（若不过滤则会出现）
        doc = Document("/_de")
        results = list(completer.get_completions(doc, None))
        names = [c.text for c in results]
        assert "_debug" not in names, f"隐藏命令不应出现，实际候选：{names}"
        assert results == [], f"应零候选，实际：{results}"


# ===========================================================================
# 5. 有空格 / 不以 "/" 开头 → 不补全
# ===========================================================================


class TestNoCompletionConditions:
    def test_space_in_text_no_completions(self):
        """'/session '（含空格，已输参数）不应触发补全。"""
        from wentian.ui.completion import CommandCompleter

        reg = _make_small_registry()
        completer = CommandCompleter(reg)

        doc = Document("/session ")
        results = list(completer.get_completions(doc, None))
        assert results == [], f"有空格时应零候选，实际：{results}"

    def test_no_slash_no_completions(self):
        """'abc'（不以 / 开头）不应触发补全。"""
        from wentian.ui.completion import CommandCompleter

        reg = _make_small_registry()
        completer = CommandCompleter(reg)

        doc = Document("abc")
        results = list(completer.get_completions(doc, None))
        assert results == [], f"非斜杠开头应零候选，实际：{results}"

    def test_empty_text_no_completions(self):
        """空输入不应触发补全。"""
        from wentian.ui.completion import CommandCompleter

        reg = _make_small_registry()
        completer = CommandCompleter(reg)

        doc = Document("")
        results = list(completer.get_completions(doc, None))
        assert results == [], f"空输入应零候选，实际：{results}"


# ===========================================================================
# 6. PromptInput 接线：completer 参数注入与向后兼容
# ===========================================================================


class TestPromptInputWiring:
    def test_completer_injected_into_session(self, tmp_path):
        """PromptInput(completer=...) 应将 completer 注入 _session.completer。"""
        from prompt_toolkit.input.defaults import create_pipe_input
        from prompt_toolkit.output import DummyOutput

        from wentian.ui.completion import CommandCompleter
        from wentian.ui.input import PromptInput

        reg = _make_small_registry()
        completer = CommandCompleter(reg)

        with create_pipe_input() as pipe:
            pi = PromptInput(
                history_path=tmp_path / "h",
                completer=completer,
                input=pipe,
                output=DummyOutput(),
            )
            assert pi._session.completer is completer, (
                "PromptSession.completer 应与传入的 completer 相同"
            )

    def test_completer_none_old_behavior(self, tmp_path):
        """completer=None（默认）时不抛出，_session.completer 应为 None。"""
        from prompt_toolkit.input.defaults import create_pipe_input
        from prompt_toolkit.output import DummyOutput

        from wentian.ui.input import PromptInput

        with create_pipe_input() as pipe:
            pi = PromptInput(
                history_path=tmp_path / "h",
                input=pipe,
                output=DummyOutput(),
            )
            assert pi._session.completer is None, (
                "未注入 completer 时 PromptSession.completer 应为 None"
            )

    def test_prompt_input_no_raise_with_completer(self, tmp_path):
        """构造 PromptInput(completer=...) 不应抛出任何异常。"""
        from prompt_toolkit.input.defaults import create_pipe_input
        from prompt_toolkit.output import DummyOutput

        from wentian.ui.completion import CommandCompleter
        from wentian.ui.input import PromptInput

        reg = _make_small_registry()
        completer = CommandCompleter(reg)

        with create_pipe_input() as pipe:
            # 仅构造，不触发 prompt；验证不抛即可
            pi = PromptInput(
                history_path=tmp_path / "h",
                completer=completer,
                input=pipe,
                output=DummyOutput(),
            )
            assert pi is not None


# ===========================================================================
# 7. 大小写不敏感前缀（"/SE" 命中 session）
# ===========================================================================


class TestCaseInsensitive:
    def test_uppercase_prefix_matches(self):
        """'/SE' 大写前缀应与 session 大小写不敏感匹配。"""
        from wentian.ui.completion import CommandCompleter

        reg = _make_small_registry()
        completer = CommandCompleter(reg)

        doc = Document("/SE")
        results = list(completer.get_completions(doc, None))
        names = [c.text for c in results]
        assert "session" in names, (
            f"大写前缀应命中 session（注册表 completions 做小写化），实际：{names}"
        )
