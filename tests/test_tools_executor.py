"""Tests for tools/executor.py.

RED-GREEN-REFACTOR cycle for T36: C10 ToolExecutor (F24 robustness +
F26 confirmation gate).

All tests use locally-defined FakeTool subclasses — they never depend on the
six real tools, keeping this layer's contract isolated.
"""
from __future__ import annotations

import time

import pytest

from wentian.tools.base import Tool, ToolError
from wentian.tools.registry import ToolRegistry
from wentian.tools.executor import ToolExecutor, ToolOutcome


# ---------------------------------------------------------------------------
# Helpers — FakeTool builders defined locally
# ---------------------------------------------------------------------------

def _make_tool(
    *,
    name: str = "fake_tool",
    description: str = "A fake tool for testing",
    parameters: dict | None = None,
    timeout_s: float = 60.0,
    requires_confirmation: bool = False,
    run_impl=None,
):
    """Return a concrete Tool subclass instance with the given behaviour."""
    _params = parameters if parameters is not None else {
        "type": "object",
        "properties": {"input": {"type": "string"}},
        "required": ["input"],
    }

    def _default_run(self, args: dict) -> str:  # noqa: ANN001
        return f"ok:{args}"

    cls = type(
        "FakeTool",
        (Tool,),
        {
            "name": name,
            "description": description,
            "parameters": _params,
            "timeout_s": timeout_s,
            "requires_confirmation": requires_confirmation,
            "run": run_impl if run_impl is not None else _default_run,
        },
    )
    return cls()


def _registry(*tools: Tool) -> ToolRegistry:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return reg


def _always(value: bool):
    """A confirm callable that ignores its argument and returns *value*."""
    def _confirm(_desc: str) -> bool:
        return value
    return _confirm


# ---------------------------------------------------------------------------
# 1. Normal execution
# ---------------------------------------------------------------------------

def test_normal_execution_returns_run_value():
    tool = _make_tool(run_impl=lambda self, args: f"echo:{args['input']}")
    ex = ToolExecutor(_registry(tool), confirm=_always(True))

    outcome = ex.execute("c1", "fake_tool", {"input": "hi"})

    assert isinstance(outcome, ToolOutcome)
    assert outcome.call_id == "c1"
    assert outcome.name == "fake_tool"
    assert outcome.content == "echo:hi"
    assert outcome.is_error is False
    assert outcome.denied is False


# ---------------------------------------------------------------------------
# 2. ToolError → is_error, content is the message
# ---------------------------------------------------------------------------

def test_tool_error_is_reported_as_error_with_message():
    def _boom(self, args):
        raise ToolError("file not found: /tmp/nope")

    tool = _make_tool(run_impl=_boom)
    ex = ToolExecutor(_registry(tool), confirm=_always(True))

    outcome = ex.execute("c2", "fake_tool", {"input": "x"})

    assert outcome.is_error is True
    assert outcome.denied is False
    assert "file not found: /tmp/nope" in outcome.content


# ---------------------------------------------------------------------------
# 3. Unexpected exception → is_error, never propagates, content names the type
# ---------------------------------------------------------------------------

def test_unexpected_exception_is_caught_and_described():
    def _boom(self, args):
        raise ValueError("totally unexpected")

    tool = _make_tool(run_impl=_boom)
    ex = ToolExecutor(_registry(tool), confirm=_always(True))

    # Must NOT raise.
    outcome = ex.execute("c3", "fake_tool", {"input": "x"})

    assert outcome.is_error is True
    assert "ValueError" in outcome.content
    assert "totally unexpected" in outcome.content


# ---------------------------------------------------------------------------
# 4. Unregistered tool name → is_error, mentions "not registered"
# ---------------------------------------------------------------------------

def test_unknown_tool_name_is_error():
    tool = _make_tool(name="known_tool")
    ex = ToolExecutor(_registry(tool), confirm=_always(True))

    outcome = ex.execute("c4", "ghost_tool", {"input": "x"})

    assert outcome.is_error is True
    assert outcome.name == "ghost_tool"
    lc = outcome.content.lower()
    assert ("not registered" in lc) or ("未注册" in outcome.content)
    # Available tool names should help the model recover.
    assert "known_tool" in outcome.content


# ---------------------------------------------------------------------------
# 5. arguments=None → is_error, explains argument-parse failure
# ---------------------------------------------------------------------------

def test_none_arguments_is_error():
    tool = _make_tool()
    ex = ToolExecutor(_registry(tool), confirm=_always(True))

    outcome = ex.execute("c5", "fake_tool", None)

    assert outcome.is_error is True
    lc = outcome.content.lower()
    assert ("argument" in lc) or ("参数" in outcome.content)


# ---------------------------------------------------------------------------
# 6. Timeout — sleeping tool returns is_error within ~0.5s
# ---------------------------------------------------------------------------

def test_timeout_returns_error_quickly():
    def _sleep(self, args):
        time.sleep(5.0)
        return "should never get here"

    tool = _make_tool(timeout_s=0.2, run_impl=_sleep)
    ex = ToolExecutor(_registry(tool), confirm=_always(True))

    start = time.monotonic()
    outcome = ex.execute("c6", "fake_tool", {"input": "x"})
    elapsed = time.monotonic() - start

    assert elapsed < 0.5, f"execute should return promptly, took {elapsed:.2f}s"
    assert outcome.is_error is True
    lc = outcome.content.lower()
    assert ("timed out" in lc) or ("timeout" in lc) or ("超时" in outcome.content)


# ---------------------------------------------------------------------------
# 7. requires_confirmation + confirm False → denied, run not called
# ---------------------------------------------------------------------------

def test_confirmation_denied_skips_run():
    calls = {"n": 0}

    def _run(self, args):
        calls["n"] += 1
        return "ran"

    tool = _make_tool(requires_confirmation=True, run_impl=_run)
    ex = ToolExecutor(_registry(tool), confirm=_always(False))

    outcome = ex.execute("c7", "fake_tool", {"input": "x"})

    assert calls["n"] == 0, "run() must not be invoked when confirmation is denied"
    assert outcome.denied is True
    assert outcome.is_error is True
    # Denied content must be model-readable.
    assert ("拒绝" in outcome.content) or ("denied" in outcome.content.lower())


# ---------------------------------------------------------------------------
# 8. confirm receives a description containing tool name + key arguments
# ---------------------------------------------------------------------------

def test_confirm_description_contains_name_and_args():
    seen = {"desc": None}

    def _confirm(desc: str) -> bool:
        seen["desc"] = desc
        return True

    tool = _make_tool(requires_confirmation=True)
    ex = ToolExecutor(_registry(tool), confirm=_confirm)

    ex.execute("c8", "fake_tool", {"input": "delete-everything"})

    assert seen["desc"] is not None
    assert "fake_tool" in seen["desc"]
    assert "delete-everything" in seen["desc"]


# ---------------------------------------------------------------------------
# 9. Read-only tool does NOT trigger confirm
# ---------------------------------------------------------------------------

def test_readonly_tool_does_not_call_confirm():
    confirm_calls = {"n": 0}

    def _confirm(_desc: str) -> bool:
        confirm_calls["n"] += 1
        return True

    tool = _make_tool(requires_confirmation=False)
    ex = ToolExecutor(_registry(tool), confirm=_confirm)

    outcome = ex.execute("c9", "fake_tool", {"input": "x"})

    assert confirm_calls["n"] == 0
    assert outcome.is_error is False


# ---------------------------------------------------------------------------
# 10. confirm True → normal execution
# ---------------------------------------------------------------------------

def test_confirmation_granted_runs_tool():
    def _run(self, args):
        return "side-effect-done"

    tool = _make_tool(requires_confirmation=True, run_impl=_run)
    ex = ToolExecutor(_registry(tool), confirm=_always(True))

    outcome = ex.execute("c10", "fake_tool", {"input": "x"})

    assert outcome.is_error is False
    assert outcome.denied is False
    assert outcome.content == "side-effect-done"
