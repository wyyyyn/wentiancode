"""Tests for tools/skill_tool.py — LoadSkillTool (T129).

RED-GREEN-REFACTOR cycle for the system-level skill-loading tool (plan C103).

The tool takes an injected, duck-typed ``activator`` whose ``activate(name, args)``
returns a result string. The tool itself MUST NOT know about concrete activator,
repl, agent, or skills classes — it only wires the model-facing call through.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Test double
# ---------------------------------------------------------------------------


class FakeActivator:
    """Records calls to ``activate`` and returns a fixed string."""

    def __init__(self, result: str = "ACTIVATED:x") -> None:
        self._result = result
        self.calls: list[tuple[str, str]] = []

    def activate(self, name: str, args: str) -> str:
        self.calls.append((name, args))
        return self._result


def _make(activator=None):
    from wentian.tools.skill_tool import LoadSkillTool

    return LoadSkillTool(activator if activator is not None else FakeActivator())


# ===========================================================================
# Contract: identity / schema / category
# ===========================================================================


class TestContract:
    def test_name_is_load_skill(self):
        """tool.name == 'load_skill'."""
        assert _make().name == "load_skill"

    def test_has_description(self):
        """A non-empty model-facing description is declared."""
        tool = _make()
        assert isinstance(tool.description, str)
        assert tool.description.strip()

    def test_schema_object_with_name_required(self):
        """parameters is an object schema; 'name' is a required string."""
        tool = _make()
        schema = tool.parameters
        assert schema["type"] == "object"
        props = schema["properties"]
        assert props["name"]["type"] == "string"
        assert "name" in schema["required"]

    def test_schema_args_optional(self):
        """'args' is an optional string parameter (declared, not required)."""
        tool = _make()
        schema = tool.parameters
        assert schema["properties"]["args"]["type"] == "string"
        assert "args" not in schema["required"]

    def test_category_is_read_only(self):
        """Loading/activating a skill is a READ_ONLY context operation."""
        from wentian.permissions.decision import Category

        tool = _make()
        assert tool.category == Category.READ_ONLY
        assert tool.requires_confirmation is False


# ===========================================================================
# run() — delegation to the injected activator
# ===========================================================================


class TestRun:
    def test_run_delegates_to_activator(self):
        """run returns the activator result and forwards name + args verbatim."""
        fake = FakeActivator("ACTIVATED:x")
        tool = _make(fake)
        result = tool.run({"name": "commit", "args": "fix"})
        assert result == "ACTIVATED:x"
        assert fake.calls == [("commit", "fix")]

    def test_run_defaults_args_to_empty_string(self):
        """Omitted 'args' is forwarded as the empty string."""
        fake = FakeActivator()
        tool = _make(fake)
        tool.run({"name": "commit"})
        assert fake.calls == [("commit", "")]

    def test_run_missing_name_returns_error_string_no_exception(self):
        """Missing 'name' returns a structured error string, never raises."""
        fake = FakeActivator()
        tool = _make(fake)
        result = tool.run({})
        assert isinstance(result, str)
        assert "name" in result.lower()
        # Activator must not be invoked on a malformed call.
        assert fake.calls == []

    def test_run_empty_name_returns_error_string_no_exception(self):
        """Empty/whitespace 'name' is treated as missing — error string, no call."""
        fake = FakeActivator()
        tool = _make(fake)
        result = tool.run({"name": "   "})
        assert isinstance(result, str)
        assert "name" in result.lower()
        assert fake.calls == []

    def test_run_non_string_name_returns_error_string(self):
        """A non-string 'name' yields an error string rather than crashing."""
        fake = FakeActivator()
        tool = _make(fake)
        result = tool.run({"name": 123})
        assert isinstance(result, str)
        assert fake.calls == []
