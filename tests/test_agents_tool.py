"""v0.13 · T143/T145 · AgentTool 测试（全离线，假 runner + 假 manager）。

覆盖用例（spec/task.md T143/T145）：
1. schema / name / category 检查（F91 / AC116）
2. definition 前台路径——manager.run_foreground 被调用，submit 未被调用（F94/F98②）
3. definition 前台自动转后台——run_foreground 返 (None, id, True) → 工具返「已转后台」
4. definition background=True——假 manager.submit 被调用，run_foreground 未被调用（F98）
5. fork 恒后台——假 manager.submit（AgentType.FORK），run_foreground 未被调用（F95）
6. 嵌套拦截——depth=1 直接返报错，run_foreground/submit 均未被调用（N52）
7. 无角色优雅报错——registry 查无此名，返「无此角色」文本，不崩（N50）
8. manager=None → definition 前台/后台/fork 均返优雅「未启用」文本，不崩
9. AgentTool 不接受 runner= 参数；timeout_s > cfg.foreground_timeout_s
"""

from __future__ import annotations

from wentian.agents.loader import AgentRegistry
from wentian.agents.spec import AgentDef, AgentType
from wentian.agents.tool import AgentTool
from wentian.permissions.decision import Category


# ---------------------------------------------------------------------------
# 测试夹具
# ---------------------------------------------------------------------------


def _make_registry(with_coder: bool = True) -> AgentRegistry:
    registry = AgentRegistry()
    if with_coder:
        registry.add(
            AgentDef(
                name="coder",
                description="编写代码的角色",
                body="你是一个代码助手。",
            )
        )
    return registry


class FakeManager:
    """假 manager：记录 run_foreground / submit 调用参数，不启动线程。

    v0.13 FixB2 — run_foreground 替代 runner 作为前台路径；submit 仍用于显式后台 + fork。
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._counter = 0
        # 控制 run_foreground 返回值：默认快速成功（前台）
        self.foreground_result: tuple[str | None, str, bool] | None = None
        self.fg_calls: list[dict] = []

    def run_foreground(
        self,
        agent_def: AgentDef,
        prompt: str,
        **runner_kwargs,
    ) -> tuple[str | None, str, bool]:
        self._counter += 1
        task_id = f"fg-{self._counter}"
        self.fg_calls.append({"agent_def": agent_def, "prompt": prompt})
        if self.foreground_result is not None:
            return self.foreground_result
        return ("前台结果", task_id, False)

    def submit(
        self,
        agent_def: AgentDef,
        prompt: str,
        *,
        background: bool = False,
        agent_type: AgentType = AgentType.DEFINITION,
        **runner_kwargs,
    ) -> str:
        self._counter += 1
        self.calls.append(
            {
                "agent_def": agent_def,
                "prompt": prompt,
                "background": background,
                "agent_type": agent_type,
            }
        )
        return f"task-{self._counter}"


def _make_tool(
    registry: AgentRegistry | None = None,
    manager: FakeManager | None = None,
) -> AgentTool:
    """构造 AgentTool——不再接受 runner 参数（FixB2）。"""
    from wentian.config import AgentsConfig

    if registry is None:
        registry = _make_registry()
    if manager is None:
        manager = FakeManager()
    cfg = AgentsConfig()
    return AgentTool(registry=registry, manager=manager, cfg=cfg)


# ---------------------------------------------------------------------------
# 用例 1：schema / name / category（F91 / AC116）
# ---------------------------------------------------------------------------


class TestSchema:
    def test_name(self) -> None:
        tool = _make_tool()
        assert tool.name == "Agent"

    def test_friendly_name(self) -> None:
        tool = _make_tool()
        assert tool.friendly_name == "Agent"

    def test_category(self) -> None:
        tool = _make_tool()
        assert tool.category == Category.COMMAND_EXEC

    def test_parameters_type_field(self) -> None:
        tool = _make_tool()
        params = tool.parameters
        assert params["type"] == "object"
        props = params["properties"]
        assert "type" in props
        # type 字段应有 enum: definition / fork
        assert set(props["type"]["enum"]) == {"definition", "fork"}

    def test_parameters_required(self) -> None:
        tool = _make_tool()
        required = set(tool.parameters.get("required", []))
        # type 和 prompt 必填，agent_type 与 background 可选
        assert "type" in required
        assert "prompt" in required

    def test_parameters_optional_fields(self) -> None:
        tool = _make_tool()
        props = tool.parameters["properties"]
        assert "agent_type" in props
        assert "background" in props
        # background 应为 bool 类型
        assert props["background"]["type"] == "boolean"


# ---------------------------------------------------------------------------
# 用例 2：definition 前台路径（F94/F98②）— 走 manager.run_foreground
# ---------------------------------------------------------------------------


class TestDefinitionForeground:
    def test_run_foreground_called_once(self) -> None:
        """前台路径：manager.run_foreground 被调用一次，返回其 text。"""
        manager = FakeManager()
        tool = _make_tool(manager=manager)

        result = tool.run(
            {"type": "definition", "agent_type": "coder", "prompt": "帮我写"}
        )

        assert result == "前台结果"
        assert len(manager.fg_calls) == 1
        assert manager.fg_calls[0]["prompt"] == "帮我写"

    def test_submit_not_called_for_foreground(self) -> None:
        """前台路径：manager.submit 不被调用。"""
        manager = FakeManager()
        tool = _make_tool(manager=manager)
        tool.run({"type": "definition", "agent_type": "coder", "prompt": "帮我写"})
        assert len(manager.calls) == 0

    def test_foreground_auto_background_conversion(self) -> None:
        """run_foreground 返 (None, id, True) → 工具返含「已转后台」和任务 id 的字符串。"""
        manager = FakeManager()
        manager.foreground_result = (None, "bg-7", True)
        tool = _make_tool(manager=manager)

        result = tool.run(
            {"type": "definition", "agent_type": "coder", "prompt": "慢任务"}
        )

        assert "已转后台" in result
        assert "bg-7" in result


# ---------------------------------------------------------------------------
# 用例 3：definition background=True（F98）
# ---------------------------------------------------------------------------


class TestDefinitionBackground:
    def test_manager_submit_called(self) -> None:
        """显式 background=True → manager.submit 被调用一次；run_foreground 不被调用。"""
        manager = FakeManager()
        tool = _make_tool(manager=manager)

        tool.run(
            {
                "type": "definition",
                "agent_type": "coder",
                "prompt": "写",
                "background": True,
            }
        )

        # manager.submit 应被调用一次
        assert len(manager.calls) == 1
        # run_foreground 不应被调用
        assert len(manager.fg_calls) == 0

    def test_result_contains_task_id(self) -> None:
        manager = FakeManager()
        tool = _make_tool(manager=manager)
        result = tool.run(
            {
                "type": "definition",
                "agent_type": "coder",
                "prompt": "写",
                "background": True,
            }
        )
        assert "已起" in result

    def test_run_foreground_not_called_for_explicit_background(self) -> None:
        """显式 background=True → run_foreground 不被调用（直接走 submit）。"""
        manager = FakeManager()
        tool = _make_tool(manager=manager)
        tool.run(
            {
                "type": "definition",
                "agent_type": "coder",
                "prompt": "写",
                "background": True,
            }
        )
        assert len(manager.fg_calls) == 0


# ---------------------------------------------------------------------------
# 用例 4：fork 恒后台（F95）
# ---------------------------------------------------------------------------


class TestForkAlwaysBackground:
    def test_manager_submit_called_with_fork_type(self) -> None:
        manager = FakeManager()
        tool = _make_tool(manager=manager)
        tool.run({"type": "fork", "prompt": "继续"})

        assert len(manager.calls) == 1
        assert manager.calls[0]["agent_type"] == AgentType.FORK

    def test_result_contains_started_marker(self) -> None:
        manager = FakeManager()
        tool = _make_tool(manager=manager)
        result = tool.run({"type": "fork", "prompt": "继续"})
        assert "已起" in result

    def test_run_foreground_not_called_for_fork(self) -> None:
        """fork 路径：run_foreground 不被调用（恒走 submit）。"""
        manager = FakeManager()
        tool = _make_tool(manager=manager)
        tool.run({"type": "fork", "prompt": "继续"})
        assert len(manager.fg_calls) == 0


# ---------------------------------------------------------------------------
# 用例 5：嵌套拦截 depth=1（N52）
# ---------------------------------------------------------------------------


class TestNestingGuard:
    def test_depth_1_returns_error(self) -> None:
        tool = _make_tool()
        result = tool.run(
            {"type": "definition", "agent_type": "coder", "prompt": "帮我写"},
            depth=1,
        )
        assert "嵌套" in result or "禁止" in result

    def test_depth_1_run_foreground_not_called(self) -> None:
        """depth=1：run_foreground 不被调用（嵌套拦截在前）。"""
        manager = FakeManager()
        tool = _make_tool(manager=manager)
        tool.run(
            {"type": "definition", "agent_type": "coder", "prompt": "帮我写"},
            depth=1,
        )
        assert len(manager.fg_calls) == 0

    def test_depth_1_manager_not_called(self) -> None:
        manager = FakeManager()
        tool = _make_tool(manager=manager)
        tool.run(
            {"type": "fork", "prompt": "继续"},
            depth=1,
        )
        assert len(manager.calls) == 0


# ---------------------------------------------------------------------------
# 用例 6：无角色优雅报错（N50）
# ---------------------------------------------------------------------------


class TestUnknownRole:
    def test_unknown_role_returns_error_text(self) -> None:
        tool = _make_tool()  # registry 只有 "coder"
        result = tool.run(
            {"type": "definition", "agent_type": "不存在的角色", "prompt": "做点什么"}
        )
        assert "无此角色" in result

    def test_unknown_role_does_not_crash(self) -> None:
        tool = _make_tool()
        # 不抛异常
        result = tool.run(
            {"type": "definition", "agent_type": "ghost", "prompt": "测试"}
        )
        assert isinstance(result, str)

    def test_unknown_role_run_foreground_not_called(self) -> None:
        """未知角色：run_foreground 不被调用（registry 查无此名直接返错误文本）。"""
        manager = FakeManager()
        tool = _make_tool(manager=manager)
        tool.run({"type": "definition", "agent_type": "nobody", "prompt": "test"})
        assert len(manager.fg_calls) == 0


# ---------------------------------------------------------------------------
# v0.13 · T145 — fork 父历史继承 + manager=None 软化
# ---------------------------------------------------------------------------


class FakeManagerWithKwargs(FakeManager):
    """扩展版假 manager：记录传入的额外 runner_kwargs（含 parent_messages 等）。"""

    def submit(
        self,
        agent_def,
        prompt: str,
        *,
        background: bool = False,
        agent_type: AgentType = AgentType.DEFINITION,
        **runner_kwargs,
    ) -> str:
        self._counter += 1
        self.calls.append(
            {
                "agent_def": agent_def,
                "prompt": prompt,
                "background": background,
                "agent_type": agent_type,
                "runner_kwargs": runner_kwargs,
            }
        )
        return f"task-{self._counter}"


class TestT145ForkParentMessages:
    def test_fork_passes_parent_messages_when_get_fn_set(self) -> None:
        """fork 路径：get_parent_messages 注入时，manager.submit 收到 parent_messages。"""
        from wentian.config import AgentsConfig

        parent_history = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "好的"},
        ]

        manager = FakeManagerWithKwargs()
        cfg = AgentsConfig()
        tool = AgentTool(
            registry=_make_registry(),
            manager=manager,
            cfg=cfg,
            get_parent_messages=lambda: parent_history,
        )

        tool.run({"type": "fork", "prompt": "继续做"})

        assert len(manager.calls) == 1
        assert "parent_messages" in manager.calls[0]["runner_kwargs"]
        assert manager.calls[0]["runner_kwargs"]["parent_messages"] == parent_history

    def test_fork_no_parent_messages_when_fn_not_set(self) -> None:
        """fork 路径：get_parent_messages 未设时，manager.submit 不传 parent_messages。"""
        from wentian.config import AgentsConfig

        manager = FakeManagerWithKwargs()
        cfg = AgentsConfig()
        tool = AgentTool(
            registry=_make_registry(),
            manager=manager,
            cfg=cfg,
        )

        tool.run({"type": "fork", "prompt": "继续做"})

        assert len(manager.calls) == 1
        assert "parent_messages" not in manager.calls[0]["runner_kwargs"]


class TestT145ManagerNoneGuards:
    def test_background_definition_manager_none_graceful(self) -> None:
        """background=True + manager=None → 返「agents 未启用」优雅文本，不崩。"""
        from wentian.config import AgentsConfig

        tool = AgentTool(
            registry=_make_registry(),
            manager=None,
            cfg=AgentsConfig(),
        )

        result = tool.run(
            {
                "type": "definition",
                "agent_type": "coder",
                "prompt": "写",
                "background": True,
            }
        )
        assert isinstance(result, str)
        assert len(result) > 0  # 不崩、不抛、返非空字符串

    def test_fork_manager_none_graceful(self) -> None:
        """fork + manager=None → 返优雅文本，不崩。"""
        from wentian.config import AgentsConfig

        tool = AgentTool(
            registry=_make_registry(),
            manager=None,
            cfg=AgentsConfig(),
        )

        result = tool.run({"type": "fork", "prompt": "继续"})
        assert isinstance(result, str)
        assert len(result) > 0

    def test_foreground_definition_manager_none_graceful(self) -> None:
        """definition 前台 + manager=None → 返「agents 未启用」优雅文本，不崩（FixB2）。"""
        from wentian.config import AgentsConfig

        tool = AgentTool(
            registry=_make_registry(),
            manager=None,
            cfg=AgentsConfig(),
        )

        result = tool.run({"type": "definition", "agent_type": "coder", "prompt": "写"})
        assert isinstance(result, str)
        assert len(result) > 0
        assert "未启用" in result


# ---------------------------------------------------------------------------
# FixB2 — 构造器 / timeout_s 断言
# ---------------------------------------------------------------------------


class TestFixB2ConstructorAndTimeout:
    def test_tool_constructed_without_runner_param(self) -> None:
        """AgentTool 不接受 runner= 参数（FixB2 去掉 runner 入参）。"""
        import inspect

        sig = inspect.signature(AgentTool.__init__)
        assert "runner" not in sig.parameters, (
            "AgentTool.__init__ 不应有 runner 参数（FixB2 已移除）"
        )

    def test_timeout_s_exceeds_foreground_timeout_s(self) -> None:
        """tool.timeout_s 应 > cfg.foreground_timeout_s（执行器不误杀前台调用）。"""
        from wentian.config import AgentsConfig

        cfg = AgentsConfig()
        tool = AgentTool(registry=_make_registry(), manager=FakeManager(), cfg=cfg)
        assert tool.timeout_s > cfg.foreground_timeout_s, (
            f"timeout_s={tool.timeout_s} 应 > foreground_timeout_s={cfg.foreground_timeout_s}"
        )
