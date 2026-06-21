"""v0.13 · C108 · F91/F92/F98 — 子 Agent 委派数据模型。

Pure stdlib leaf: AgentType / TaskStatus enums and AgentDef / BackgroundTask dataclasses.
No imports from wentian.repl / cli / agent / providers / tools.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class AgentType(Enum):
    """Agent 的派发类型。"""

    DEFINITION = "definition"  # F91 — 按命名 AgentDef 派发
    FORK = "fork"  # F92 — fork 当前上下文


class TaskStatus(Enum):
    """后台 Agent 任务的执行状态。"""

    RUNNING = "running"  # F98
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class AgentDef:
    """Agent 定义（不可变）。工具白名单 tools=None 表示不受限。"""

    name: str
    description: str
    body: str
    tools: tuple[str, ...] | None = None
    disallowed_tools: tuple[str, ...] = ()
    model: str = "inherit"
    max_turns: int | None = None
    permission_mode: str | None = None
    source: str = "builtin"


@dataclass
class BackgroundTask:
    """后台 Agent 任务（可变），跟踪单次委派的生命周期。"""

    id: str
    kind: AgentType
    label: str
    status: TaskStatus
    result: Any
    usage: dict[str, Any]
    prompt: str
    created_at: float
