"""Tests for tools/base.py and tools/registry.py.

RED-GREEN-REFACTOR cycle for T31: Tool ABC + ToolRegistry.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Helpers — FakeTool defined locally (not from the tools layer itself)
# ---------------------------------------------------------------------------


def _make_fake_tool_class(
    name: str = "fake_tool",
    description: str = "A fake tool for testing",
    parameters: dict | None = None,
):
    """Return a concrete Tool subclass with the given attributes.

    Uses ``type()`` with a pre-built namespace so that ``run`` is defined
    inside the class body, satisfying ABC's abstract-method tracking.
    """
    from wentian.tools.base import Tool

    _params = (
        parameters
        if parameters is not None
        else {
            "type": "object",
            "properties": {"input": {"type": "string"}},
            "required": ["input"],
        }
    )

    def run(self, args: dict) -> str:  # noqa: ANN001
        return f"result:{args}"

    return type(
        "FakeTool",
        (Tool,),
        {
            "name": name,
            "description": description,
            "parameters": _params,
            "run": run,
        },
    )


# ---------------------------------------------------------------------------
# ToolError
# ---------------------------------------------------------------------------


class TestToolError:
    def test_tool_error_is_exception(self):
        """ToolError must be a subclass of Exception."""
        from wentian.tools.base import ToolError

        assert issubclass(ToolError, Exception)

    def test_tool_error_can_be_raised_and_caught(self):
        """ToolError can be raised and caught as Exception."""
        from wentian.tools.base import ToolError

        with pytest.raises(Exception):
            raise ToolError("something went wrong")

    def test_tool_error_message_preserved(self):
        """ToolError preserves the message string."""
        from wentian.tools.base import ToolError

        err = ToolError("msg")
        assert str(err) == "msg"


# ---------------------------------------------------------------------------
# Tool ABC
# ---------------------------------------------------------------------------


class TestToolABC:
    def test_tool_is_abstract(self):
        """Tool cannot be instantiated directly — it's an ABC."""
        from wentian.tools.base import Tool

        with pytest.raises(TypeError):
            Tool()  # type: ignore[abstract]

    def test_concrete_subclass_without_run_is_abstract(self):
        """A subclass that does not implement run() is still abstract."""
        from wentian.tools.base import Tool

        class NoRun(Tool):
            name = "norun"
            description = "missing run"
            parameters = {}

        with pytest.raises(TypeError):
            NoRun()

    def test_concrete_subclass_instantiates(self):
        """A fully-implemented subclass can be instantiated."""
        FakeTool = _make_fake_tool_class()
        instance = FakeTool()
        assert instance is not None

    def test_tool_has_default_timeout(self):
        """Tool.timeout_s defaults to 60.0."""
        from wentian.tools.base import Tool

        assert Tool.timeout_s == 60.0

    def test_requires_confirmation_derives_from_category(self):
        """v0.6 · C35 · F43/F45（任务 T75）— requires_confirmation is now a
        property derived from category: read-only ⇒ False, else ⇒ True."""
        from wentian.permissions.decision import Category
        from wentian.tools.base import Tool

        read_only = type(
            "RO",
            (Tool,),
            {
                "name": "ro",
                "description": "d",
                "parameters": {},
                "category": Category.READ_ONLY,
                "run": lambda self, a: "x",
            },
        )()
        side_effect = type(
            "SE",
            (Tool,),
            {
                "name": "se",
                "description": "d",
                "parameters": {},
                "category": Category.FILE_WRITE,
                "run": lambda self, a: "x",
            },
        )()
        assert read_only.requires_confirmation is False
        assert side_effect.requires_confirmation is True

    def test_run_returns_string(self):
        """run() returns a string on success."""
        FakeTool = _make_fake_tool_class()
        result = FakeTool().run({"input": "hello"})
        assert isinstance(result, str)

    def test_run_can_raise_tool_error(self):
        """run() may raise ToolError on failure."""
        from wentian.tools.base import Tool, ToolError

        class BrokenTool(Tool):
            name = "broken"
            description = "always fails"
            parameters = {}

            def run(self, args: dict) -> str:
                raise ToolError("failure")

        with pytest.raises(ToolError, match="failure"):
            BrokenTool().run({})


# ---------------------------------------------------------------------------
# spec() helper
# ---------------------------------------------------------------------------


class TestSpecHelper:
    def test_spec_returns_tool_spec(self):
        """spec(tool) returns a ToolSpec instance."""
        from wentian.tools.base import spec
        from wentian.providers.base import ToolSpec

        FakeTool = _make_fake_tool_class(name="alpha", description="Alpha tool")
        ts = spec(FakeTool())
        assert isinstance(ts, ToolSpec)

    def test_spec_fields_match_tool_attributes(self):
        """spec(tool) copies name, description, parameters from the tool instance."""
        from wentian.tools.base import spec

        params = {"type": "object", "properties": {}}
        FakeTool = _make_fake_tool_class(
            name="mytool", description="Does something", parameters=params
        )
        ts = spec(FakeTool())
        assert ts.name == "mytool"
        assert ts.description == "Does something"
        assert ts.parameters == params


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------


class TestToolRegistry:
    def _registry(self):
        from wentian.tools.registry import ToolRegistry

        return ToolRegistry()

    def test_register_and_get_returns_same_instance(self):
        """After register(tool), get(name) returns the same object."""
        reg = self._registry()
        FakeTool = _make_fake_tool_class(name="tool_a")
        instance = FakeTool()
        reg.register(instance)
        assert reg.get("tool_a") is instance

    def test_get_unknown_name_returns_none(self):
        """get('nonexistent') returns None."""
        reg = self._registry()
        assert reg.get("nonexistent") is None

    def test_duplicate_register_raises_value_error(self):
        """Registering a second tool with the same name raises ValueError."""
        reg = self._registry()
        FakeTool = _make_fake_tool_class(name="dup")
        reg.register(FakeTool())
        with pytest.raises(ValueError, match="dup"):
            reg.register(FakeTool())

    def test_specs_returns_list_of_tool_specs(self):
        """specs() returns a list[ToolSpec]."""
        from wentian.providers.base import ToolSpec

        reg = self._registry()
        FakeTool = _make_fake_tool_class(name="spec_tool")
        reg.register(FakeTool())
        result = reg.specs()
        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], ToolSpec)

    def test_specs_fields_match_tool_attributes(self):
        """Each ToolSpec in specs() has fields matching the registered tool."""
        reg = self._registry()
        params = {"type": "object", "properties": {"x": {"type": "number"}}}
        FakeTool = _make_fake_tool_class(
            name="myspec", description="My spec tool", parameters=params
        )
        reg.register(FakeTool())
        ts = reg.specs()[0]
        assert ts.name == "myspec"
        assert ts.description == "My spec tool"
        assert ts.parameters == params

    def test_specs_preserves_registration_order(self):
        """specs() returns ToolSpecs in the order tools were registered."""
        reg = self._registry()
        for i in range(3):
            FakeTool = _make_fake_tool_class(name=f"tool_{i}")
            reg.register(FakeTool())
        names = [ts.name for ts in reg.specs()]
        assert names == ["tool_0", "tool_1", "tool_2"]

    def test_names_returns_registered_names_in_order(self):
        """names() returns tool names in registration order."""
        reg = self._registry()
        for letter in ("b", "a", "c"):
            FakeTool = _make_fake_tool_class(name=letter)
            reg.register(FakeTool())
        assert reg.names() == ["b", "a", "c"]

    def test_specs_empty_when_no_tools_registered(self):
        """specs() returns an empty list when no tools are registered."""
        reg = self._registry()
        assert reg.specs() == []

    def test_names_empty_when_no_tools_registered(self):
        """names() returns an empty list when no tools are registered."""
        reg = self._registry()
        assert reg.names() == []
