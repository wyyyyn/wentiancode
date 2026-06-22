"""v0.13 · C114 · F91/F94/F95（任务 T143）— 统一 Agent 工具 AgentTool。

对模型暴露**唯一一个** ``Agent`` 工具（类别 COMMAND_EXEC，需确认）：
- ``type="definition"``：按 ``agent_type`` 取 AgentDef，默认**前台同步**跑到底
  （调用 runner），``background=True`` 时走 manager.submit 进后台。
- ``type="fork"``：构造占位 AgentDef，**恒走** manager.submit（AgentType.FORK），
  强制后台，不阻塞主对话。
- 深度守卫：``depth >= 1`` 时直接返报错文本，**不调** runner/manager（N52 双保险）。
- 无角色软化：registry 查不到 agent_type → 返清晰错误文本，绝不崩溃（N50）。

分层纪律：只 import agents.spec / agents.loader / agents.runner / agents.manager /
config / tools.base / permissions.decision + stdlib；**绝不 import** wentian.repl /
wentian.cli。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from wentian.agents.loader import AgentRegistry
from wentian.agents.spec import AgentDef, AgentType
from wentian.config import AgentsConfig
from wentian.permissions.decision import Category
from wentian.tools.base import Tool

__all__ = ["AgentTool"]

#: depth >= 此值即拒绝再派子 Agent（N52 双保险，与 runner.py 的 _MAX_DEPTH 对齐）。
_MAX_DEPTH = 1


class AgentTool(Tool):
    """统一 Agent 工具——type 分流（definition / fork）+ 嵌套深度拦截。

    构造器参数通过**鸭子注入**传入，不直接依赖 REPL/CLI 类：

    Parameters
    ----------
    registry:
        AgentRegistry，按 name 查找 AgentDef。
    runner:
        可调用对象，签名 ``(agent_def, prompt, **kw) -> SubAgentResult``；
        definition 前台路径直接调用（不经 manager）。
    manager:
        BackgroundTaskManager（或鸭子兼容对象），提供 ``submit`` 方法；
        background / fork 路径使用。
    cfg:
        AgentsConfig，预留供后续超时等配置读取。
    """

    # ------------------------------------------------------------------ #
    # Tool ABC 类属性
    # ------------------------------------------------------------------ #

    name: str = "Agent"
    description: str = (
        "委派一个子 Agent 完成任务。\n"
        "- type='definition'：按命名角色（agent_type）启动隔离子 Agent，"
        "默认前台同步等结果；background=True 则转后台。\n"
        "- type='fork'：复用当前对话上下文 fork 一个子 Agent，恒后台，"
        "结果将在下一轮以 <system-reminder> 回灌。"
    )
    parameters: dict = {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["definition", "fork"],
                "description": "委派路径：definition（命名角色）或 fork（继承当前上下文）。",
            },
            "agent_type": {
                "type": "string",
                "description": "角色名（definition 路径必填；fork 路径忽略）。",
            },
            "prompt": {
                "type": "string",
                "description": "交给子 Agent 的任务描述。",
            },
            "background": {
                "type": "boolean",
                "description": "是否后台执行（fork 恒为 true；definition 默认 false）。",
            },
        },
        "required": ["type", "prompt"],
    }
    category: Category = Category.COMMAND_EXEC
    friendly_name: str = "Agent"

    # ------------------------------------------------------------------ #
    # 构造器
    # ------------------------------------------------------------------ #

    def __init__(
        self,
        *,
        registry: AgentRegistry | None,
        runner: Any,
        manager: Any,
        cfg: AgentsConfig,
        get_parent_messages: Callable[[], list] | None = None,
    ) -> None:
        self._registry = registry
        self._runner = runner
        self._manager = manager
        self._cfg = cfg
        self._get_parent_messages = get_parent_messages

    # ------------------------------------------------------------------ #
    # 公开入口
    # ------------------------------------------------------------------ #

    def run(self, args: dict, *, depth: int = 0) -> str:  # type: ignore[override]
        """执行 Agent 工具。

        Parameters
        ----------
        args:
            工具调用参数，需含 ``type`` 与 ``prompt``。
        depth:
            调用深度（0 = 主对话；>=1 = 子 Agent 内部）。深度 >=1 时立即拦截。
        """
        # ① 嵌套拦截——最先判，绝不 spawn（N52 双保险）
        if depth >= _MAX_DEPTH:
            return "子 Agent 禁止嵌套调用 Agent 工具（depth >= 1）。"

        agent_type_str = args.get("type", "")
        prompt = args.get("prompt", "")

        if agent_type_str == "definition":
            return self._run_definition(args, prompt)
        elif agent_type_str == "fork":
            return self._run_fork(prompt)
        else:
            return f"未知 type 值「{agent_type_str}」，应为 'definition' 或 'fork'。"

    # ------------------------------------------------------------------ #
    # 私有分流方法
    # ------------------------------------------------------------------ #

    def _run_definition(self, args: dict, prompt: str) -> str:
        """definition 路径：查 registry → 前台 runner 或后台 manager.submit。

        - registry 查无此名 → 返「无此角色：<name>」（N50 软化，绝不崩）。
        - background=True → manager.submit，立即返「任务 id=X 已起」。
        - 默认前台 → 直接调用 runner，返 SubAgentResult.text（F94）。
        - manager=None + background=True → 返「agents 未启用」（N50 软化）。
        - runner=None + 前台 → 返「agents 未启用」（N50 软化）。
        """
        role_name = args.get("agent_type", "")
        agent_def: AgentDef | None = (
            self._registry.get(role_name) if self._registry is not None else None
        )
        if agent_def is None:
            return f"无此角色：{role_name}"

        background: bool = bool(args.get("background", False))

        if background:
            if self._manager is None:
                return "agents 未启用，无法派发后台任务"
            task_id = self._manager.submit(agent_def, prompt, background=True)
            return f"任务 id={task_id} 已起"

        # 前台同步——直接调用 runner，不经 manager（F94）
        if self._runner is None:
            return "agents 未启用，无法执行前台子 Agent 任务"
        result = self._runner(agent_def, prompt)
        return result.text

    def _run_fork(self, prompt: str) -> str:
        """fork 路径：构造占位 AgentDef，恒后台 manager.submit（AgentType.FORK，F95）。

        v0.13 · T145 装配：若 get_parent_messages 已注入，将父历史经
        parent_messages 传入 manager.submit → runner → run_subagent（F95/AC120）。
        manager=None → 返「agents 未启用」（N50 软化）。
        """
        if self._manager is None:
            return "agents 未启用，无法派发 fork 子 Agent"

        synthetic_def = AgentDef(
            name="fork",
            description="fork 当前上下文",
            body="",
        )

        extra_kw: dict[str, Any] = {}
        if self._get_parent_messages is not None:
            extra_kw["parent_messages"] = self._get_parent_messages()

        task_id = self._manager.submit(
            synthetic_def,
            prompt,
            agent_type=AgentType.FORK,
            **extra_kw,
        )
        return f"任务 id={task_id} 已起"
