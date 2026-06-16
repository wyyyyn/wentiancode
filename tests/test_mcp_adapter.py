"""v0.7 · C43 · F53/F54（任务 T86）
Tests for MCPTool adapter — RED phase first, then GREEN.

Strategy:
- Fake MCPClient that implements call_tool (controllable return / MCPError raise)
- Construct RemoteTool directly (frozen dataclass)
- Verify naming, description/parameters pass-through, category/requires_confirmation,
  run() delegates with the stripped remote name (not namespaced), and MCPError→ToolError
"""

from __future__ import annotations

import pytest

from wentian.mcp.client import MCPError, RemoteTool
from wentian.permissions.decision import Category
from wentian.tools.base import ToolError
from wentian.mcp.adapter import MCPTool, NAMESPACE_SEP


# ---------------------------------------------------------------------------
# Fake client
# ---------------------------------------------------------------------------


class FakeClient:
    """Minimal stand-in for MCPClient — only call_tool is needed."""

    def __init__(
        self, *, return_value: str = "ok", raise_error: MCPError | None = None
    ):
        self._return_value = return_value
        self._raise_error = raise_error
        self.calls: list[tuple[str, dict]] = []

    def call_tool(self, name: str, arguments: dict) -> str:
        self.calls.append((name, arguments))
        if self._raise_error is not None:
            raise self._raise_error
        return self._return_value


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_remote(
    name: str = "read_file",
    description: str = "Reads a file",
    input_schema: dict | None = None,
    read_only: bool = True,
) -> RemoteTool:
    return RemoteTool(
        name=name,
        description=description,
        input_schema=input_schema
        or {"type": "object", "properties": {"path": {"type": "string"}}},
        read_only=read_only,
    )


# ---------------------------------------------------------------------------
# Test 1 — Namespaced name, description and parameters pass-through
# ---------------------------------------------------------------------------


def test_name_is_namespaced():
    remote = make_remote(name="read_file")
    tool = MCPTool("fs", remote, FakeClient())
    assert tool.name == f"fs{NAMESPACE_SEP}read_file"


def test_namespace_sep_constant():
    assert NAMESPACE_SEP == "__"


def test_description_passthrough():
    remote = make_remote(description="Reads a file from the filesystem")
    tool = MCPTool("fs", remote, FakeClient())
    assert tool.description == "Reads a file from the filesystem"


def test_parameters_passthrough():
    schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }
    remote = make_remote(input_schema=schema)
    tool = MCPTool("fs", remote, FakeClient())
    assert tool.parameters == schema


# ---------------------------------------------------------------------------
# Test 2 — Category and requires_confirmation (F54 security defaults)
# ---------------------------------------------------------------------------


def test_read_only_tool_has_read_only_category():
    remote = make_remote(read_only=True)
    tool = MCPTool("fs", remote, FakeClient())
    assert tool.category == Category.READ_ONLY


def test_read_only_tool_requires_no_confirmation():
    remote = make_remote(read_only=True)
    tool = MCPTool("fs", remote, FakeClient())
    assert tool.requires_confirmation is False


def test_non_read_only_tool_has_file_write_category():
    remote = make_remote(read_only=False)
    tool = MCPTool("fs", remote, FakeClient())
    assert tool.category == Category.FILE_WRITE


def test_non_read_only_tool_requires_confirmation():
    remote = make_remote(read_only=False)
    tool = MCPTool("fs", remote, FakeClient())
    assert tool.requires_confirmation is True


# ---------------------------------------------------------------------------
# Test 3 — run() delegates with the original (non-namespaced) remote name
# ---------------------------------------------------------------------------


def test_run_uses_original_remote_name():
    """call_tool must receive 'read_file', not 'fs__read_file'."""
    client = FakeClient(return_value="file contents")
    remote = make_remote(name="read_file")
    tool = MCPTool("fs", remote, client)

    args = {"path": "/tmp/foo.txt"}
    tool.run(args)

    assert len(client.calls) == 1
    called_name, called_args = client.calls[0]
    assert called_name == "read_file", f"Expected 'read_file', got '{called_name}'"
    assert called_args == args


def test_run_returns_client_result():
    client = FakeClient(return_value="hello world")
    tool = MCPTool("fs", make_remote(), client)
    assert tool.run({}) == "hello world"


# ---------------------------------------------------------------------------
# Test 4 — MCPError → ToolError translation
# ---------------------------------------------------------------------------


def test_mcp_error_becomes_tool_error():
    client = FakeClient(raise_error=MCPError("remote tool failed"))
    tool = MCPTool("fs", make_remote(), client)

    with pytest.raises(ToolError):
        tool.run({})


def test_tool_error_message_is_readable():
    client = FakeClient(raise_error=MCPError("connection reset"))
    tool = MCPTool("fs", make_remote(), client)

    with pytest.raises(ToolError) as exc_info:
        tool.run({})

    msg = str(exc_info.value)
    assert "connection reset" in msg


def test_tool_error_does_not_expose_traceback():
    """ToolError message should be a clean user-facing string, not a raw traceback."""
    client = FakeClient(raise_error=MCPError("timeout"))
    tool = MCPTool("fs", make_remote(), client)

    with pytest.raises(ToolError) as exc_info:
        tool.run({})

    msg = str(exc_info.value)
    assert "Traceback" not in msg
    assert "File " not in msg


# ---------------------------------------------------------------------------
# Test 5 — friendly_name is assigned (base class requirement)
# ---------------------------------------------------------------------------


def test_friendly_name_is_set():
    remote = make_remote(name="write_file")
    tool = MCPTool("fs", remote, FakeClient())
    # Must be a non-empty string; recommended value is remote.name
    assert isinstance(tool.friendly_name, str)
    assert tool.friendly_name != ""


# ---------------------------------------------------------------------------
# Test 6 — command_arg and path_args are None / empty (MCP tools not in sandbox)
# ---------------------------------------------------------------------------


def test_command_arg_is_none():
    tool = MCPTool("fs", make_remote(), FakeClient())
    assert tool.command_arg is None


def test_path_args_is_empty():
    tool = MCPTool("fs", make_remote(), FakeClient())
    assert tool.path_args == ()
