"""v0.7 · C43 · F53/F54（任务 T86）
MCPTool — 远端 MCP 工具 → 统一 Tool 抽象的适配器。

分层铁律（adapter.py 是 mcp 包唯一合法跨层 import 处）：
- wentian.tools.base      (Tool, ToolError)
- wentian.permissions.decision  (Category)
- wentian.mcp.client      (MCPClient, RemoteTool, MCPError)
- 标准库
禁第三方、禁 asyncio。

设计要点（F53/F54）：
- name = f"{server_name}__{remote.name}"  命名空间键；调用时用原始 remote.name
- category 按 remote.read_only 决定：True→READ_ONLY，False→FILE_WRITE（安全默认）
- requires_confirmation 由基类 Tool 派生，无需自写
- command_arg = None; path_args = ()  MCP 参数无法静态解析，不进黑名单/沙箱层
"""

from __future__ import annotations

from wentian.mcp.client import MCPClient, MCPError, RemoteTool
from wentian.permissions.decision import Category
from wentian.tools.base import Tool, ToolError

__all__ = ["MCPTool", "NAMESPACE_SEP"]

#: 命名空间分隔符；registry 键与模型可见 name 均用此分隔 server 与工具名。
NAMESPACE_SEP = "__"


class MCPTool(Tool):
    """将远端 MCP 工具包装为统一 Tool 接口。

    Parameters
    ----------
    server_name:
        MCP 服务器标识，用于构成命名空间 ``{server_name}__{remote.name}``。
    remote:
        从 MCPClient.list_tools() 返回的远端工具描述符。
    client:
        已握手的 MCPClient 实例，负责实际 RPC 调用。
    """

    #: MCP 工具不需要静态解析命令参数，不进黑名单/沙箱层。
    command_arg: str | None = None
    path_args: tuple[str, ...] = ()

    def __init__(self, server_name: str, remote: RemoteTool, client: MCPClient) -> None:
        # --- 统一 Tool 契约字段 ---
        self.name = f"{server_name}{NAMESPACE_SEP}{remote.name}"
        self.description = remote.description
        self.parameters = remote.input_schema

        # F54 安全默认：read_only=True → READ_ONLY；否则 FILE_WRITE（有副作用）
        self.category = Category.READ_ONLY if remote.read_only else Category.FILE_WRITE

        # 权限规则引擎用；MCP 工具不进精确/glob 路由，给可读值即可
        self.friendly_name = remote.name

        # 继承基类默认 timeout（60.0）；若 client 有 _timeout_s 则取用
        if hasattr(client, "_timeout_s"):
            self.timeout_s = client._timeout_s

        # 内部状态：原始远端工具名（不含命名空间前缀）
        self._client = client
        self._remote_name = remote.name

    def run(self, args: dict) -> str:
        """调用远端工具，返回文本结果。

        Parameters
        ----------
        args:
            工具参数 dict，直接透传给 MCPClient.call_tool。

        Returns
        -------
        str
            远端工具返回的文本内容。

        Raises
        ------
        ToolError
            MCPError（超时/连接关闭/远端 error/isError）统一转换为 ToolError，
            消息面向用户，不暴露 traceback。
        """
        try:
            return self._client.call_tool(self._remote_name, args)
        except MCPError as exc:
            raise ToolError(f"MCP tool '{self._remote_name}' failed: {exc}") from exc
        except Exception as exc:
            raise ToolError(
                f"MCP tool '{self._remote_name}' unexpected error: {exc}"
            ) from exc
