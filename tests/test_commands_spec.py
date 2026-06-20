"""v0.10 · C85 · F70/F72（任务 T108）— CommandType / CommandSpec 的单元测试。

TDD 红-绿-重构：先跑此文件确认因功能缺失而失败，再实现 spec.py 转绿。
"""

from __future__ import annotations

import dataclasses

import pytest


def test_command_type_members():
    """CommandType 有且仅有 LOCAL / UI_STATE / PROMPT 三个成员。"""
    from wentian.commands.spec import CommandType

    names = {m.name for m in CommandType}
    assert names == {"LOCAL", "UI_STATE", "PROMPT"}


def test_command_type_values():
    """枚举值为对应字符串。"""
    from wentian.commands.spec import CommandType

    assert CommandType.LOCAL.value == "local"
    assert CommandType.UI_STATE.value == "ui_state"
    assert CommandType.PROMPT.value == "prompt"


def test_command_spec_construction():
    """CommandSpec 可以用必填字段构造；默认值符合规格。"""
    from wentian.commands.spec import CommandSpec, CommandType

    def fake_handler(ctx, args):
        return None

    spec = CommandSpec(
        name="help",
        summary="显示帮助",
        usage="/help [command]",
        type=CommandType.LOCAL,
        handler=fake_handler,
    )
    assert spec.name == "help"
    assert spec.summary == "显示帮助"
    assert spec.usage == "/help [command]"
    assert spec.type == CommandType.LOCAL
    assert spec.handler is fake_handler
    # 默认值
    assert spec.aliases == ()
    assert spec.arg_hint == ""
    assert spec.hidden is False


def test_command_spec_with_aliases():
    """CommandSpec 支持别名 tuple 和 arg_hint / hidden。"""
    from wentian.commands.spec import CommandSpec, CommandType

    def noop(ctx, args):
        pass

    spec = CommandSpec(
        name="exit",
        summary="退出",
        usage="/exit",
        type=CommandType.LOCAL,
        handler=noop,
        aliases=("quit", "q"),
        arg_hint="",
        hidden=True,
    )
    assert spec.aliases == ("quit", "q")
    assert spec.hidden is True


def test_command_spec_frozen():
    """CommandSpec 是 frozen dataclass，修改字段应抛 FrozenInstanceError。"""
    from wentian.commands.spec import CommandSpec, CommandType

    spec = CommandSpec(
        name="help",
        summary="s",
        usage="u",
        type=CommandType.LOCAL,
        handler=lambda ctx, a: None,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.name = "other"  # type: ignore[misc]


def test_command_spec_handler_callable():
    """handler 字段持有 Callable，可被调用。"""
    from wentian.commands.spec import CommandSpec, CommandType

    calls: list[tuple] = []

    def my_handler(ctx, args):
        calls.append((ctx, args))
        return None

    spec = CommandSpec(
        name="test",
        summary="s",
        usage="u",
        type=CommandType.PROMPT,
        handler=my_handler,
    )
    assert callable(spec.handler)
    spec.handler(None, "")
    assert calls == [(None, "")]
