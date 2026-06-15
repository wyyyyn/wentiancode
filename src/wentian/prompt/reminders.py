"""动态提醒拼装 — EnvInfo / render_env_reminder / render_switch_reminder /
build_request_decorator.

v0.5 · C22（任务 T60）

本模块是纯函数层：
- 零后端 SDK 依赖（无 anthropic / openai）
- 零 rich / prompt_toolkit 依赖
- 仅 stdlib + wentian.providers.base（Message 类型）

语义：每次 LLM 请求发出前，由 build_request_decorator 返回的闭包将动态内容
（环境信息 + 会话开关提醒）以 <system-reminder> 标签注入消息流；注入结果仅
在本次请求有效，**永不持久化到会话历史**。

关键约束：decorator 绝不 mutate 入参列表及其中任何 Message dict 的 content
字段——提醒永不持久化的结构保证。
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wentian.providers.base import Message

__all__ = [
    "EnvInfo",
    "render_env_reminder",
    "render_switch_reminder",
    "build_request_decorator",
]


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EnvInfo:
    """本次请求时的环境快照，用于生成环境信息提醒块。

    Attributes:
        cwd:        当前工作目录。
        os:         操作系统标识字符串（如 ``"Darwin 25.0"``）。
        date:       日期字符串（如 ``"2026-06-15"``）。
        git_branch: 当前 Git 分支名；若非 git 仓库则为 ``None``。
    """
    cwd: Path
    os: str
    date: str
    git_branch: str | None


# ---------------------------------------------------------------------------
# 渲染函数
# ---------------------------------------------------------------------------

def render_env_reminder(env: EnvInfo) -> str:
    """返回包含 <system-reminder> 标签的环境信息块。

    包含四项：工作目录 / 操作系统 / 日期 / Git 分支。
    ``git_branch`` 为 ``None`` 时显示「（非 git 仓库）」。
    """
    branch_str = env.git_branch if env.git_branch is not None else "（非 git 仓库）"
    body = (
        f"工作目录：{env.cwd}\n"
        f"操作系统：{env.os}\n"
        f"日期：{env.date}\n"
        f"Git 分支：{branch_str}"
    )
    return f"<system-reminder>\n{body}\n</system-reminder>"


# 完整计划模式提醒的固定文案
_PLAN_MODE_FULL = (
    "<system-reminder>\n"
    "当前处于计划模式（Plan Mode）。\n"
    "规则：只读勘察环境与代码库；先给出清晰的分步执行计划再停下，等待用户确认；"
    "不做任何修改（不写文件、不执行副作用命令）。\n"
    "用户将输入 /do 切换到执行模式后再开始实施。\n"
    "</system-reminder>"
)

# 精简计划模式提醒（后续回合使用）
_PLAN_MODE_BRIEF = (
    "<system-reminder>"
    "（仍在计划模式：只读勘察、暂不改动）"
    "</system-reminder>"
)


def render_switch_reminder(
    *,
    plan_mode: bool,
    round_index: int,
    repeat_every: int = 5,
) -> str | None:
    """会话开关提醒（当前只有计划模式）。

    - ``plan_mode=False`` → ``None``（不注入任何提醒）。
    - ``plan_mode=True``：

      - ``round_index == 1`` 或 ``(round_index - 1) % repeat_every == 0``
        → 返回**完整**提醒（含 <system-reminder> 标签）；
      - 否则 → 返回**精简**一行提醒（同样包 <system-reminder> 标签）。

    Args:
        plan_mode:    是否处于计划模式。
        round_index:  当前回合序号（从 1 开始）。
        repeat_every: 完整提醒重复间隔，默认 5 回合。

    Returns:
        提醒字符串，或 ``None``。
    """
    if not plan_mode:
        return None

    is_full = (round_index == 1) or ((round_index - 1) % repeat_every == 0)
    return _PLAN_MODE_FULL if is_full else _PLAN_MODE_BRIEF


# ---------------------------------------------------------------------------
# 请求装饰器工厂
# ---------------------------------------------------------------------------

def build_request_decorator(
    *,
    env: EnvInfo,
    plan_mode: bool,
    repeat_every: int = 5,
) -> Callable[[list[Message], int], list[Message]]:
    """返回请求时拼装闭包 ``decorator(messages, round_index) -> list[Message]``。

    闭包行为：
    1. 拷贝 ``messages``（不改动入参列表或其中任何 dict 的 content）。
    2. 在拷贝里**第一条** ``role == 'user'`` 的消息 content **前置** env 提醒。
    3. 在拷贝里**最后一条** ``role == 'user'`` 的消息 content **追加** switch 提醒
       （``render_switch_reminder`` 返回非 ``None`` 时）。
    4. 返回新列表。

    单轮场景下第一条 user 与最后一条 user 是同一条——该条同时被前置 env、
    追加 switch（顺序：env → 原 content → switch）。

    无 user 消息时安全返回拷贝，不抛错。

    Args:
        env:          环境信息快照。
        plan_mode:    是否处于计划模式。
        repeat_every: 传递给 ``render_switch_reminder`` 的重复间隔。

    Returns:
        闭包 ``(messages, round_index) -> list[Message]``。
    """
    env_reminder = render_env_reminder(env)

    def decorator(messages: list[Message], round_index: int) -> list[Message]:
        # 找到第一条和最后一条 user 消息的 index
        first_user_idx: int | None = None
        last_user_idx: int | None = None
        for i, msg in enumerate(messages):
            if msg.get("role") == "user":
                if first_user_idx is None:
                    first_user_idx = i
                last_user_idx = i

        switch_reminder = render_switch_reminder(
            plan_mode=plan_mode,
            round_index=round_index,
            repeat_every=repeat_every,
        )

        # 浅拷贝列表；只深拷贝需要修改 content 的那几条消息
        result: list[Message] = list(messages)

        if first_user_idx is not None:
            # last_user_idx is guaranteed set whenever first_user_idx is set
            assert last_user_idx is not None  # narrow type for checker

            # 需要修改 content 的 indices（单轮时两者相同，用 set 自动去重）
            indices_to_copy: set[int] = {first_user_idx}
            if switch_reminder is not None:
                indices_to_copy.add(last_user_idx)

            # copy.copy（浅拷贝）足够：只改 content 字符串，不改嵌套结构
            copied: dict[int, Message] = {
                idx: copy.copy(messages[idx]) for idx in indices_to_copy
            }

            # 前置 env 提醒到第一条 user
            copied[first_user_idx]["content"] = (  # type: ignore[index]
                env_reminder + "\n" + copied[first_user_idx]["content"]  # type: ignore[index]
            )

            # 追加 switch 提醒到最后一条 user（switch_reminder 非 None 时 last 已入 set）
            if switch_reminder is not None:
                copied[last_user_idx]["content"] = (  # type: ignore[index]
                    copied[last_user_idx]["content"] + "\n" + switch_reminder  # type: ignore[index]
                )

            # 写回结果列表
            for idx, msg in copied.items():
                result[idx] = msg

        return result

    return decorator
