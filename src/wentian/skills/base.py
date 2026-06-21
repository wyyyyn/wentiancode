"""v0.11 · C100 · F84（任务 T126）— Skill 系统的数据模型（叶子模块）。

本模块是 Skill 系统的纯叶子：只依赖 stdlib（``dataclasses`` / ``enum`` /
``typing``），零业务 import——不引入 rich / prompt_toolkit / 任何后端 SDK，
也不反向依赖 wentian.agent / wentian.repl / wentian.providers / wentian.commands。

定义两类符号：

- ``SkillMode``：Skill 的运行模式（共享当前对话 / 独立子对话）。
- ``Skill``：单个 Skill 的不可变数据模型（frozen dataclass）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SkillMode(Enum):
    """Skill 运行模式。"""

    SHARED = "shared"
    """共享当前对话：激活进集、每轮注入正文、收窄白名单、结果留主历史（默认）。"""

    ISOLATED = "isolated"
    """独立子对话：worker 线程跑嵌套 AgentLoop、末条助手正文回流为工具结果。"""


@dataclass(frozen=True)
class Skill:
    """单个 Skill 的不可变数据模型。"""

    name: str
    """唯一标识、无斜杠、小写（slash 命令名来源）。"""

    description: str
    """一句话，进「可用 Skill」菜单 + /help。"""

    body: str
    """Markdown 正文（SOP；含未替换的 $ARGUMENTS/$1 占位符）。"""

    mode: SkillMode = SkillMode.SHARED
    """运行模式，缺省 SHARED。"""

    allowed_tools: tuple[str, ...] | None = None
    """None ⇒ 不收窄；空 tuple 也视为不收窄（由上层规则处理）。"""

    history: int = 0
    """仅 isolated：带多少条主历史进子对话。"""

    model: str | None = None
    """模型覆盖（缺省复用当前）。"""

    source: str = "builtin"
    """来源层："project" / "user" / "builtin"。"""
