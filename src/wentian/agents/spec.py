"""v0.13 · C108 · F91/F92/F98 — sub-Agent dispatch data model.

Pure stdlib leaf: AgentType / TaskStatus enums and AgentDef / BackgroundTask dataclasses.
No imports from wentian.repl / cli / agent / providers / tools.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class AgentType(Enum):
    """Dispatch type for Agents."""

    DEFINITION = "definition"  # F91 — dispatch by named AgentDef
    FORK = "fork"  # F92 — fork current context


class TaskStatus(Enum):
    """Execution status of background Agent tasks."""

    RUNNING = "running"  # F98
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class AgentDef:
    """Agent definition (immutable). tools=None means no restriction on the tool allowlist."""

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
    """Background Agent task (mutable), tracks the lifecycle of a single dispatch."""

    id: str
    kind: AgentType
    label: str
    status: TaskStatus
    result: Any
    usage: dict[str, Any]
    prompt: str
    created_at: float
