"""v0.7 · C43 · F53/F54 (task T86)
MCPTool — remote MCP tool → adapter to unified Tool abstraction.

Layering rule (adapter.py is the only legal cross-layer import point in the mcp package):
- wentian.tools.base      (Tool, ToolError)
- wentian.permissions.decision  (Category)
- wentian.mcp.client      (MCPClient, RemoteTool, MCPError)
- stdlib
No third-party libs, no asyncio.

Design notes (F53/F54):
- name = f"{server_name}__{remote.name}"  namespace key; use original remote.name when calling
- category determined by remote.read_only: True→READ_ONLY, False→FILE_WRITE (safe default)
- requires_confirmation derived from base class Tool, no need to write manually
- command_arg = None; path_args = ()  MCP parameters cannot be statically parsed, not routed into blocklist/sandbox layer
"""

from __future__ import annotations

from wentian.mcp.client import MCPClient, MCPError, RemoteTool
from wentian.permissions.decision import Category
from wentian.tools.base import Tool, ToolError

__all__ = ["MCPTool", "NAMESPACE_SEP"]

#: Namespace separator; both registry keys and model-visible names use this to separate server and tool names.
NAMESPACE_SEP = "__"


class MCPTool(Tool):
    """Wraps a remote MCP tool as a unified Tool interface.

    Parameters
    ----------
    server_name:
        MCP server identifier, used to form the namespace ``{server_name}__{remote.name}``.
    remote:
        Remote tool descriptor returned from MCPClient.list_tools().
    client:
        Handshaken MCPClient instance responsible for actual RPC calls.
    """

    #: MCP tools do not need static command argument parsing, not routed into blocklist/sandbox layer.
    command_arg: str | None = None
    path_args: tuple[str, ...] = ()

    def __init__(self, server_name: str, remote: RemoteTool, client: MCPClient) -> None:
        # --- Unified Tool contract fields ---
        self.name = f"{server_name}{NAMESPACE_SEP}{remote.name}"
        self.description = remote.description
        self.parameters = remote.input_schema

        # F54 safe default: read_only=True → READ_ONLY; otherwise FILE_WRITE (has side effects)
        self.category = Category.READ_ONLY if remote.read_only else Category.FILE_WRITE

        # Used by permission rule engine; MCP tools skip exact/glob routing, a readable value is sufficient
        self.friendly_name = remote.name

        # Inherits base class default timeout (60.0); uses client._timeout_s if present
        if hasattr(client, "_timeout_s"):
            self.timeout_s = client._timeout_s

        # Internal state: original remote tool name (without namespace prefix)
        self._client = client
        self._remote_name = remote.name

    def run(self, args: dict) -> str:
        """Calls the remote tool and returns the text result.

        Parameters
        ----------
        args:
            Tool argument dict, passed directly to MCPClient.call_tool.

        Returns
        -------
        str
            Text content returned by the remote tool.

        Raises
        ------
        ToolError
            MCPError (timeout/connection closed/remote error/isError) uniformly converted to ToolError,
            message is user-facing and does not expose traceback.
        """
        try:
            return self._client.call_tool(self._remote_name, args)
        except MCPError as exc:
            raise ToolError(f"MCP tool '{self._remote_name}' failed: {exc}") from exc
        except Exception as exc:
            raise ToolError(
                f"MCP tool '{self._remote_name}' unexpected error: {exc}"
            ) from exc
