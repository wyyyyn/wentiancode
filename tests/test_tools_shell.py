"""v0.3 · C9 · F20（任务 T34）

Tests for RunCommandTool — TDD RED → GREEN sequence.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool(root: Path, timeout_s: float = 60.0):
    """Import and instantiate RunCommandTool with optional timeout override."""
    from wentian.tools.shell import RunCommandTool  # noqa: PLC0415

    tool = RunCommandTool(root)
    tool.timeout_s = timeout_s
    return tool


# ---------------------------------------------------------------------------
# T34-1: echo hi — stdout captured, exit code 0, cwd = root
# ---------------------------------------------------------------------------


def test_echo_hi_contains_output(tmp_path):
    tool = _make_tool(tmp_path)
    result = tool.run({"command": "echo hi"})
    assert "hi" in result


def test_echo_hi_exit_code_zero(tmp_path):
    tool = _make_tool(tmp_path)
    result = tool.run({"command": "echo hi"})
    assert "0" in result  # exit code 0 reported


def test_echo_hi_cwd_is_root(tmp_path):
    tool = _make_tool(tmp_path)
    result = tool.run({"command": "pwd"})
    # resolve() handles symlinks (macOS /var → /private/var)
    assert str(tmp_path.resolve()) in result


# ---------------------------------------------------------------------------
# T34-2: exit 3 — non-zero exit does NOT raise ToolError; exit code reported
# ---------------------------------------------------------------------------


def test_nonzero_exit_does_not_raise(tmp_path):
    tool = _make_tool(tmp_path)
    # Must return a string, not raise
    result = tool.run({"command": "exit 3"})
    assert isinstance(result, str)


def test_nonzero_exit_code_in_result(tmp_path):
    tool = _make_tool(tmp_path)
    result = tool.run({"command": "exit 3"})
    assert "3" in result


# ---------------------------------------------------------------------------
# T34-3: stderr captured and labeled
# ---------------------------------------------------------------------------


def test_stderr_captured(tmp_path):
    tool = _make_tool(tmp_path)
    result = tool.run({"command": "echo err 1>&2"})
    assert "err" in result


def test_stderr_labeled(tmp_path):
    """The stderr section must be clearly labeled so the model can distinguish it."""
    tool = _make_tool(tmp_path)
    result = tool.run({"command": "echo err 1>&2"})
    lower = result.lower()
    assert "stderr" in lower


def test_empty_stderr_omitted(tmp_path):
    """When stderr is empty it should not appear in the output."""
    tool = _make_tool(tmp_path)
    result = tool.run({"command": "echo hi"})
    lower = result.lower()
    assert "stderr" not in lower


# ---------------------------------------------------------------------------
# T34-4: timeout → ToolError with "timed out" / "超时"
# ---------------------------------------------------------------------------


def test_timeout_raises_tool_error(tmp_path):
    from wentian.tools.base import ToolError  # noqa: PLC0415

    tool = _make_tool(tmp_path, timeout_s=0.5)
    with pytest.raises(ToolError):
        tool.run({"command": "sleep 5"})


def test_timeout_message_mentions_timeout(tmp_path):
    from wentian.tools.base import ToolError  # noqa: PLC0415

    tool = _make_tool(tmp_path, timeout_s=0.5)
    with pytest.raises(ToolError, match=r"(?i)(timed out|超时|timeout)"):
        tool.run({"command": "sleep 5"})


# ---------------------------------------------------------------------------
# T34-5: output truncation at 10 000 chars
# ---------------------------------------------------------------------------


def _big_command() -> str:
    """Generate > 10 000 chars of stdout via Python one-liner."""
    return f"{sys.executable} -c \"print('x' * 60000)\""


def test_large_output_truncated(tmp_path):
    tool = _make_tool(tmp_path)
    result = tool.run({"command": _big_command()})
    assert len(result) < 15_000  # well under raw 60k


def test_large_output_has_truncation_marker(tmp_path):
    tool = _make_tool(tmp_path)
    result = tool.run({"command": _big_command()})
    lower = result.lower()
    assert any(word in lower for word in ("truncated", "截断", "trimmed", "omitted"))


def test_large_output_has_head_and_tail(tmp_path):
    """Head (first 5 000 chars) and tail (last 5 000 chars) of actual content
    must both appear in the result."""
    tool = _make_tool(tmp_path)
    # Command writes distinct head and tail markers
    cmd = (
        f"{sys.executable} -c \""
        "print('HEAD_MARKER', end=''); "
        "print('x' * 50000, end=''); "
        "print('TAIL_MARKER')"
        "\""
    )
    result = tool.run({"command": cmd})
    assert "HEAD_MARKER" in result
    assert "TAIL_MARKER" in result


# ---------------------------------------------------------------------------
# T34-6: parameter validation
# ---------------------------------------------------------------------------


def test_missing_command_raises(tmp_path):
    from wentian.tools.base import ToolError  # noqa: PLC0415

    tool = _make_tool(tmp_path)
    with pytest.raises(ToolError):
        tool.run({})


def test_non_string_command_raises(tmp_path):
    from wentian.tools.base import ToolError  # noqa: PLC0415

    tool = _make_tool(tmp_path)
    with pytest.raises(ToolError):
        tool.run({"command": 42})
