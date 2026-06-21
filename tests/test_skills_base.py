"""v0.11 · C100 · F73（任务 T126）— Skill / SkillMode 数据模型单元测试。

TDD 红-绿-重构：先跑此文件确认因 skills.base 模块缺失而失败，再实现转绿。
"""

from __future__ import annotations

import dataclasses

import pytest


def test_skill_mode_members():
    """SkillMode 有且仅有 SHARED / ISOLATED 两个成员，值为对应字符串。"""
    from wentian.skills.base import SkillMode

    names = {m.name for m in SkillMode}
    assert names == {"SHARED", "ISOLATED"}
    assert SkillMode.SHARED.value == "shared"
    assert SkillMode.ISOLATED.value == "isolated"


def test_skill_defaults():
    """仅给必填字段构造 Skill 时，各默认值符合规格。"""
    from wentian.skills.base import Skill, SkillMode

    skill = Skill(name="x", description="d", body="b")
    assert skill.name == "x"
    assert skill.description == "d"
    assert skill.body == "b"
    # 默认值
    assert skill.mode is SkillMode.SHARED
    assert skill.allowed_tools is None
    assert skill.history == 0
    assert skill.model is None
    assert skill.source == "builtin"


def test_skill_full_construction():
    """所有字段可显式赋值，并按原样保留。"""
    from wentian.skills.base import Skill, SkillMode

    skill = Skill(
        name="commit",
        description="生成提交",
        body="# SOP\n$ARGUMENTS",
        mode=SkillMode.ISOLATED,
        allowed_tools=("bash", "read"),
        history=5,
        model="opus",
        source="project",
    )
    assert skill.mode is SkillMode.ISOLATED
    assert skill.allowed_tools == ("bash", "read")
    assert skill.history == 5
    assert skill.model == "opus"
    assert skill.source == "project"


def test_skill_frozen():
    """Skill 是 frozen dataclass，修改字段应抛 FrozenInstanceError。"""
    from wentian.skills.base import Skill

    skill = Skill(name="x", description="d", body="b")
    with pytest.raises(dataclasses.FrozenInstanceError):
        skill.name = "other"  # type: ignore[misc]


def test_skill_exports_from_package():
    """包入口导出 Skill 与 SkillMode。"""
    from wentian.skills import Skill, SkillMode

    assert Skill is not None
    assert SkillMode is not None
