"""v0.7 · C42 · F51/N20 (task T85)
MCP session client — three-step handshake + id/waiter pairing + out-of-order response + timeout + close wake-up.

Design constraints (F51):
- Only import stdlib (threading / typing / dataclasses)
- Only import wentian.mcp.protocol + wentian.mcp.transport
- No asyncio, all synchronous + threading.Event
- No third-party libraries

Usage convention:
  Caller should call transport.start() before instantiating MCPClient,
  or manually call transport.start() before initialize().
  MCPClient.__init__ only registers callbacks, does not call start().
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
# Public types
# ---------------------------------------------------------------------------


class MCPError(Exception):
    """MCP client-layer exception: timeout / connection closed / remote error / isError."""


@dataclass(frozen=True)
class RemoteTool:
    """Remote MCP tool descriptor."""

    name: str
    description: str
    input_schema: dict
    read_only: bool  # from annotations.readOnlyHint, default False


# ---------------------------------------------------------------------------
# Internal waiter slot
# ---------------------------------------------------------------------------


class _Waiter:
    """Waiter slot occupied by a single send_request call.

    Attributes
    ----------
    event:
        set by _route upon receiving the corresponding Response (or on close).
    result:
        filled with Response.result on success.
    error:
        JSON-RPC error object (dict); or None.
    closed:
        True indicates the connection is closed (triggered by close()), needs to raise MCPError.
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
    """MCP protocol session client.

    Thread-safe: multiple threads can concurrently call call_tool; each call holds an independent id/waiter,
    responses are precisely matched by id, out-of-order responses do not cross-contaminate.

    Parameters
    ----------
    transport:
        Transport object implementing the Transport ABC. Caller should call transport.start() first.
    timeout_s:
        Timeout in seconds for waiting on a single request response, default 30.0.
    """

    def __init__(self, transport: Transport, *, timeout_s: float = 30.0) -> None:
        self._transport = transport
        self._timeout_s = timeout_s

        # id allocation (lock-protected increment)
        self._id_lock = threading.Lock()
        self._next_id: int = 0

        # Pending response slots (lock-protected dict access)
        self._pending_lock = threading.Lock()
        self._pending: dict[int, _Waiter] = {}

        # Close flag
        self._closed = False

        # Register message routing callback
        self._transport.set_on_message(self._route)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def initialize(self) -> dict:
        """Handshake step 1: send initialize request, wait for server capability response; then send initialized notification.

        Returns
        -------
        dict
            result returned by the server (contains protocolVersion / capabilities / serverInfo etc.).
        """
        params = {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "wentian", "version": "0.7.0"},
        }
        response = self._send_request("initialize", params)
        # JSON-RPC error handling
        if response.error is not None:
            raise MCPError(f"initialize failed: {response.error}")

        # Send notifications/initialized (notification, no id, no wait)
        notif = build_notification("notifications/initialized", None)
        self._transport.send(notif)

        return response.result  # type: ignore[return-value]

    def list_tools(self) -> list[RemoteTool]:
        """Fetch server tool list.

        Returns
        -------
        list[RemoteTool]
            Server tools/list result.tools[] parsed into a list of RemoteTool.
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
        """Call a remote tool.

        Parameters
        ----------
        name:
            Tool name.
        arguments:
            Tool arguments dict.

        Returns
        -------
        str
            All text blocks in result.content[] concatenated; non-text blocks replaced with
            "[non-text content omitted]" as placeholder.

        Raises
        ------
        MCPError
            When JSON-RPC error is non-empty, or result.isError is true.
        """
        params = {"name": name, "arguments": arguments}
        response = self._send_request("tools/call", params)

        # JSON-RPC error
        if response.error is not None:
            raise MCPError(f"tools/call JSON-RPC error: {response.error}")

        assert response.result is not None
        result = response.result

        # Remote tool error (isError)
        if result.get("isError"):
            content_text = _extract_text(result.get("content", []))
            raise MCPError(f"tool '{name}' isError=true: {content_text}")

        return _extract_text(result.get("content", []))

    def close(self) -> None:
        """Close transport; wake up all pending waiters, causing them to raise MCPError (connection closed)."""
        self._closed = True
        self._transport.close()

        # Wake up all pending waiters
        with self._pending_lock:
            waiters = list(self._pending.values())
        for waiter in waiters:
            waiter.closed = True
            waiter.event.set()

    # ------------------------------------------------------------------
    # Internal: id allocation
    # ------------------------------------------------------------------

    def _alloc_id(self) -> int:
        with self._id_lock:
            rid = self._next_id
            self._next_id += 1
        return rid

    # ------------------------------------------------------------------
    # Internal: message routing (called by transport's reader thread)
    # ------------------------------------------------------------------

    def _route(self, raw: dict) -> None:
        """Parse received frame, precisely match Response to corresponding waiter."""
        msg = parse_message(raw)
        if isinstance(msg, Response):
            with self._pending_lock:
                waiter = self._pending.get(msg.id)
            if waiter is not None:
                waiter.result = msg.result
                waiter.error = msg.error
                waiter.event.set()
        # Notification: not handled in this version (placeholder, ignored)

    # ------------------------------------------------------------------
    # Internal: send request and block waiting for response
    # ------------------------------------------------------------------

    def _send_request(self, method: str, params: dict | None) -> Response:
        """Allocate id → register waiter → send → wait → clean up and return Response.

        Raises
        ------
        MCPError
            On timeout (event.wait exceeds timeout_s) or connection closed (close() was called).
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
            # Clean up waiter regardless of timeout/success/exception, to prevent leaks
            with self._pending_lock:
                self._pending.pop(rid, None)

        if not fired:
            raise MCPError(
                f"Timeout waiting for response to '{method}' (id={rid}, timeout={self._timeout_s}s)"
            )

        if waiter.closed:
            raise MCPError("MCPClient connection closed while waiting for response")

        # Reassemble into Response object (waiter stores the split result/error)
        return Response(id=rid, result=waiter.result, error=waiter.error)


# ---------------------------------------------------------------------------
# Internal utility functions
# ---------------------------------------------------------------------------


def _extract_text(content: list[dict]) -> str:
    """Concatenate content block list into text, replacing non-text blocks with a placeholder."""
    parts: list[str] = []
    for block in content:
        if block.get("type") == "text":
            parts.append(block.get("text", ""))
        else:
            parts.append("[non-text content omitted]")
    return "".join(parts)
