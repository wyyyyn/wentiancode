"""Tests for tools/executor.py.

RED-GREEN-REFACTOR cycle for T36 (F24 robustness + timeout) and
v0.6 · C35 · F43/F45（任务 T75）— the confirmation gate (F26) is removed;
the executor is now pure parse → timed run. Confirmation/permission gating
moves up to the AgentLoop (five-layer pipeline). ``ToolOutcome.denied`` is
retained as a field but is no longer produced by the executor.

All tests use locally-defined FakeTool subclasses — they never depend on the
six real tools, keeping this layer's contract isolated.
"""

from __future__ import annotations

import time

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
    """Return a concrete Tool subclass instance with the given behaviour.

    ``requires_confirmation`` is set as a class attribute, which shadows the
    base-class derived property — letting these isolation tests pin behaviour
    without depending on ``category``.
    """
    _params = (
        parameters
        if parameters is not None
        else {
            "type": "object",
            "properties": {"input": {"type": "string"}},
            "required": ["input"],
        }
    )

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


# ---------------------------------------------------------------------------
# 1. Normal execution
# ---------------------------------------------------------------------------


def test_normal_execution_returns_run_value():
    tool = _make_tool(run_impl=lambda self, args: f"echo:{args['input']}")
    ex = ToolExecutor(_registry(tool))

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
    ex = ToolExecutor(_registry(tool))

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
    ex = ToolExecutor(_registry(tool))

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
    ex = ToolExecutor(_registry(tool))

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
    ex = ToolExecutor(_registry(tool))

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
    ex = ToolExecutor(_registry(tool))

    start = time.monotonic()
    outcome = ex.execute("c6", "fake_tool", {"input": "x"})
    elapsed = time.monotonic() - start

    assert elapsed < 0.5, f"execute should return promptly, took {elapsed:.2f}s"
    assert outcome.is_error is True
    lc = outcome.content.lower()
    assert ("timed out" in lc) or ("timeout" in lc) or ("超时" in outcome.content)


# ---------------------------------------------------------------------------
# 7. No confirmation gate — side-effect tools run without any callback
#    (v0.6 · C35 · F43/F45 · T75 — F26 replaced by the five-layer pipeline)
# ---------------------------------------------------------------------------


def test_side_effect_tool_runs_without_confirmation_gate():
    """A tool with requires_confirmation=True still runs: gating moved to loop."""
    calls = {"n": 0}

    def _run(self, args):
        calls["n"] += 1
        return "side-effect-done"

    tool = _make_tool(requires_confirmation=True, run_impl=_run)
    ex = ToolExecutor(_registry(tool))

    outcome = ex.execute("c7", "fake_tool", {"input": "x"})

    assert calls["n"] == 1, "run() must execute — the executor no longer gates"
    assert outcome.is_error is False
    assert outcome.denied is False
    assert outcome.content == "side-effect-done"


# ---------------------------------------------------------------------------
# 8. Executor no longer accepts a confirm parameter
# ---------------------------------------------------------------------------


def test_executor_constructor_takes_no_confirm():
    import inspect

    params = inspect.signature(ToolExecutor.__init__).parameters
    assert "confirm" not in params, "confirm gate (F26) is removed in v0.6"


# ---------------------------------------------------------------------------
# 9. ToolOutcome retains the denied field (produced elsewhere now)
# ---------------------------------------------------------------------------


def test_tool_outcome_denied_field_retained():
    oc = ToolOutcome(call_id="c", name="t", content="x", is_error=True, denied=True)
    assert oc.denied is True
