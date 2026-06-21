"""v0.10 · C86 · F70/N34/N37（任务 T110）— 命令注册中心的单元测试。

TDD 红-绿-重构：先确认因功能缺失而失败，再实现转绿。
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _make_spec(name: str, *, aliases: tuple[str, ...] = (), hidden: bool = False):
    """构造一个最简 CommandSpec 用于测试。"""
    from wentian.commands.spec import CommandSpec, CommandType

    return CommandSpec(
        name=name,
        summary=f"测试命令 {name}",
        usage=f"/{name}",
        type=CommandType.LOCAL,
        handler=lambda ctx, args: None,
        aliases=aliases,
        hidden=hidden,
    )


# ---------------------------------------------------------------------------
# 基本查找
# ---------------------------------------------------------------------------


def test_register_and_lookup_by_name():
    """register 后可按规范名查到。"""
    from wentian.commands.registry import CommandRegistry

    reg = CommandRegistry()
    spec = _make_spec("help")
    reg.register(spec)
    assert reg.lookup("help") is spec


def test_lookup_by_alias():
    """register 后可按别名查到。"""
    from wentian.commands.registry import CommandRegistry

    reg = CommandRegistry()
    spec = _make_spec("exit", aliases=("quit", "q"))
    reg.register(spec)
    assert reg.lookup("quit") is spec
    assert reg.lookup("q") is spec


def test_lookup_case_insensitive():
    """lookup 大小写不敏感（NAME / name / Name 均命中）。"""
    from wentian.commands.registry import CommandRegistry

    reg = CommandRegistry()
    spec = _make_spec("help")
    reg.register(spec)
    assert reg.lookup("HELP") is spec
    assert reg.lookup("Help") is spec
    assert reg.lookup("help") is spec


def test_lookup_alias_case_insensitive():
    """别名查找也大小写不敏感。"""
    from wentian.commands.registry import CommandRegistry

    reg = CommandRegistry()
    spec = _make_spec("exit", aliases=("quit",))
    reg.register(spec)
    assert reg.lookup("QUIT") is spec


def test_lookup_missing_returns_none():
    """lookup 未注册的名称返回 None。"""
    from wentian.commands.registry import CommandRegistry

    reg = CommandRegistry()
    assert reg.lookup("nope") is None


# ---------------------------------------------------------------------------
# visible / all / completions
# ---------------------------------------------------------------------------


def test_visible_excludes_hidden():
    """visible() 按注册顺序返回 hidden=False 的命令；hidden 不在内。"""
    from wentian.commands.registry import CommandRegistry

    reg = CommandRegistry()
    a = _make_spec("aaa")
    b = _make_spec("bbb", hidden=True)
    c = _make_spec("ccc")
    reg.register(a)
    reg.register(b)
    reg.register(c)
    visible = reg.visible()
    assert visible == [a, c]


def test_visible_registration_order():
    """visible() 严格按注册顺序。"""
    from wentian.commands.registry import CommandRegistry

    reg = CommandRegistry()
    specs = [_make_spec(n) for n in ("zzz", "aaa", "mmm")]
    for s in specs:
        reg.register(s)
    assert reg.visible() == specs


def test_all_includes_hidden():
    """all() 包含 hidden 命令，按注册顺序。"""
    from wentian.commands.registry import CommandRegistry

    reg = CommandRegistry()
    a = _make_spec("aaa")
    b = _make_spec("bbb", hidden=True)
    reg.register(a)
    reg.register(b)
    assert reg.all() == [a, b]


def test_completions_prefix_match():
    """completions("se") 返回可见命令中规范名以 "se" 开头的 CommandSpec 列表。"""
    from wentian.commands.registry import CommandRegistry
    from wentian.commands.spec import CommandSpec

    reg = CommandRegistry()
    reg.register(_make_spec("session"))
    reg.register(_make_spec("search"))
    reg.register(_make_spec("help"))
    reg.register(_make_spec("set", hidden=True))  # hidden 不入

    result = reg.completions("se")
    # 返回 CommandSpec 对象，保序
    assert all(isinstance(s, CommandSpec) for s in result)
    assert [s.name for s in result] == ["session", "search"]


def test_completions_empty_prefix():
    """completions("") 返回全部可见命令的 CommandSpec 列表。"""
    from wentian.commands.registry import CommandRegistry
    from wentian.commands.spec import CommandSpec

    reg = CommandRegistry()
    reg.register(_make_spec("aaa"))
    reg.register(_make_spec("bbb", hidden=True))
    reg.register(_make_spec("ccc"))
    result = reg.completions("")
    assert all(isinstance(s, CommandSpec) for s in result)
    assert [s.name for s in result] == ["aaa", "ccc"]


def test_completions_no_match():
    """completions("zzz") 无匹配返回 []。"""
    from wentian.commands.registry import CommandRegistry

    reg = CommandRegistry()
    reg.register(_make_spec("help"))
    assert reg.completions("zzz") == []


def test_completions_case_insensitive_prefix():
    """completions 的 prefix 大小写不敏感；返回 CommandSpec 对象。"""
    from wentian.commands.registry import CommandRegistry
    from wentian.commands.spec import CommandSpec

    reg = CommandRegistry()
    reg.register(_make_spec("session"))
    result = reg.completions("SE")
    assert all(isinstance(s, CommandSpec) for s in result)
    assert [s.name for s in result] == ["session"]


# ---------------------------------------------------------------------------
# 冲突检测
# ---------------------------------------------------------------------------


def test_register_duplicate_name_raises():
    """同名命令再注册抛 ValueError。"""
    from wentian.commands.registry import CommandRegistry

    reg = CommandRegistry()
    reg.register(_make_spec("help"))
    with pytest.raises(ValueError, match="help"):
        reg.register(_make_spec("help"))


def test_register_alias_conflicts_with_existing_name_raises():
    """新命令的别名与已有规范名冲突 → ValueError。"""
    from wentian.commands.registry import CommandRegistry

    reg = CommandRegistry()
    reg.register(_make_spec("help"))
    with pytest.raises(ValueError):
        # 新命令的别名 "help" 与已注册规范名冲突
        reg.register(_make_spec("other", aliases=("help",)))


def test_register_name_conflicts_with_existing_alias_raises():
    """新命令规范名与已有别名冲突 → ValueError。"""
    from wentian.commands.registry import CommandRegistry

    reg = CommandRegistry()
    reg.register(_make_spec("exit", aliases=("quit",)))
    with pytest.raises(ValueError):
        # 新命令规范名 "quit" 与已注册别名冲突
        reg.register(_make_spec("quit"))


def test_register_alias_conflicts_with_existing_alias_raises():
    """新命令别名与已有别名冲突 → ValueError。"""
    from wentian.commands.registry import CommandRegistry

    reg = CommandRegistry()
    reg.register(_make_spec("exit", aliases=("q",)))
    with pytest.raises(ValueError):
        reg.register(_make_spec("quit", aliases=("q",)))


# ---------------------------------------------------------------------------
# v0.11 · C107b（任务 T134b）— unregister
# ---------------------------------------------------------------------------


def test_unregister_removes_from_lookup_visible_completions():
    """register 后 unregister：lookup / visible / completions 都查不到了。"""
    from wentian.commands.registry import CommandRegistry

    reg = CommandRegistry()
    spec = _make_spec("deploy")
    reg.register(spec)
    assert reg.lookup("deploy") is spec
    assert spec in reg.visible()
    assert [s.name for s in reg.completions("dep")] == ["deploy"]

    reg.unregister("deploy")
    assert reg.lookup("deploy") is None
    assert spec not in reg.visible()
    assert reg.completions("dep") == []
    assert spec not in reg.all()


def test_unregister_removes_all_alias_keys():
    """unregister 连同别名一起摘掉：别名也查不到。"""
    from wentian.commands.registry import CommandRegistry

    reg = CommandRegistry()
    spec = _make_spec("exit", aliases=("quit", "q"))
    reg.register(spec)
    reg.unregister("exit")
    assert reg.lookup("exit") is None
    assert reg.lookup("quit") is None
    assert reg.lookup("q") is None


def test_unregister_case_insensitive():
    """unregister 按规范名大小写不敏感匹配。"""
    from wentian.commands.registry import CommandRegistry

    reg = CommandRegistry()
    spec = _make_spec("review")
    reg.register(spec)
    reg.unregister("REVIEW")
    assert reg.lookup("review") is None


def test_unregister_absent_is_noop():
    """unregister 不存在的名字 = no-op（不抛）。"""
    from wentian.commands.registry import CommandRegistry

    reg = CommandRegistry()
    reg.register(_make_spec("help"))
    reg.unregister("nope")  # 不抛
    assert reg.lookup("help") is not None
    assert len(reg.all()) == 1


def test_unregister_then_reregister_succeeds():
    """unregister 后该 key 释放，可重新注册同名（不再冲突）。"""
    from wentian.commands.registry import CommandRegistry

    reg = CommandRegistry()
    reg.register(_make_spec("review"))
    reg.unregister("review")
    new_spec = _make_spec("review")
    reg.register(new_spec)  # 不抛
    assert reg.lookup("review") is new_spec
