"""v0.13 · C112 · F94/F95/F96/F100（任务 T141）— 子 Agent 执行器 run_subagent。

子 Agent 委派的**执行心脏**：装配一个**完全隔离**的 :class:`~wentian.agent.loop.AgentLoop`，
在独立 worker 线程内跑到底，收末条助手正文 + usage + 停机原因为
:class:`SubAgentResult`。

本模块住 **agents 编排层**（不在纯叶子 ``filter`` / ``spec`` 旁）——它**可以**
import ``wentian.agent`` / ``wentian.providers`` / ``wentian.permission_gate``，
因为嵌套 ``AgentLoop`` 的装配刻意下沉到这里。它**不 import** ``wentian.repl`` /
``wentian.cli``（避免成环 + 保持并发改写纪律）；主对话的 provider 配置 / 工具
注册中心 / 执行器 / 权限流水线都靠**入参注入**取得。

结构镜像 v0.11 ``skill_activator._run_isolated``：worker ``threading.Thread`` +
``asyncio.run(self._drive(...))`` 避免与调用方所在线程已有的事件循环嵌套冲突；
``_build_loop`` 留 ``loop_factory`` 注入缝供测试；``_drive`` 异步消费
``agent.run(...)``、捕获终值 ``AgentDone`` 取 ``.text``。

隔离铁律（N53）：**每次调用**都新造 provider 实例、新 :class:`Mode`、新权限门、
新 messages——绝不跨调用共享可变状态。每个子 Agent 拿自己的门，绝不复用主 REPL 的。

错误软化（N54）：整个 drive 包在 try/except，并检查 ``AgentDone`` 停机原因；
MAX_ROUNDS / STREAM_ERROR / UNKNOWN_TOOL_LOOP / 任何异常 → 转结构化
``SubAgentResult``，**绝不让异常逃逸 run_subagent**。

嵌套防护：``resolve_allowed_tools`` 的全局禁默认剥掉 ``"Agent"``；外加 depth 守卫
(``depth >= 1`` 拒绝再 spawn)——双保险。
"""

from __future__ import annotations

import asyncio
import dataclasses
import threading
from collections.abc import Callable
from dataclasses import dataclass

from wentian.agent.events import AgentDone, StopReason
from wentian.agent.loop import AgentLoop
from wentian.agents.filter import resolve_allowed_tools
from wentian.agents.spec import AgentDef
from wentian.config import ProviderConfig
from wentian.permissions.decision import Mode
from wentian.providers.base import Message, Usage

__all__ = ["SubAgentResult", "run_subagent"]

#: ``inherit`` 别名映射到的哨兵——保持父对话 model 不变。
_INHERIT_SENTINEL = "__inherit__"

#: 子 Agent 最大递归深度：>=1 即拒绝再 spawn（与全局禁 Agent 双保险）。
_MAX_DEPTH = 1


@dataclass
class SubAgentResult:
    """单次子 Agent 委派的结果（正文 + token 用量 + 停机原因串）。

    ``stop_reason`` 取 :class:`~wentian.agent.events.StopReason` 的 ``name``
    （``"COMPLETED"`` / ``"MAX_ROUNDS"`` / …），或装配期前置失败的 ``"ERROR"``。
    """

    text: str
    usage: dict
    stop_reason: str


# ---------------------------------------------------------------------------
# 公共入口
# ---------------------------------------------------------------------------


def run_subagent(
    agent_def: AgentDef,
    prompt: str,
    *,
    base_provider_cfg: ProviderConfig,
    provider_factory: Callable[[ProviderConfig], object],
    registry: object | None,
    executor: object | None,
    pipeline: object | None,
    settings: object | None,
    model_aliases: dict[str, str],
    parent_messages: list[Message] | None = None,
    depth: int = 0,
    loop_factory: Callable[..., object] | None = None,
) -> SubAgentResult:
    """装配一个隔离 AgentLoop 跑子 Agent 到底，返回结构化结果。

    流程（brief T141）：

    1. **model 别名解析**：``inherit`` → 保留 ``base_provider_cfg.model``；映射到
       具体 model → 用之；别名表里查不到 → 提前返回 ``ERROR``（**绝不空起 loop**）。
    2. **新 provider 实例**：``dataclasses.replace(base_cfg, model=mapped)`` →
       ``provider_factory(cfg)``（工厂让测试注入假 provider）。
    3. **隔离权限**：新 :class:`Mode`（取 ``agent_def.permission_mode`` 或安全默认
       :data:`Mode.DEFAULT`）+ 新 ``build_permission_gate``——每个子 Agent 拿自己的门。
    4. **允许集**：``resolve_allowed_tools(...)``，全局禁 ``"Agent"``（嵌套防护）。
       definition → 起始 ``[{"role":"user","content":prompt}]``、system =
       ``agent_def.body``；fork → ``parent_messages + [user prompt]``。
    5. **depth 守卫**：``depth >= 1`` → 拒绝 spawn，返回结构化 ``ERROR``。
    6. worker 线程内 ``asyncio.run(_drive(...))`` 跑到底，取 ``AgentDone``。
    7. **错误软化**：异常 + MAX_ROUNDS/STREAM_ERROR/UNKNOWN_TOOL_LOOP → 结构化
       结果，绝不让异常逃逸。
    """
    # --- 5. depth 守卫（与全局禁 Agent 双保险，最先判，绝不 spawn） ---
    if depth >= _MAX_DEPTH:
        return SubAgentResult(
            text=(
                f"子 Agent `{agent_def.name}` 已达最大委派深度（depth={depth}），"
                "为防无界嵌套拒绝再派发。"
            ),
            usage={},
            stop_reason="ERROR",
        )

    # --- 1. model 别名解析（不可解析 → 提前返报错，绝不空起 loop） ---
    mapped, alias_error = _resolve_model(
        agent_def.model, base_provider_cfg, model_aliases
    )
    if alias_error is not None:
        return SubAgentResult(text=alias_error, usage={}, stop_reason="ERROR")

    # --- 2. 新 provider 实例（dataclasses.replace + 注入工厂） ---
    cfg = dataclasses.replace(base_provider_cfg, model=mapped)
    provider = provider_factory(cfg)

    # --- 4. 起始消息 + 子 system（按 definition / fork 分流） ---
    seed, system = _build_seed(agent_def, prompt, parent_messages)

    # --- 4. 允许集（全局禁 Agent）---
    all_tools = _all_tool_names(registry)
    allowed = resolve_allowed_tools(
        all_tools,
        role_allow=agent_def.tools,
        role_deny=agent_def.disallowed_tools,
    )

    # --- 3. 隔离权限门（每个子 Agent 自己的 Mode + gate） ---
    gate = _build_isolated_gate(agent_def, registry, pipeline, settings)

    # --- 6+7. worker 线程跑到底 + 错误软化 ---
    holder: dict[str, SubAgentResult] = {}

    def _worker() -> None:
        try:
            agent = _build_loop(
                provider,
                registry=registry,
                executor=executor,
                allowed=allowed,
                gate=gate,
                max_turns=agent_def.max_turns,
                loop_factory=loop_factory,
            )
            holder["result"] = asyncio.run(_drive(agent, seed, system))
        except Exception as exc:  # noqa: BLE001 — N54：异常绝不逃逸 run_subagent
            holder["result"] = SubAgentResult(
                text=f"因 EXCEPTION 停止：{exc}",
                usage={},
                stop_reason="STREAM_ERROR",
            )

    thread = threading.Thread(target=_worker, name=f"subagent-{agent_def.name}")
    thread.start()
    thread.join()  # 同步等待：返回时 worker 已收束（无泄漏）。

    return holder.get(
        "result",
        SubAgentResult(text="子 Agent 未产出结果。", usage={}, stop_reason="ERROR"),
    )


# ---------------------------------------------------------------------------
# 私有装配步骤
# ---------------------------------------------------------------------------


def _resolve_model(
    alias: str,
    base_cfg: ProviderConfig,
    model_aliases: dict[str, str],
) -> tuple[str, str | None]:
    """解析 model 别名 → ``(mapped_model, error_msg)``。

    - ``inherit``（映射到哨兵 ``__inherit__``）→ 保留 ``base_cfg.model``，无错。
    - 别名在表里且非哨兵 → 用映射目标，无错。
    - 别名**不在表里** → ``(base_cfg.model, "别名 <x> 不可解析…")``——调用方据此
      提前返回、绝不空起 loop。
    """
    if alias not in model_aliases:
        return base_cfg.model, (
            f"别名 {alias} 不可解析：未在 model_aliases 中定义"
            f"（可用：{', '.join(sorted(model_aliases))}）。"
        )
    mapped = model_aliases[alias]
    if mapped == _INHERIT_SENTINEL:
        return base_cfg.model, None
    return mapped, None


def _build_seed(
    agent_def: AgentDef,
    prompt: str,
    parent_messages: list[Message] | None,
) -> tuple[list[Message], str]:
    """构造子对话起始消息 + 子 system（按 definition / fork 分流）。

    **派发类型由 ``parent_messages`` 携带**（``AgentDef`` 本身不带 type 字段——
    type 是派发期决策，由调用方据 :class:`~wentian.agents.spec.AgentType` 决定是否
    传父历史）：

    - **definition**（``parent_messages`` 为 None/空）：起始 =
      ``[{"role":"user","content":prompt}]``、system = ``agent_def.body``。
    - **fork**（``parent_messages`` 非空）：起始 = ``parent_messages 的浅拷贝 +
      [user prompt]``（绝不原地改父历史）、system = ``agent_def.body``。
    """
    user_turn: Message = {"role": "user", "content": prompt}
    if parent_messages:
        # 浅拷贝每条 + 追加触发 user：父历史绝不被原地改动（隔离）。
        seed: list[Message] = [dict(m) for m in parent_messages]
        seed.append(user_turn)
    else:
        seed = [user_turn]
    return seed, agent_def.body


def _all_tool_names(registry: object | None) -> set[str]:
    """从注册中心取全量工具名（``names()`` 鸭子方法）；无 registry → 空集。

    空集时 ``resolve_allowed_tools`` 仍会施加全局禁（无副作用），允许集为空——
    与「纯对话子循环」一致（无工具可调）。
    """
    if registry is None:
        return set()
    names = getattr(registry, "names", None)
    if callable(names):
        return set(names())
    return set()


def _build_isolated_gate(
    agent_def: AgentDef,
    registry: object | None,
    pipeline: object | None,
    settings: object | None,  # noqa: ARG001 — 预留：未来按 agent 覆盖 settings
):
    """构造**该子 Agent 专属**的权限门，或 None（无 pipeline ⇒ 不设门）。

    每个子 Agent 拿自己的 :class:`Mode` 与 ``build_permission_gate`` 闭包——绝不
    复用主 REPL 的门（隔离铁律）。子 Agent 无人值守：``ask`` 回调走**安全默认
    拒绝**（任何 ASK 裁决一律 Deny，绝不阻塞等人）。``get_mode`` 返回这次新造的
    固定 Mode（取 ``permission_mode`` 或 :data:`Mode.DEFAULT`）。
    """
    if pipeline is None:
        return None

    # 装配层 import（permission_gate 跨层、可 import permissions+tools+ui）。
    from wentian.permission_gate import build_permission_gate
    from wentian.ui.confirm import Choice

    mode = _resolve_mode(agent_def.permission_mode)

    def get_mode() -> Mode:
        return mode

    async def ask(_call, _decision) -> Choice:
        # 无人值守子 Agent：ASK 一律安全默认拒绝（绝不阻塞等待人工确认）。
        return Choice.DENY

    return build_permission_gate(
        pipeline=pipeline,
        registry=registry,
        ask=ask,
        get_mode=get_mode,
        on_allow_always=None,  # 子 Agent 绝不持久化 allow-always 规则。
    )


def _resolve_mode(permission_mode: str | None) -> Mode:
    """把 ``agent_def.permission_mode``（配置字符串）解析为 :class:`Mode`。

    缺失 / 不可识别 → 安全默认 :data:`Mode.DEFAULT`。
    """
    if permission_mode is None:
        return Mode.DEFAULT
    try:
        return Mode(permission_mode)
    except ValueError:
        return Mode.DEFAULT


def _build_loop(
    provider: object,
    *,
    registry: object | None,
    executor: object | None,
    allowed: frozenset[str],
    gate,
    max_turns: int | None,
    loop_factory: Callable[..., object] | None,
):
    """装配隔离 AgentLoop（测试可经 ``loop_factory`` 注入假驱动循环）。

    ``max_turns`` 缺失 → 沿用 AgentLoop 默认 ``max_rounds=20``。无 registry/executor
    ⇒ 纯对话子循环（声明 ≠ 执行；任何工具请求会在 max_rounds 处刹车）。
    """
    kwargs: dict = {
        "registry": registry,
        "executor": executor,
        "allowed_tools": allowed,
        "permission_gate": gate,
    }
    if max_turns is not None:
        kwargs["max_rounds"] = max_turns

    if loop_factory is not None:
        return loop_factory(provider, **kwargs)
    return AgentLoop(provider, **kwargs)


async def _drive(agent, seed: list[Message], system: str) -> SubAgentResult:
    """驱动子对话事件流，把终值 ``AgentDone`` 转 :class:`SubAgentResult`（含软化）。

    镜像 ``skill_activator._drive`` + repl ``_consume_agent``：异步消费
    ``agent.run(...)``、捕获 ``AgentDone``。``AgentDone.text`` 即 loop 写入末条
    助手消息的同一份正文。停机原因若非 COMPLETED（MAX_ROUNDS/STREAM_ERROR/
    UNKNOWN_TOOL_LOOP/USER_CANCELLED）→ 文本前缀「因 <REASON> 停止…」软化（N54）。
    """
    final: AgentDone | None = None
    async for ev in agent.run(seed, system=system, tools=None):
        if isinstance(ev, AgentDone):
            final = ev

    if final is None:
        # 事件流未产出 AgentDone（异常已被 loop 吞或流为空）——按错误软化。
        return SubAgentResult(
            text="子对话未正常收束（无 AgentDone 事件）。",
            usage={},
            stop_reason="STREAM_ERROR",
        )

    reason = final.stop_reason
    usage_dict = _usage_to_dict(final.usage)

    if reason is StopReason.COMPLETED:
        return SubAgentResult(
            text=final.text, usage=usage_dict, stop_reason=reason.name
        )

    # 非正常收束 → 软化为结构化结果（带停机原因 + 已有部分正文）。
    partial = final.text or ""
    detail = f"：{final.error}" if final.error else ""
    return SubAgentResult(
        text=f"因 {reason.name} 停止{detail}。{partial}".rstrip(),
        usage=usage_dict,
        stop_reason=reason.name,
    )


def _usage_to_dict(usage: Usage | None) -> dict:
    """把 :class:`~wentian.providers.base.Usage` 转 dict；None → ``{}``。"""
    if usage is None:
        return {}
    return dataclasses.asdict(usage)
