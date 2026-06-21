"""v0.11 · C104 · F87/F89（任务 T133）— SkillActivator：Skill 激活的唯一编排点。

本模块住 **repl 装配层**（不在 ``skills/`` 叶子包里），因此**可以** import
``wentian.agent`` / ``wentian.skills`` / ``wentian.providers``——isolated 子对话
的嵌套 ``AgentLoop`` 编排刻意下沉到这里，让 ``skills`` 包保持零 agent/provider/
repl 依赖。它**不 import** ``wentian.repl``（避免成环）——主 session 的 messages /
system / provider / tool_registry / executor 都靠构造期注入的句柄与回调取得。

``load_skill`` 工具与 ``/<name>`` 命令共用这一个入口：

- **SHARED**：``render_body(skill.body, args)`` → 把 ``(name, rendered,
  allowed_tools)`` 加进激活集（去重按 name、重复激活刷新 args）→ 返回确认串。
  正文经 ``active_bodies()`` 每轮 live 喂 reminder 通道；白名单经
  ``allowed_tools()`` 喂 AgentLoop 装配。
- **ISOLATED**：在**独立 worker 线程**内 ``asyncio.run`` 一个全新 ``AgentLoop``
  跑嵌套子对话（末 ``skill.history`` 条主历史 + 子 system = 主 system + Skill
  正文 + 子工具 = 该 Skill 白名单 + 子 model = ``skill.model or 当前``）→ 收集
  子对话**末条助手正文**作返回值。**不进激活集**、不碰主历史 / 主白名单。

worker 线程自起事件循环，避免与主 ``_chat_once`` 的 ``asyncio.run`` 嵌套冲突
（无论 ``activate`` 由执行器工作线程还是 REPL 主线程触发都安全）。
"""

from __future__ import annotations

import asyncio
import copy
import threading
from collections.abc import Callable

from wentian.agent.events import AgentDone
from wentian.agent.loop import AgentLoop
from wentian.providers.base import Message
from wentian.skills.base import Skill, SkillMode
from wentian.skills.loader import render_body
from wentian.skills.registry import SkillRegistry

__all__ = ["SkillActivator"]

# ``load_skill`` 工具恒并入收窄白名单——即便激活集把工具收窄，模型也始终能继续
# 加载 / 切换 Skill（系统级豁免，见 plan C103/C104）。
_LOAD_SKILL = "load_skill"


class SkillActivator:
    """Skill 激活的唯一编排点（SHARED 进集 / ISOLATED 子对话）。"""

    def __init__(
        self,
        registry: SkillRegistry,
        *,
        provider,
        tool_registry,
        executor,
        get_main_messages: Callable[[], list[Message]],
        get_main_system: Callable[[], str],
        loop_factory: Callable[..., AgentLoop] | None = None,
    ) -> None:
        self._registry = registry
        self._provider = provider
        self._tool_registry = tool_registry
        self._executor = executor
        self._get_main_messages = get_main_messages
        self._get_main_system = get_main_system
        self._loop_factory = loop_factory
        # 激活集（仅 SHARED）：插入序 = 激活序，去重按 name（重复激活刷新 args）。
        # name -> (rendered_body, allowed_tools)
        self._active: dict[str, tuple[str, tuple[str, ...] | None]] = {}

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    def activate(self, name: str, args: str) -> str:
        """``load_skill`` 工具与 ``/<name>`` 命令的统一入口。

        未知 name → 返回清晰错误串（不抛）。SHARED → 进集 + 返回确认串。
        ISOLATED → 跑子对话、返回子对话末条助手正文（verbatim）。
        """
        skill = self._registry.get(name)
        if skill is None:
            return f"未找到 Skill `{name}`，请检查名称（可用 /skills 查看已加载列表）。"

        if skill.mode is SkillMode.ISOLATED:
            return self._run_isolated(skill, args)

        # SHARED：渲正文 → 进集（去重按 name、刷新 args）→ 确认串。
        rendered = render_body(skill.body, args)
        # 先删后插，保证「刷新」时也回到激活序末尾（dict 保留插入序）。
        self._active.pop(name, None)
        self._active[name] = (rendered, skill.allowed_tools)
        return f"已激活 Skill `{name}`，指令已注入上下文。"

    def active_bodies(self) -> list[tuple[str, str]]:
        """``[(name, rendered_body), ...]``，按激活序——reminders 每轮 live 读。"""
        return [(name, body) for name, (body, _tools) in self._active.items()]

    def allowed_tools(self) -> frozenset[str] | None:
        """激活集白名单并集 ∪ ``{load_skill}``；空集 / 任一不限 → None。

        - 激活集空 → None（不收窄、全量工具）。
        - 任一激活 Skill 的 ``allowed_tools`` 为 None 或空 tuple → None
          （一个不受限的 Skill 即意味着不收窄）。
        - 否则 → ``frozenset(所有白名单并集) | {load_skill}``。
        """
        if not self._active:
            return None
        union: set[str] = set()
        for _name, (_body, tools) in self._active.items():
            if not tools:  # None 或空 tuple
                return None
            union.update(tools)
        union.add(_LOAD_SKILL)
        return frozenset(union)

    def clear(self) -> None:
        """清空激活集（``/clear`` // ``session new`` 调）。"""
        self._active.clear()

    # ------------------------------------------------------------------
    # ISOLATED 子对话
    # ------------------------------------------------------------------

    def _run_isolated(self, skill: Skill, args: str) -> str:
        """在独立 worker 线程内跑嵌套 AgentLoop，返回子对话末条助手正文。

        子对话起始 = 主历史末 ``skill.history`` 条的深拷贝（绝不改主历史）；
        若起始为空或末条非 user，则追加触发 user（``args`` 非空用 args，否则
        ``f"执行 {name}"``）——子 agent 需要一个 user 轮才会动作。子 system =
        主 system + ``# Skill: <name>\\n<rendered>``；子 tools 白名单 = 该 Skill
        ``allowed_tools``（None ⇒ 全量）；子 model = ``skill.model or 当前``。

        worker 线程自起事件循环（``asyncio.run``），避免与调用方所在线程已有的
        事件循环冲突。
        """
        rendered = render_body(skill.body, args)

        # --- 子起始历史：主历史末 history 条深拷贝（永不改主历史） ---
        main_messages = self._get_main_messages()
        if skill.history > 0:
            tail = main_messages[-skill.history :]
        else:
            tail = []
        seed: list[Message] = copy.deepcopy(tail)

        # 子 agent 需要 user 轮才会动作：空或末条非 user → 追加触发 user。
        if not seed or seed[-1].get("role") != "user":
            trigger = args if args else f"执行 {skill.name}"
            seed.append({"role": "user", "content": trigger})

        # --- 子 system ---
        main_system = self._get_main_system() or ""
        sub_system = f"{main_system}\n\n# Skill: {skill.name}\n{rendered}"

        # --- 子工具白名单（同 SHARED 规则，但仅此一个 Skill）：
        #     allowed_tools None/空 ⇒ 全量（不收窄） ---
        sub_allowed: frozenset[str] | None
        if skill.allowed_tools:
            sub_allowed = frozenset(skill.allowed_tools) | {_LOAD_SKILL}
        else:
            sub_allowed = None

        # 注：tools 声明与 model 由装配层在 loop_factory / provider 内决定；
        # 本编排只负责驱动循环并取末条助手正文。skill.model 经 loop_factory
        # 传递（默认工厂用注入的 provider，model 覆盖留给装配层接线）。

        result: dict[str, str] = {"text": ""}

        def _worker() -> None:
            agent = self._build_loop(sub_allowed, skill)
            result["text"] = asyncio.run(self._drive(agent, seed, sub_system))

        thread = threading.Thread(target=_worker, name=f"skill-isolated-{skill.name}")
        thread.start()
        thread.join()  # 同步等待：activate 返回时 worker 已收束（无泄漏）。
        return result["text"]

    def _build_loop(
        self, sub_allowed: frozenset[str] | None, skill: Skill
    ) -> AgentLoop:
        """构造子对话用的 AgentLoop（测试可经 loop_factory 注入假驱动循环）。

        无 tool_registry / executor（纯对话子循环）⇒ ``max_rounds=1``：若子模型仍
        请求工具，则首轮即 MAX_ROUNDS 刹车、只存文本（与 repl 的 executor 缺席
        决策同构）。有 registry/executor ⇒ 放开多轮，子工具按 ``sub_allowed`` 收窄。
        """
        if self._loop_factory is not None:
            return self._loop_factory(
                self._provider,
                registry=self._tool_registry,
                executor=self._executor,
                allowed_tools=sub_allowed,
                skill=skill,
            )

        tools_enabled = self._tool_registry is not None and self._executor is not None
        if tools_enabled:
            return AgentLoop(
                self._provider,
                registry=self._tool_registry,
                executor=self._executor,
                allowed_tools=sub_allowed,
            )
        # 纯对话子循环：声明 ≠ 执行；max_rounds=1 让任何工具请求首轮即刹车。
        return AgentLoop(
            self._provider,
            registry=None,
            executor=None,
            max_rounds=1,
            allowed_tools=None,
        )

    @staticmethod
    async def _drive(agent: AgentLoop, seed: list[Message], system: str) -> str:
        """驱动子对话事件流，提取末条助手正文。

        镜像 repl ``_consume_agent``：消费 ``agent.run(...)`` 的异步事件流、把
        ``AgentDone`` 捕获为终值——``AgentDone.text`` 即 loop COMPLETED 路径下
        写入末条助手消息的同一份正文（``messages.append({"role":"assistant",
        "content": round_result.text})``）。子对话不碰主 session、主激活集、主
        白名单——它只在本地 ``seed`` 上原地变更（已是深拷贝），结果只取 text。
        """
        final: AgentDone | None = None
        async for ev in agent.run(seed, system=system, tools=None):
            if isinstance(ev, AgentDone):
                final = ev
        return final.text if final is not None else ""
