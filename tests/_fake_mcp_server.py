"""v0.7 · C41 · F52（任务 T83/T84）
假 MCP 服务端——两种模式：

1. **stdio 模式**（__main__）：
   可直接 `python tests/_fake_mcp_server.py` 运行；
   循环读 stdin、按 method 回固定 JSON-RPC 2.0 响应行。

   环境变量控制异常模式：
   - FAKE_NO_REPLY=1  ：收到请求后不回复（模拟超时）
   - FAKE_STDERR_FLOOD=1：先往 stderr 写大量内容、再正常回复

2. **http 模式**（FakeMcpHttpServer context manager）：
   用 http.server.HTTPServer + 线程跑本地回环服务；
   端口 0 自动分配，可配置「即时 JSON」或「SSE 事件流」响应；
   记录所有收到的请求头，供测试断言。
"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

# ---------------------------------------------------------------------------
# 公共常量
# ---------------------------------------------------------------------------

INIT_RESULT = {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "serverInfo": {"name": "fake", "version": "0"},
}

TOOLS_LIST_RESULT = {
    "tools": [
        {
            "name": "fake_tool",
            "description": "A fake tool for testing",
            "inputSchema": {"type": "object", "properties": {}},
        }
    ]
}

TOOLS_CALL_RESULT = {"content": [{"type": "text", "text": "ok"}]}


def _make_response(req_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _make_error(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _dispatch(frame: dict) -> dict | None:
    """根据 method 返回回应帧字典；无需回复则返回 None。"""
    method = frame.get("method", "")
    req_id = frame.get("id")

    # 通知帧（无 id）—— 不需要回复
    if req_id is None:
        return None

    if method == "initialize":
        return _make_response(req_id, INIT_RESULT)
    elif method == "tools/list":
        return _make_response(req_id, TOOLS_LIST_RESULT)
    elif method == "tools/call":
        return _make_response(req_id, TOOLS_CALL_RESULT)
    else:
        return _make_error(req_id, -32601, f"Method not found: {method}")


# ---------------------------------------------------------------------------
# stdio 模式：作为可执行脚本运行
# ---------------------------------------------------------------------------


def _run_stdio() -> None:  # pragma: no cover
    """stdio 循环：读 stdin → dispatch → 写 stdout。"""
    no_reply = os.environ.get("FAKE_NO_REPLY", "") == "1"
    stderr_flood = os.environ.get("FAKE_STDERR_FLOOD", "") == "1"

    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue

        try:
            frame = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        if no_reply:
            # 收到请求，不回复（让上层超时）
            continue

        response = _dispatch(frame)
        if response is None:
            continue

        if stderr_flood:
            # 先往 stderr 写大量内容（测试 stderr 抽干不阻塞）
            sys.stderr.write("X" * 65536 + "\n")
            sys.stderr.flush()

        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    _run_stdio()


# ---------------------------------------------------------------------------
# http 模式：FakeMcpHttpServer context manager
# ---------------------------------------------------------------------------


class _FakeMcpHandler(BaseHTTPRequestHandler):
    """极简 HTTP 处理器，把请求头记录进 server.received_headers。"""

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: ANN001
        # 静默日志，避免污染测试输出
        pass

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else ""

        # 记录请求头（每次请求追加一条）
        self.server.received_headers.append(dict(self.headers))  # type: ignore[attr-defined]

        try:
            frame = json.loads(body) if body else {}
        except json.JSONDecodeError:
            frame = {}

        response_dict = _dispatch(frame) or {}
        mode: str = self.server.response_mode  # type: ignore[attr-defined]

        if mode == "json":
            self._send_json(response_dict)
        elif mode == "sse":
            self._send_sse(response_dict)
        else:
            self._send_json(response_dict)

    def _send_json(self, data: dict) -> None:
        encoded = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_sse(self, data: dict) -> None:
        """以 text/event-stream 格式发送单个 SSE 事件后关闭连接。"""
        event_data = json.dumps(data)
        # SSE 格式：data: {json}\n\n
        payload = f"data: {event_data}\n\n".encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        self.wfile.flush()


class FakeMcpHttpServer:
    """可作为 context manager 使用的假 MCP HTTP 服务。

    Parameters
    ----------
    mode:
        ``"json"`` — 每次请求立即返回 ``application/json``。
        ``"sse"``  — 每次请求返回 ``text/event-stream`` 单事件流。

    Attributes
    ----------
    url:
        服务可访问的完整 URL，``start()`` 后有效。
    received_headers:
        收到的所有请求头列表（按顺序追加）。
    """

    def __init__(self, mode: str = "json") -> None:
        self.mode = mode
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.url: str = ""
        self.received_headers: list[dict] = []

    def start(self) -> "FakeMcpHttpServer":
        server = HTTPServer(("127.0.0.1", 0), _FakeMcpHandler)
        server.response_mode = self.mode  # type: ignore[attr-defined]
        server.received_headers = self.received_headers  # type: ignore[attr-defined]
        self._server = server
        port = server.server_address[1]
        self.url = f"http://127.0.0.1:{port}/"
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "FakeMcpHttpServer":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()
