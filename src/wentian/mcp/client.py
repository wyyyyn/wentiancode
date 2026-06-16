"""v0.7 · C42 · F51/N20（任务 T85）
MCP 会话客户端 — 三步握手 + id/waiter 配对 + 乱序回包 + 超时 + close 唤醒。

设计约束（F51）：
- 只 import 标准库（threading / typing / dataclasses）
- 只 import wentian.mcp.protocol + wentian.mcp.transport
- 禁止 asyncio，全同步 + threading.Event
- 禁止第三方

使用约定：
  调用方先 transport.start() 再实例化 MCPClient，
  或直接在 initialize() 前手动调 transport.start()。
  MCPClient.__init__ 只注册回调，不调 start()。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from wentian.mcp.protocol import (
    Response,
    build_notification,
    build_request,
    parse_message,
)
from wentian.mcp.transport import Transport

__all__ = ["MCPClient", "MCPError", "RemoteTool"]


# ---------------------------------------------------------------------------
# 公共类型
# ---------------------------------------------------------------------------


class MCPError(Exception):
    """MCP 客户端层异常：超时 / 连接关闭 / 远端 error / isError。"""


@dataclass(frozen=True)
class RemoteTool:
    """远端 MCP 工具描述符。"""

    name: str
    description: str
    input_schema: dict
    read_only: bool  # 取自 annotations.readOnlyHint，缺省 False


# ---------------------------------------------------------------------------
# 内部等待槽
# ---------------------------------------------------------------------------


class _Waiter:
    """一个 send_request 调用占据的等待槽。

    Attributes
    ----------
    event:
        由 _route 在收到对应 Response 时（或 close 时）set。
    result:
        成功时填入 Response.result。
    error:
        JSON-RPC error 对象（dict）；或 None。
    closed:
        True 表示连接已关闭（close() 触发），需 raise MCPError。
    """

    __slots__ = ("event", "result", "error", "closed")

    def __init__(self) -> None:
        self.event: threading.Event = threading.Event()
        self.result: dict | None = None
        self.error: dict | None = None
        self.closed: bool = False


# ---------------------------------------------------------------------------
# MCPClient
# ---------------------------------------------------------------------------


class MCPClient:
    """MCP 协议会话客户端。

    线程安全：多线程可并发调用 call_tool；各调用占独立 id/waiter，
    回包按 id 精确配对，乱序回包不会串位。

    Parameters
    ----------
    transport:
        实现 Transport ABC 的传输对象。调用方应先调 transport.start()。
    timeout_s:
        等待单个请求回应的超时秒数，默认 30.0。
    """

    def __init__(self, transport: Transport, *, timeout_s: float = 30.0) -> None:
        self._transport = transport
        self._timeout_s = timeout_s

        # id 分配（锁保护自增）
        self._id_lock = threading.Lock()
        self._next_id: int = 0

        # 待回包槽（锁保护 dict 访问）
        self._pending_lock = threading.Lock()
        self._pending: dict[int, _Waiter] = {}

        # 关闭标志
        self._closed = False

        # 注册消息路由回调
        self._transport.set_on_message(self._route)

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def initialize(self) -> dict:
        """握手第一步：发 initialize 请求，等服务端回应能力；随后发 initialized 通知。

        Returns
        -------
        dict
            服务端返回的 result（包含 protocolVersion / capabilities / serverInfo 等）。
        """
        params = {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "wentian", "version": "0.7.0"},
        }
        response = self._send_request("initialize", params)
        # JSON-RPC error 处理
        if response.error is not None:
            raise MCPError(f"initialize failed: {response.error}")

        # 发 notifications/initialized（通知，无 id，不等回）
        notif = build_notification("notifications/initialized", None)
        self._transport.send(notif)

        return response.result  # type: ignore[return-value]

    def list_tools(self) -> list[RemoteTool]:
        """获取服务端工具列表。

        Returns
        -------
        list[RemoteTool]
            服务端 tools/list result.tools[] 解析为 RemoteTool 列表。
        """
        response = self._send_request("tools/list", None)
        if response.error is not None:
            raise MCPError(f"tools/list failed: {response.error}")

        assert response.result is not None
        tools_raw: list[dict] = response.result.get("tools", [])
        result: list[RemoteTool] = []
        for t in tools_raw:
            annotations: dict = t.get("annotations", {}) or {}
            read_only: bool = bool(annotations.get("readOnlyHint", False))
            result.append(
                RemoteTool(
                    name=t["name"],
                    description=t.get("description", ""),
                    input_schema=t.get("inputSchema", {}),
                    read_only=read_only,
                )
            )
        return result

    def call_tool(self, name: str, arguments: dict) -> str:
        """调用远端工具。

        Parameters
        ----------
        name:
            工具名。
        arguments:
            工具参数 dict。

        Returns
        -------
        str
            result.content[] 中所有 text 块拼接的文本；非 text 块用
            "[非文本内容已省略]" 占位。

        Raises
        ------
        MCPError
            JSON-RPC error 非空，或 result.isError 为真时。
        """
        params = {"name": name, "arguments": arguments}
        response = self._send_request("tools/call", params)

        # JSON-RPC error
        if response.error is not None:
            raise MCPError(f"tools/call JSON-RPC error: {response.error}")

        assert response.result is not None
        result = response.result

        # 远端工具报错（isError）
        if result.get("isError"):
            content_text = _extract_text(result.get("content", []))
            raise MCPError(f"tool '{name}' isError=true: {content_text}")

        return _extract_text(result.get("content", []))

    def close(self) -> None:
        """关闭传输；唤醒所有挂起的 waiter，令其报 MCPError（连接已关）。"""
        self._closed = True
        self._transport.close()

        # 唤醒所有挂起 waiter
        with self._pending_lock:
            waiters = list(self._pending.values())
        for waiter in waiters:
            waiter.closed = True
            waiter.event.set()

    # ------------------------------------------------------------------
    # 内部：id 分配
    # ------------------------------------------------------------------

    def _alloc_id(self) -> int:
        with self._id_lock:
            rid = self._next_id
            self._next_id += 1
        return rid

    # ------------------------------------------------------------------
    # 内部：消息路由（由 transport 的读取线程调用）
    # ------------------------------------------------------------------

    def _route(self, raw: dict) -> None:
        """解析收到的帧，将 Response 精确配对到对应的 waiter。"""
        msg = parse_message(raw)
        if isinstance(msg, Response):
            with self._pending_lock:
                waiter = self._pending.get(msg.id)
            if waiter is not None:
                waiter.result = msg.result
                waiter.error = msg.error
                waiter.event.set()
        # Notification：本版暂不处理（占位忽略）

    # ------------------------------------------------------------------
    # 内部：发请求并阻塞等待回应
    # ------------------------------------------------------------------

    def _send_request(self, method: str, params: dict | None) -> Response:
        """分配 id → 注册 waiter → 发送 → 等待 → 清理并返回 Response。

        Raises
        ------
        MCPError
            超时（event.wait 超过 timeout_s）或连接已关（close() 被调用）时。
        """
        if self._closed:
            raise MCPError("MCPClient is closed")

        rid = self._alloc_id()
        waiter = _Waiter()

        with self._pending_lock:
            self._pending[rid] = waiter

        try:
            frame = build_request(method, params, id=rid)
            self._transport.send(frame)

            fired = waiter.event.wait(timeout=self._timeout_s)
        finally:
            # 无论超时/成功/异常，都清理 waiter，防止泄漏
            with self._pending_lock:
                self._pending.pop(rid, None)

        if not fired:
            raise MCPError(
                f"Timeout waiting for response to '{method}' (id={rid}, timeout={self._timeout_s}s)"
            )

        if waiter.closed:
            raise MCPError("MCPClient connection closed while waiting for response")

        # 重组为 Response 对象（waiter 里存的是拆开的 result/error）
        return Response(id=rid, result=waiter.result, error=waiter.error)


# ---------------------------------------------------------------------------
# 内部工具函数
# ---------------------------------------------------------------------------


def _extract_text(content: list[dict]) -> str:
    """将 content 块列表拼接为文本，非 text 块用占位符替代。"""
    parts: list[str] = []
    for block in content:
        if block.get("type") == "text":
            parts.append(block.get("text", ""))
        else:
            parts.append("[非文本内容已省略]")
    return "".join(parts)
