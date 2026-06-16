"""v0.7 · C40 · F51（任务 T81）
wentian.mcp — MCP JSON-RPC 2.0 编解码层（leaf 纯包）。

Leaf 纯包约束：
- 本包内所有模块只 import 标准库
- 禁止 import 任何 wentian.* 或第三方库
- 纯函数 + frozen dataclass，零 IO，零线程

Public API（通过此 __init__ 统一导出）：
- JSONRPC_VERSION  常量 "2.0"
- Response         frozen dataclass：回应帧（id + result/error）
- Notification     frozen dataclass：通知帧（method + params）
- build_request    构造请求帧 dict
- build_notification  构造通知帧 dict（无 id 键）
- parse_message    解析下行帧 → Response | Notification | None

v0.7 · C41 · F52（任务 T83/T84）Transport 层追加导出：
- Transport        ABC（四方法：start / send / set_on_message / close）
- StdioTransport   子进程 stdio 传输实现
- HttpTransport    HTTP/SSE 传输实现（urllib.request，零第三方）
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
    # v0.7 · C41 · F52（任务 T83/T84）
    "Transport",
    "StdioTransport",
    "HttpTransport",
    # v0.7 · C42 · F51/N20（任务 T85）
    "MCPClient",
    "MCPError",
    "RemoteTool",
    # v0.7 · C43 · F53/F54（任务 T86）
    "MCPTool",
    "NAMESPACE_SEP",
    # v0.7 · C45 · F55/N21（任务 T87）
    "MCPManager",
    "DiscoveryReport",
]
