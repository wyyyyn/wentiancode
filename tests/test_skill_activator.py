"""v0.11 · C104 · F87/F89（任务 T133）— SkillActivator 双模式激活编排测试。

离线（假 provider，绝不联网）。覆盖：
- SHARED：activate 进集 + 确认串 + active_bodies/allowed_tools 反映。
- 白名单并集规则：任一不限→None；多 Skill 都有白名单→并集 ∪ {load_skill}。
- 空集：未激活 → allowed_tools() is None、active_bodies()==[]。
- ISOLATED：worker 线程跑子对话、返回末条助手正文、不进激活集、
  线程 join 干净（无泄漏）、子对话起始带主历史末 history 条。
- clear：清空激活集。
"""

from __future__ import annotations

import threading
from typing import Iterator

from conftest import FakeProvider

from wentian.providers.base import (
    Done,
    Message,
    Provider,
    StreamEvent,
    TextDelta,
    ToolSpec,
)
from wentian.skill_activator import SkillActivator
from wentian.skills.base import Skill, SkillMode
from wentian.skills.registry import SkillRegistry


# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------


class RecordingProvider(Provider):
    """记录每次 stream() 收到的 messages / system / tools，并 yield 固定助手文本。

    用于断言子对话起始历史（messages）与子系统提示（system）。
    """

    name = "recording"

    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list[list[Message]] = []
        self.systems_seen: list[str | None] = []
        self.tools_seen: list[object] = []

    def stream(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
    ) -> Iterator[StreamEvent]:
        self.calls.append([dict(m) for m in messages])
        self.systems_seen.append(system)
        self.tools_seen.append(tools)
        yield TextDelta(self._text)
        yield Done(None)


def _registry(*skills: Skill) -> SkillRegistry:
    reg = SkillRegistry()
    for s in skills:
        reg.add(s)
    return reg


def _make_activator(
    registry: SkillRegistry,
    *,
    provider=None,
    main_messages: list[Message] | None = None,
    main_system: str = "MAIN-SYS",
) -> SkillActivator:
    """构造 activator；默认假 provider、空主历史。"""
    msgs = main_messages if main_messages is not None else []
    return SkillActivator(
        registry,
        provider=provider if provider is not None else FakeProvider([Done(None)]),
        tool_registry=None,
        executor=None,
        get_main_messages=lambda: msgs,
        get_main_system=lambda: main_system,
    )


# ---------------------------------------------------------------------------
# SHARED
# ---------------------------------------------------------------------------


def test_shared_activate_returns_confirmation_and_enters_set() -> None:
    skill = Skill(
        name="commit",
        description="提交",
        body="提交说明：$ARGUMENTS\n按规范提交。",
        mode=SkillMode.SHARED,
        allowed_tools=("shell", "read_file"),
    )
    act = _make_activator(_registry(skill))

    result = act.activate("commit", "fix")

    assert "已激活" in result
    assert "commit" in result

    bodies = act.active_bodies()
    assert bodies == [("commit", "提交说明：fix\n按规范提交。")]

    assert act.allowed_tools() == frozenset({"shell", "read_file", "load_skill"})


def test_shared_reactivate_refreshes_args() -> None:
    skill = Skill(
        name="commit",
        description="提交",
        body="参数=$ARGUMENTS",
        allowed_tools=("shell",),
    )
    act = _make_activator(_registry(skill))

    act.activate("commit", "first")
    act.activate("commit", "second")

    bodies = act.active_bodies()
    # 去重按 name：仍只有一条，args 刷新为最新。
    assert bodies == [("commit", "参数=second")]


def test_unknown_skill_returns_error_string_no_raise() -> None:
    act = _make_activator(_registry())
    result = act.activate("nope", "")
    assert isinstance(result, str)
    assert "nope" in result
    # 未知名不进激活集。
    assert act.active_bodies() == []


# ---------------------------------------------------------------------------
# 白名单并集规则
# ---------------------------------------------------------------------------


def test_allowed_tools_union_with_load_skill() -> None:
    s1 = Skill(name="a", description="", body="A", allowed_tools=("shell",))
    s2 = Skill(name="b", description="", body="B", allowed_tools=("read_file",))
    act = _make_activator(_registry(s1, s2))

    act.activate("a", "")
    act.activate("b", "")

    assert act.allowed_tools() == frozenset({"shell", "read_file", "load_skill"})


def test_allowed_tools_any_unrestricted_returns_none() -> None:
    s1 = Skill(name="a", description="", body="A", allowed_tools=("shell",))
    s2 = Skill(name="b", description="", body="B", allowed_tools=None)
    act = _make_activator(_registry(s1, s2))

    act.activate("a", "")
    act.activate("b", "")

    assert act.allowed_tools() is None


def test_allowed_tools_empty_tuple_counts_as_unrestricted() -> None:
    s1 = Skill(name="a", description="", body="A", allowed_tools=("shell",))
    s2 = Skill(name="b", description="", body="B", allowed_tools=())
    act = _make_activator(_registry(s1, s2))

    act.activate("a", "")
    act.activate("b", "")

    assert act.allowed_tools() is None


# ---------------------------------------------------------------------------
# 空集
# ---------------------------------------------------------------------------


def test_empty_active_set() -> None:
    act = _make_activator(_registry())
    assert act.allowed_tools() is None
    assert act.active_bodies() == []


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


def test_clear_empties_active_set() -> None:
    skill = Skill(name="a", description="", body="A", allowed_tools=("shell",))
    act = _make_activator(_registry(skill))
    act.activate("a", "")
    assert act.active_bodies() != []
    assert act.allowed_tools() is not None

    act.clear()

    assert act.active_bodies() == []
    assert act.allowed_tools() is None


# ---------------------------------------------------------------------------
# ISOLATED
# ---------------------------------------------------------------------------


def test_isolated_returns_final_assistant_text_no_active_set_no_leak() -> None:
    skill = Skill(
        name="audit",
        description="审计",
        body="执行审计：$ARGUMENTS",
        mode=SkillMode.ISOLATED,
        history=2,
        allowed_tools=("read_file",),
    )
    main_messages: list[Message] = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]
    provider = RecordingProvider("子对话最终结论。")
    act = _make_activator(
        _registry(skill),
        provider=provider,
        main_messages=main_messages,
        main_system="MAIN-SYS",
    )

    baseline_threads = threading.active_count()

    result = act.activate("audit", "范围X")

    # 返回值 == 子对话末条助手正文（verbatim）。
    assert result == "子对话最终结论。"

    # 不进激活集。
    assert act.active_bodies() == []
    assert act.allowed_tools() is None

    # worker 线程已 join：active_count 回到基线（activate 同步返回）。
    assert threading.active_count() == baseline_threads

    # 子对话起始带主历史末 2 条（u2/a2），且 provider 至少被调一次。
    assert len(provider.calls) >= 1
    seed = provider.calls[0]
    # 末 history=2 条 + 因末条是 assistant 而补的触发 user。
    assert seed[0] == {"role": "user", "content": "u2"}
    assert seed[1] == {"role": "assistant", "content": "a2"}
    # 子系统提示含主 system + Skill 正文（已渲染）。
    sub_system = provider.systems_seen[0]
    assert sub_system is not None
    assert "MAIN-SYS" in sub_system
    assert "执行审计：范围X" in sub_system
    assert "audit" in sub_system

    # 主历史绝不被改动。
    assert main_messages == [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]


def test_isolated_zero_history_starts_with_trigger_user() -> None:
    skill = Skill(
        name="audit",
        description="审计",
        body="正文",
        mode=SkillMode.ISOLATED,
        history=0,
    )
    provider = RecordingProvider("OK")
    act = _make_activator(
        _registry(skill),
        provider=provider,
        main_messages=[{"role": "user", "content": "x"}],
    )

    result = act.activate("audit", "")

    assert result == "OK"
    seed = provider.calls[0]
    # history=0 → 起始空 → 追加触发 user（args 空则用 f"执行 {name}"）。
    assert seed == [{"role": "user", "content": "执行 audit"}]
    # 主激活集不变。
    assert act.active_bodies() == []


def test_isolated_uses_args_as_trigger_when_seed_not_ending_user() -> None:
    skill = Skill(
        name="audit",
        description="审计",
        body="正文",
        mode=SkillMode.ISOLATED,
        history=1,
    )
    # 末条是 assistant → 需补触发 user；args 非空 → 用 args 作触发文本。
    main_messages: list[Message] = [{"role": "assistant", "content": "prev"}]
    provider = RecordingProvider("DONE")
    act = _make_activator(
        _registry(skill),
        provider=provider,
        main_messages=main_messages,
    )

    result = act.activate("audit", "我的参数")

    assert result == "DONE"
    seed = provider.calls[0]
    assert seed[0] == {"role": "assistant", "content": "prev"}
    assert seed[-1] == {"role": "user", "content": "我的参数"}
