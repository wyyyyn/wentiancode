"""v0.7 · C40 · F51 (task T81)
wentian.mcp — MCP JSON-RPC 2.0 encode/decode layer (leaf pure package).

Leaf pure package constraints:
- All modules in this package only import the standard library
- No imports of any wentian.* or third-party libraries
- Pure functions + frozen dataclasses, zero IO, zero threads

Public API (exported uniformly via this __init__):
- JSONRPC_VERSION  constant "2.0"
- Response         frozen dataclass: response frame (id + result/error)
- Notification     frozen dataclass: notification frame (method + params)
- build_request    construct request frame dict
- build_notification  construct notification frame dict (no id key)
- parse_message    parse downstream frame → Response | Notification | None

v0.7 · C41 · F52 (task T83/T84) Transport layer additional exports:
- Transport        ABC (four methods: start / send / set_on_message / close)
- StdioTransport   subprocess stdio transport implementation
- HttpTransport    HTTP/SSE transport implementation (urllib.request, zero third-party)
"""

from wentian.mcp.protocol import (
    JSONRPC_VERSION,
    Notification,
    Response,
    build_notification,
    build_request,
    parse_message,
)
from wentian.mcp.transport import HttpTransport, StdioTransport, Transport
from wentian.mcp.client import MCPClient, MCPError, RemoteTool
from wentian.mcp.adapter import MCPTool, NAMESPACE_SEP
from wentian.mcp.manager import DiscoveryReport, MCPManager

__all__ = [
    "JSONRPC_VERSION",
    "Response",
    "Notification",
    "build_request",
    "build_notification",
    "parse_message",
    # v0.7 · C41 · F52 (task T83/T84)
    "Transport",
    "StdioTransport",
    "HttpTransport",
    # v0.7 · C42 · F51/N20 (task T85)
    "MCPClient",
    "MCPError",
    "RemoteTool",
    # v0.7 · C43 · F53/F54 (task T86)
    "MCPTool",
    "NAMESPACE_SEP",
    # v0.7 · C45 · F55/N21 (task T87)
    "MCPManager",
    "DiscoveryReport",
]
