"""v0.12 · C94 · F77/F78/F81/F82（任务 T118）— HookEvent / Action / HookRule 的单元测试。

TDD 红-绿-重构：先跑此文件确认因功能缺失而失败，再实现 hooks/spec.py 转绿。
"""

from __future__ import annotations

import dataclasses

import pytest


# ---------------------------------------------------------------------------
# HookEvent 枚举 —— 十个成员，values 为字符串
# ---------------------------------------------------------------------------


def test_hook_event_member_count():
    """HookEvent 有且仅有 10 个成员。"""
    from wentian.hooks.spec import HookEvent

    assert len(HookEvent) == 10


def test_hook_event_member_names():
    """HookEvent 的十个成员名称完整且无多余。"""
    from wentian.hooks.spec import HookEvent

    expected = {
        "SESSION_START",
        "SESSION_END",
        "USER_PROMPT_SUBMIT",
        "STOP",
        "ROUND_START",
        "ROUND_END",
        "PRE_TOOL_USE",
        "POST_TOOL_USE",
        "PRE_COMPACT",
        "NOTIFICATION",
    }
    assert {m.name for m in HookEvent} == expected


def test_hook_event_values():
    """HookEvent 各成员的字符串 value 与规格一致。"""
    from wentian.hooks.spec import HookEvent

    assert HookEvent.SESSION_START.value == "SessionStart"
    assert HookEvent.SESSION_END.value == "SessionEnd"
    assert HookEvent.USER_PROMPT_SUBMIT.value == "UserPromptSubmit"
    assert HookEvent.STOP.value == "Stop"
    assert HookEvent.ROUND_START.value == "RoundStart"
    assert HookEvent.ROUND_END.value == "RoundEnd"
    assert HookEvent.PRE_TOOL_USE.value == "PreToolUse"
    assert HookEvent.POST_TOOL_USE.value == "PostToolUse"
    assert HookEvent.PRE_COMPACT.value == "PreCompact"
    assert HookEvent.NOTIFICATION.value == "Notification"


# ---------------------------------------------------------------------------
# INTERCEPT_EVENTS —— 含且仅含 PRE_TOOL_USE
# ---------------------------------------------------------------------------


def test_intercept_events_is_frozenset():
    """INTERCEPT_EVENTS 是 frozenset。"""
    from wentian.hooks.spec import INTERCEPT_EVENTS

    assert isinstance(INTERCEPT_EVENTS, frozenset)


def test_intercept_events_contains_only_pre_tool_use():
    """INTERCEPT_EVENTS 含且仅含 HookEvent.PRE_TOOL_USE（AC95 局部）。"""
    from wentian.hooks.spec import HookEvent, INTERCEPT_EVENTS

    assert INTERCEPT_EVENTS == frozenset({HookEvent.PRE_TOOL_USE})


# ---------------------------------------------------------------------------
# Match 枚举 —— 两个成员
# ---------------------------------------------------------------------------


def test_match_members():
    """Match 有且仅有 ALL / ANY 两个成员，values 为 'all'/'any'。"""
    from wentian.hooks.spec import Match

    assert len(Match) == 2
    assert Match.ALL.value == "all"
    assert Match.ANY.value == "any"


# ---------------------------------------------------------------------------
# Clause dataclass —— frozen，两个必填字段
# ---------------------------------------------------------------------------


def test_clause_construction():
    """Clause(field, pattern) 可正常构造，字段值可读取。"""
    from wentian.hooks.spec import Clause

    c = Clause(field="tool_name", pattern="Bash")
    assert c.field == "tool_name"
    assert c.pattern == "Bash"


def test_clause_frozen():
    """Clause 是 frozen dataclass，修改字段应抛 FrozenInstanceError。"""
    from wentian.hooks.spec import Clause

    c = Clause(field="tool_name", pattern="Bash")
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.field = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Condition dataclass —— frozen，带默认值
# ---------------------------------------------------------------------------


def test_condition_defaults():
    """Condition() 无参构造时默认 match=Match.ALL、clauses=()。"""
    from wentian.hooks.spec import Condition, Match

    cond = Condition()
    assert cond.match is Match.ALL
    assert cond.clauses == ()


def test_condition_custom():
    """Condition 接受自定义 match 和 clauses 元组。"""
    from wentian.hooks.spec import Clause, Condition, Match

    c1 = Clause(field="command", pattern="/rm/")
    c2 = Clause(field="tool_name", pattern="!Bash")
    cond = Condition(match=Match.ANY, clauses=(c1, c2))
    assert cond.match is Match.ANY
    assert len(cond.clauses) == 2
    assert cond.clauses[0] is c1
    assert cond.clauses[1] is c2


def test_condition_frozen():
    """Condition 是 frozen dataclass，修改字段应抛 FrozenInstanceError。"""
    from wentian.hooks.spec import Condition

    cond = Condition()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cond.match = None  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Action dataclasses —— 四种，frozen，必填与默认字段
# ---------------------------------------------------------------------------


def test_shell_action_required_field():
    """ShellAction 需要 command 字段；timeout 默认 None。"""
    from wentian.hooks.spec import ShellAction

    a = ShellAction(command="echo hello")
    assert a.command == "echo hello"
    assert a.timeout is None


def test_shell_action_with_timeout():
    """ShellAction 可指定 timeout。"""
    from wentian.hooks.spec import ShellAction

    a = ShellAction(command="./script.sh", timeout=30)
    assert a.timeout == 30


def test_shell_action_frozen():
    """ShellAction 是 frozen dataclass。"""
    from wentian.hooks.spec import ShellAction

    a = ShellAction(command="echo hi")
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.command = "other"  # type: ignore[misc]


def test_prompt_action_required_field():
    """PromptAction 需要 text 字段。"""
    from wentian.hooks.spec import PromptAction

    a = PromptAction(text="请注意：{tool_name} 被调用")
    assert a.text == "请注意：{tool_name} 被调用"


def test_prompt_action_frozen():
    """PromptAction 是 frozen dataclass。"""
    from wentian.hooks.spec import PromptAction

    a = PromptAction(text="hello")
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.text = "other"  # type: ignore[misc]


def test_http_action_required_and_defaults():
    """HttpAction 需要 url；method 默认 POST；timeout 默认 None。"""
    from wentian.hooks.spec import HttpAction

    a = HttpAction(url="https://example.com/hook")
    assert a.url == "https://example.com/hook"
    assert a.method == "POST"
    assert a.timeout is None


def test_http_action_custom():
    """HttpAction 可自定义 method 和 timeout。"""
    from wentian.hooks.spec import HttpAction

    a = HttpAction(url="https://example.com", method="GET", timeout=10)
    assert a.method == "GET"
    assert a.timeout == 10


def test_http_action_frozen():
    """HttpAction 是 frozen dataclass。"""
    from wentian.hooks.spec import HttpAction

    a = HttpAction(url="https://example.com")
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.url = "other"  # type: ignore[misc]


def test_subagent_action_default():
    """SubAgentAction() 无参构造，prompt 默认为空串。"""
    from wentian.hooks.spec import SubAgentAction

    a = SubAgentAction()
    assert a.prompt == ""


def test_subagent_action_with_prompt():
    """SubAgentAction 可带 prompt 字符串。"""
    from wentian.hooks.spec import SubAgentAction

    a = SubAgentAction(prompt="分析工具调用")
    assert a.prompt == "分析工具调用"


def test_subagent_action_frozen():
    """SubAgentAction 是 frozen dataclass。"""
    from wentian.hooks.spec import SubAgentAction

    a = SubAgentAction()
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.prompt = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Action union alias —— 四种动作类型均可被接受
# ---------------------------------------------------------------------------


def test_action_union_alias_accessible():
    """Action 联合别名可从 hooks.spec 导入。"""
    from wentian.hooks.spec import Action  # noqa: F401 — import is the assertion


# ---------------------------------------------------------------------------
# HookRule dataclass —— frozen，必填 event+action，其余默认
# ---------------------------------------------------------------------------


def test_hook_rule_required_fields():
    """HookRule(event, action) 可构造；condition=None/once=False/background=False 为默认。"""
    from wentian.hooks.spec import HookEvent, HookRule, ShellAction

    rule = HookRule(
        event=HookEvent.SESSION_START,
        action=ShellAction(command="echo start"),
    )
    assert rule.event is HookEvent.SESSION_START
    assert isinstance(rule.action, ShellAction)
    assert rule.condition is None
    assert rule.once is False
    assert rule.background is False


def test_hook_rule_with_condition():
    """HookRule 可带 Condition。"""
    from wentian.hooks.spec import (
        Clause,
        Condition,
        HookEvent,
        HookRule,
        Match,
        ShellAction,
    )

    cond = Condition(
        match=Match.ALL,
        clauses=(Clause(field="tool_name", pattern="Bash"),),
    )
    rule = HookRule(
        event=HookEvent.PRE_TOOL_USE,
        action=ShellAction(command="./check.sh"),
        condition=cond,
    )
    assert rule.condition is cond


def test_hook_rule_once_and_background():
    """HookRule 可设置 once=True 和 background=True。"""
    from wentian.hooks.spec import HookEvent, HookRule, PromptAction

    rule = HookRule(
        event=HookEvent.SESSION_END,
        action=PromptAction(text="会话已结束"),
        once=True,
        background=True,
    )
    assert rule.once is True
    assert rule.background is True


def test_hook_rule_frozen():
    """HookRule 是 frozen dataclass，修改字段应抛 FrozenInstanceError。"""
    from wentian.hooks.spec import HookEvent, HookRule, ShellAction

    rule = HookRule(
        event=HookEvent.STOP,
        action=ShellAction(command="echo stop"),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        rule.once = True  # type: ignore[misc]


def test_hook_rule_with_all_action_types():
    """HookRule.action 可容纳四种 Action 类型（联合类型兼容性）。"""
    from wentian.hooks.spec import (
        HookEvent,
        HookRule,
        HttpAction,
        PromptAction,
        ShellAction,
        SubAgentAction,
    )

    rules = [
        HookRule(event=HookEvent.SESSION_START, action=ShellAction(command="x")),
        HookRule(event=HookEvent.SESSION_END, action=PromptAction(text="y")),
        HookRule(event=HookEvent.ROUND_END, action=HttpAction(url="http://x")),
        HookRule(event=HookEvent.POST_TOOL_USE, action=SubAgentAction()),
    ]
    assert len(rules) == 4


# ---------------------------------------------------------------------------
# __init__.py 导出 —— 全部公开符号可从 wentian.hooks 直接导入
# ---------------------------------------------------------------------------


def test_package_exports_all_public_symbols():
    """wentian.hooks 包导出全部公开符号。"""
    from wentian.hooks import (  # noqa: F401
        INTERCEPT_EVENTS,
        Action,
        Clause,
        Condition,
        HookEvent,
        HookRule,
        HttpAction,
        Match,
        PromptAction,
        ShellAction,
        SubAgentAction,
    )
