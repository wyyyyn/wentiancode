"""v0.11 · C102 · F73（任务 T128）— SkillRegistry 单元测试。

TDD 红-绿-重构：先跑此文件确认因 skills.registry 模块缺失而失败，再实现转绿。
"""

from __future__ import annotations


def _make_skill(name: str, description: str = ""):
    """构造一个最简 Skill 用于测试。"""
    from wentian.skills.base import Skill

    return Skill(name=name, description=description or f"描述 {name}", body=f"# {name}")


# ---------------------------------------------------------------------------
# add / get
# ---------------------------------------------------------------------------


def test_add_and_get_by_name():
    """add 后可按名查到对应 Skill。"""
    from wentian.skills.registry import SkillRegistry

    reg = SkillRegistry()
    a = _make_skill("alpha")
    b = _make_skill("beta")
    reg.add(a)
    reg.add(b)
    assert reg.get("alpha") is a
    assert reg.get("beta") is b


def test_get_missing_returns_none():
    """get 未注册的名称返回 None。"""
    from wentian.skills.registry import SkillRegistry

    reg = SkillRegistry()
    assert reg.get("nope") is None


# ---------------------------------------------------------------------------
# list（按名排序、稳定）
# ---------------------------------------------------------------------------


def test_list_sorted_by_name():
    """list() 按 name 升序返回，乱序加入也保证有序。"""
    from wentian.skills.registry import SkillRegistry

    reg = SkillRegistry()
    reg.add(_make_skill("zeta"))
    reg.add(_make_skill("alpha"))
    reg.add(_make_skill("mu"))
    names = [s.name for s in reg.list()]
    assert names == ["alpha", "mu", "zeta"]


def test_list_empty():
    """空注册中心 list() 返回空列表。"""
    from wentian.skills.registry import SkillRegistry

    assert SkillRegistry().list() == []


# ---------------------------------------------------------------------------
# menu
# ---------------------------------------------------------------------------


def test_menu_returns_name_description_tuples_sorted():
    """menu() 返回 ((name, description), ...)，按 name 排序。"""
    from wentian.skills.registry import SkillRegistry

    reg = SkillRegistry()
    reg.add(_make_skill("zeta", "做 z 的事"))
    reg.add(_make_skill("alpha", "做 a 的事"))
    menu = reg.menu()
    assert menu == (("alpha", "做 a 的事"), ("zeta", "做 z 的事"))
    # 是元组的元组
    assert isinstance(menu, tuple)
    assert all(isinstance(item, tuple) and len(item) == 2 for item in menu)


# ---------------------------------------------------------------------------
# 同名覆盖（高层覆盖低层；调用方控制顺序）
# ---------------------------------------------------------------------------


def test_add_same_name_overwrites():
    """同名再 add，第二个覆盖第一个。"""
    from wentian.skills.registry import SkillRegistry

    reg = SkillRegistry()
    first = _make_skill("dup", "第一个")
    second = _make_skill("dup", "第二个")
    reg.add(first)
    reg.add(second)
    assert reg.get("dup") is second
    assert reg.get("dup").description == "第二个"
    # 只有一个条目
    assert len(reg.list()) == 1
    assert reg.menu() == (("dup", "第二个"),)
