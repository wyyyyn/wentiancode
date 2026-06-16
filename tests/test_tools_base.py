"""Tests for tools/base.py Tool metadata contract.

v0.6 · C35 · F43/F45（任务 T75）— Tool gains category / friendly_name /
parameter-extraction declarations; requires_confirmation becomes a derived
property (``category != READ_ONLY``).
"""
from __future__ import annotations

from wentian.permissions.decision import Category
from wentian.tools.base import Tool


# ---------------------------------------------------------------------------
# Helpers — concrete Tool subclasses defined locally.
# ---------------------------------------------------------------------------

def _make_tool_class(**ns) -> type:
    base_ns = {
        "name": "fake_tool",
        "description": "A fake tool",
        "parameters": {"type": "object", "properties": {}, "required": []},
        "run": lambda self, args: "ok",
    }
    base_ns.update(ns)
    return type("FakeTool", (Tool,), base_ns)


# ---------------------------------------------------------------------------
# requires_confirmation is a derived property of category
# ---------------------------------------------------------------------------

def test_read_only_category_does_not_require_confirmation():
    tool = _make_tool_class(category=Category.READ_ONLY)()
    assert tool.requires_confirmation is False


def test_file_write_category_requires_confirmation():
    tool = _make_tool_class(category=Category.FILE_WRITE)()
    assert tool.requires_confirmation is True


def test_command_exec_category_requires_confirmation():
    tool = _make_tool_class(category=Category.COMMAND_EXEC)()
    assert tool.requires_confirmation is True


# ---------------------------------------------------------------------------
# friendly_name / extraction declarations carry through
# ---------------------------------------------------------------------------

def test_friendly_name_is_exposed():
    tool = _make_tool_class(category=Category.READ_ONLY, friendly_name="Read")()
    assert tool.friendly_name == "Read"


def test_default_extraction_declarations():
    """Defaults: no command arg, no path args."""
    tool = _make_tool_class(category=Category.READ_ONLY)()
    assert tool.command_arg is None
    assert tool.path_args == ()


def test_extraction_declarations_can_be_set():
    tool = _make_tool_class(
        category=Category.COMMAND_EXEC,
        command_arg="command",
        path_args=(),
    )()
    assert tool.command_arg == "command"
    assert tool.path_args == ()
