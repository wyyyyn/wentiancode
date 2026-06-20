"""v0.10 · C88 · F71（任务 T109）— 斜杠命令解析器的单元测试。

TDD 红-绿-重构：先确认因功能缺失而失败，再实现转绿。
"""

from __future__ import annotations


def test_parse_simple():
    """/Help → ParsedCommand(name="help", args="")（名转小写）。"""
    from wentian.commands.parser import parse

    result = parse("/Help")
    assert result is not None
    assert result.name == "help"
    assert result.args == ""


def test_parse_with_args():
    """/session resume abc-123 → name="session", args="resume abc-123"。"""
    from wentian.commands.parser import parse

    result = parse("/session resume abc-123")
    assert result is not None
    assert result.name == "session"
    assert result.args == "resume abc-123"


def test_parse_single_char():
    """/x → name="x", args=""。"""
    from wentian.commands.parser import parse

    result = parse("/x")
    assert result is not None
    assert result.name == "x"
    assert result.args == ""


def test_parse_args_stripped():
    """参数首尾空白被 strip。"""
    from wentian.commands.parser import parse

    result = parse("/cmd   arg1  ")
    assert result is not None
    assert result.name == "cmd"
    assert result.args == "arg1"


def test_parse_bare_slash_returns_none():
    """裸斜杠 "/" 返回 None。"""
    from wentian.commands.parser import parse

    assert parse("/") is None


def test_parse_slash_with_spaces_returns_none():
    """/   （斜杠后只有空格）返回 None。"""
    from wentian.commands.parser import parse

    assert parse("/   ") is None


def test_parse_slash_space_name_returns_none():
    """/ foo（斜杠后紧跟空格再是名称）返回 None（斜杠后第一段为空）。"""
    from wentian.commands.parser import parse

    assert parse("/ foo") is None


def test_parsed_command_frozen():
    """ParsedCommand 是 frozen dataclass，修改字段应抛 FrozenInstanceError。"""
    import dataclasses

    import pytest

    from wentian.commands.parser import parse

    result = parse("/help")
    assert result is not None
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.name = "other"  # type: ignore[misc]


def test_parse_uppercase_normalised():
    """命令名统一转小写，如 /SESSION → name="session"。"""
    from wentian.commands.parser import parse

    result = parse("/SESSION foo bar")
    assert result is not None
    assert result.name == "session"
    assert result.args == "foo bar"
