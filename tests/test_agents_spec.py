"""v0.13 · C108 · F91/F92/F98（任务 T136）— AgentType / TaskStatus / AgentDef / BackgroundTask 单元测试。

TDD 红-绿-重构：先跑此文件确认因 agents.spec 模块缺失而失败，再实现转绿。
"""

from __future__ import annotations

import dataclasses

import pytest


# ---------------------------------------------------------------------------
# AgentType 枚举
# ---------------------------------------------------------------------------


def test_agent_type_members():
    """AgentType 有且仅有 DEFINITION / FORK 两个成员。"""
    from wentian.agents.spec import AgentType

    names = {m.name for m in AgentType}
    assert names == {"DEFINITION", "FORK"}


def test_agent_type_values():
    """AgentType 各成员的字符串 value 与规格一致。"""
    from wentian.agents.spec import AgentType

    assert AgentType.DEFINITION.value == "definition"
    assert AgentType.FORK.value == "fork"


# ---------------------------------------------------------------------------
# TaskStatus 枚举
# ---------------------------------------------------------------------------


def test_task_status_members():
    """TaskStatus 有且仅有 RUNNING / DONE / FAILED 三个成员。"""
    from wentian.agents.spec import TaskStatus

    names = {m.name for m in TaskStatus}
    assert names == {"RUNNING", "DONE", "FAILED"}


def test_task_status_values():
    """TaskStatus 各成员的字符串 value 与规格一致。"""
    from wentian.agents.spec import TaskStatus

    assert TaskStatus.RUNNING.value == "running"
    assert TaskStatus.DONE.value == "done"
    assert TaskStatus.FAILED.value == "failed"


# ---------------------------------------------------------------------------
# AgentDef dataclass（frozen=True）
# ---------------------------------------------------------------------------


def test_agent_def_full_construction():
    """AgentDef 可用所有字段完整构造，并按原样保留。"""
    from wentian.agents.spec import AgentDef

    agent = AgentDef(
        name="coder",
        description="写代码",
        body="你是…",
        tools=("read_file",),
        disallowed_tools=(),
        model="inherit",
        max_turns=None,
        permission_mode=None,
        source="builtin",
    )
    assert agent.name == "coder"
    assert agent.description == "写代码"
    assert agent.body == "你是…"
    assert agent.tools == ("read_file",)
    assert agent.disallowed_tools == ()
    assert agent.model == "inherit"
    assert agent.max_turns is None
    assert agent.permission_mode is None
    assert agent.source == "builtin"


def test_agent_def_frozen():
    """AgentDef 是 frozen dataclass，改字段抛 FrozenInstanceError。"""
    from wentian.agents.spec import AgentDef

    agent = AgentDef(
        name="coder",
        description="写代码",
        body="你是…",
        tools=("read_file",),
        disallowed_tools=(),
        model="inherit",
        max_turns=None,
        permission_mode=None,
        source="builtin",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        agent.name = "other"  # type: ignore[misc]


def test_agent_def_tools_none():
    """AgentDef 的 tools=None 合法（代表无白名单约束）。"""
    from wentian.agents.spec import AgentDef

    agent = AgentDef(
        name="planner",
        description="规划",
        body="规划任务",
        tools=None,
        disallowed_tools=(),
        model="inherit",
        max_turns=None,
        permission_mode=None,
        source="builtin",
    )
    assert agent.tools is None


def test_agent_def_defaults():
    """AgentDef 仅给必填字段构造时，各默认值符合规格。"""
    from wentian.agents.spec import AgentDef

    agent = AgentDef(name="x", description="d", body="b")
    assert agent.tools is None
    assert agent.disallowed_tools == ()
    assert agent.model == "inherit"
    assert agent.max_turns is None
    assert agent.permission_mode is None
    assert agent.source == "builtin"


# ---------------------------------------------------------------------------
# BackgroundTask dataclass（可变，非 frozen）
# ---------------------------------------------------------------------------


def test_background_task_construction():
    """BackgroundTask 可用全字段构造，并按原样保留。"""
    from wentian.agents.spec import AgentType, BackgroundTask, TaskStatus

    task = BackgroundTask(
        id="1",
        kind=AgentType.DEFINITION,
        label="coder",
        status=TaskStatus.RUNNING,
        result=None,
        usage={},
        prompt="帮我写…",
        created_at=0.0,
    )
    assert task.id == "1"
    assert task.kind is AgentType.DEFINITION
    assert task.label == "coder"
    assert task.status is TaskStatus.RUNNING
    assert task.result is None
    assert task.usage == {}
    assert task.prompt == "帮我写…"
    assert task.created_at == 0.0


def test_background_task_mutable():
    """BackgroundTask 是可变 dataclass：status / result / usage 可后续赋值。"""
    from wentian.agents.spec import AgentType, BackgroundTask, TaskStatus

    task = BackgroundTask(
        id="2",
        kind=AgentType.FORK,
        label="reviewer",
        status=TaskStatus.RUNNING,
        result=None,
        usage={},
        prompt="帮我审…",
        created_at=1.0,
    )
    task.status = TaskStatus.DONE
    task.result = "OK"
    task.usage = {"input_tokens": 100, "output_tokens": 50}

    assert task.status is TaskStatus.DONE
    assert task.result == "OK"
    assert task.usage == {"input_tokens": 100, "output_tokens": 50}


def test_background_task_not_frozen():
    """BackgroundTask 不是 frozen dataclass（改字段不抛异常）。"""
    from wentian.agents.spec import AgentType, BackgroundTask, TaskStatus

    task = BackgroundTask(
        id="3",
        kind=AgentType.DEFINITION,
        label="test",
        status=TaskStatus.RUNNING,
        result=None,
        usage={},
        prompt="test",
        created_at=2.0,
    )
    # Should not raise
    task.label = "changed"
    assert task.label == "changed"
