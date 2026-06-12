"""Tests for REPL plan mode — /plan //do（v0.4 · C19 · F33，任务 T56）.

计划模式是 REPL 内存态界面策略（不持久化、不随 /new //resume //provider
重置）：registry 存在且开启时，回合的 tools 按 ``plan_tools`` 名单过滤为
三只读工具、system 追加计划模式后缀，且同名单作为 ``allowed_tools`` 注入
AgentLoop（双保险：声明过滤挡引导，loop 的 blocked 合成挡硬闯）。
"""
from __future__ import annotations

from pathlib import Path

from rich.console import Console

from conftest import ScriptedProvider

from wentian.config import ConfigError
from wentian.providers.base import (
    Done,
    Provider,
    TextDelta,
    ToolCallEvent,
    ToolSpec,
)
from wentian.render import Renderer
from wentian.session import SessionStore


# ---------------------------------------------------------------------------
# Fakes / helpers（与 tests/test_repl.py 同形态，独立定义保持模块自足）
# ---------------------------------------------------------------------------

class _FakeTool:
    """带 requires_confirmation 的最小工具替身（loop 分类用）。"""

    def __init__(self, requires_confirmation: bool = True) -> None:
        self.requires_confirmation = requires_confirmation


class _FakeRegistry:
    """Minimal registry：specs() 供 provider 声明，get() 供 loop 分类。"""

    def __init__(self, specs: list[ToolSpec], tools: dict | None = None) -> None:
        self._specs = specs
        self._tools = (
            tools if tools is not None else {s.name: _FakeTool() for s in specs}
        )

    def specs(self) -> list[ToolSpec]:
        return self._specs

    def get(self, name: str):
        return self._tools.get(name)


class _FakeExecutor:
    """记录 execute() 调用；blocked 调用绝不应触达这里。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []

    def execute(self, call_id: str, name: str, arguments):
        self.calls.append((call_id, name, arguments))
        return _Outcome(
            call_id=call_id, name=name, content=f"ran {name}", is_error=False
        )


class _Outcome:
    """ToolOutcome 鸭子类型最小替身。"""

    def __init__(self, *, call_id, name, content, is_error, denied=False):
        self.call_id = call_id
        self.name = name
        self.content = content
        self.is_error = is_error
        self.denied = denied


def _spec(name: str) -> ToolSpec:
    return ToolSpec(name=name, description=name, parameters={"type": "object"})


#: 三只读 + 一写（声明顺序即过滤后顺序）。
_SPECS = [
    _spec("read_file"),
    _spec("find_files"),
    _spec("search_text"),
    _spec("write_file"),
]


def _make_registry() -> _FakeRegistry:
    return _FakeRegistry(
        _SPECS,
        tools={
            "read_file": _FakeTool(requires_confirmation=False),
            "find_files": _FakeTool(requires_confirmation=False),
            "search_text": _FakeTool(requires_confirmation=False),
            "write_file": _FakeTool(requires_confirmation=True),
        },
    )


def _make_plan_repl(
    provider: Provider,
    store: SessionStore,
    console: Console,
    *,
    inputs: list[str],
    registry=None,
    executor=None,
    system: str | None = None,
):
    """Assemble a REPL for plan-mode tests. Returns (repl, session)."""
    from wentian.repl import REPL

    session = store.create(provider=provider.name)
    renderer = Renderer(console)
    input_iter = iter(inputs)

    def _input_fn(prompt: str = "") -> str:
        return next(input_iter)

    def provider_factory(name: str) -> Provider:
        raise ConfigError(f"unknown provider: {name}")

    repl = REPL(
        provider=provider,
        session=session,
        store=store,
        renderer=renderer,
        provider_factory=provider_factory,
        input_fn=_input_fn,
        system=system,
        registry=registry,
        executor=executor,
    )
    return repl, session


# ===========================================================================
# 1 — tools 过滤 + system 后缀；/do 恢复
# ===========================================================================

class TestPlanModeToolsAndSystem:
    def test_plan_filters_tools_and_appends_suffix_do_restores(self, tmp_path):
        """/plan 后回合只声明三只读工具、system 含计划模式后缀；/do 后
        恢复全量 tools、system 无后缀。"""
        provider = ScriptedProvider([
            [TextDelta("计划如下"), Done()],
            [TextDelta("开始执行"), Done()],
        ])
        registry = _make_registry()
        executor = _FakeExecutor()
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_plan_repl(
            provider, store, console,
            inputs=["/plan", "勘察一下", "/do", "动手吧", "/exit"],
            registry=registry, executor=executor, system="基础提示",
        )

        repl.run()

        assert len(provider.calls) == 2
        # 计划模式回合：仅三只读名，原顺序。
        assert [t.name for t in provider.tools_seen[0]] == [
            "read_file", "find_files", "search_text",
        ]
        assert provider.systems_seen[0].startswith("基础提示")
        assert "计划模式" in provider.systems_seen[0]
        assert "只读工具" in provider.systems_seen[0]
        # /do 之后：全量 tools、system 还原无后缀。
        assert [t.name for t in provider.tools_seen[1]] == [
            "read_file", "find_files", "search_text", "write_file",
        ]
        assert provider.systems_seen[1] == "基础提示"

    def test_plan_mode_without_registry_keeps_plain_behavior(self, tmp_path):
        """registry=None 时计划模式不改变行为：tools=None、system 原样
        （维持 T55 行为）。"""
        provider = ScriptedProvider([[TextDelta("回答"), Done()]])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_plan_repl(
            provider, store, console, inputs=[], system="基础提示",
        )

        repl._dispatch_command("/plan")
        repl._chat_once("你好")

        assert provider.tools_seen == [None]
        assert provider.systems_seen == ["基础提示"]


# ===========================================================================
# 2 — 计划模式拦截写工具（blocked 合成，循环继续）
# ===========================================================================

class TestPlanModeBlocksWrites:
    def test_write_tool_blocked_executor_untouched_loop_continues(self, tmp_path):
        """计划模式中脚本请求 write_file → executor 未被调；入史 tool 消息
        is_error=True 且 content 含「计划模式」；循环继续（第 2 轮照常，
        非 UNKNOWN_TOOL_LOOP 停机）。"""
        provider = ScriptedProvider([
            [
                TextDelta("先改文件"),
                ToolCallEvent(id="w1", name="write_file", arguments={"path": "a"}),
                Done(),
            ],
            [TextDelta("收到限制，先给计划"), Done()],
        ])
        registry = _make_registry()
        executor = _FakeExecutor()
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_plan_repl(
            provider, store, console, inputs=[],
            registry=registry, executor=executor,
        )

        repl._dispatch_command("/plan")
        repl._chat_once("改个文件")

        # blocked 调用绝不触达 executor。
        assert executor.calls == []
        # 历史成对：拦截结果作为错误 tool 消息入史，循环继续到第 2 轮。
        assert [m["role"] for m in session.messages] == [
            "user", "assistant", "tool", "assistant",
        ]
        tool_msg = session.messages[2]
        assert tool_msg["tool_call_id"] == "w1"
        assert tool_msg["is_error"] is True
        assert "计划模式" in tool_msg["content"]
        assert session.messages[3] == {
            "role": "assistant", "content": "收到限制，先给计划",
        }
        assert len(provider.calls) == 2
        # 不是未知工具停机：黄提示不应出现。
        assert "未知工具" not in console.export_text()


# ===========================================================================
# 3 — status_line 标记（含跨 /new 持久：界面策略非会话数据）
# ===========================================================================

class TestPlanModeStatusLine:
    def test_status_line_marker_appears_and_clears(self, tmp_path):
        provider = ScriptedProvider([])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_plan_repl(provider, store, console, inputs=[])

        assert "计划模式" not in repl.status_line()
        repl._dispatch_command("/plan")
        assert "计划模式" in repl.status_line()
        repl._dispatch_command("/do")
        assert "计划模式" not in repl.status_line()

    def test_plan_mode_survives_new_session(self, tmp_path):
        """计划模式不随 /new 重置（REPL 界面策略，非会话数据）。"""
        provider = ScriptedProvider([])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_plan_repl(provider, store, console, inputs=[])

        repl._dispatch_command("/plan")
        repl._dispatch_command("/new")
        assert "计划模式" in repl.status_line()


# ===========================================================================
# 4 — 尾随文字语义：/do text 发消息、裸 /do 仅切换、/plan text 同理
# ===========================================================================

class TestPlanModeTrailingText:
    def test_do_with_text_exits_and_sends_message(self, tmp_path):
        provider = ScriptedProvider([[TextDelta("好的，开工"), Done()]])
        registry = _make_registry()
        executor = _FakeExecutor()
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_plan_repl(
            provider, store, console, inputs=[],
            registry=registry, executor=executor,
        )

        repl._dispatch_command("/plan")
        repl._dispatch_command("/do 按计划执行")

        assert repl._plan_mode is False
        assert len(provider.calls) == 1
        assert provider.calls[0][0] == {"role": "user", "content": "按计划执行"}
        # 已退出计划模式 → 该回合声明全量工具。
        assert [t.name for t in provider.tools_seen[0]] == [
            "read_file", "find_files", "search_text", "write_file",
        ]
        assert session.messages[0] == {"role": "user", "content": "按计划执行"}

    def test_bare_do_only_toggles_no_message(self, tmp_path):
        provider = ScriptedProvider([])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, session = _make_plan_repl(provider, store, console, inputs=[])

        repl._dispatch_command("/plan")
        repl._dispatch_command("/do")

        assert repl._plan_mode is False
        assert provider.calls == []
        assert session.messages == []

    def test_plan_with_text_sends_message_in_plan_mode(self, tmp_path):
        provider = ScriptedProvider([[TextDelta("调研计划"), Done()]])
        registry = _make_registry()
        executor = _FakeExecutor()
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_plan_repl(
            provider, store, console, inputs=[],
            registry=registry, executor=executor,
        )

        repl._dispatch_command("/plan 帮我调研这个模块")

        assert repl._plan_mode is True
        assert len(provider.calls) == 1
        assert provider.calls[0][0] == {
            "role": "user", "content": "帮我调研这个模块",
        }
        # 该回合已按计划模式过滤工具。
        assert [t.name for t in provider.tools_seen[0]] == [
            "read_file", "find_files", "search_text",
        ]


# ===========================================================================
# 5 — /help 文案 + /plan 幂等
# ===========================================================================

class TestPlanModeCommandsSurface:
    def test_help_lists_plan_and_do(self, tmp_path):
        provider = ScriptedProvider([])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_plan_repl(provider, store, console, inputs=[])

        repl._dispatch_command("/help")

        output = console.export_text()
        assert "/plan" in output
        assert "/do" in output

    def test_plan_is_idempotent(self, tmp_path):
        provider = ScriptedProvider([])
        store = SessionStore(tmp_path)
        console = Console(record=True)
        repl, _ = _make_plan_repl(provider, store, console, inputs=[])

        repl._dispatch_command("/plan")
        repl._dispatch_command("/plan")  # 重复执行：仍在计划模式，不报错

        assert repl._plan_mode is True
        repl._dispatch_command("/do")
        assert repl._plan_mode is False
