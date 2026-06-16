"""v0.7 · C42 · F51/N20（任务 T85）
tests/test_mcp_client.py — MCPClient 单元测试

覆盖：
  - initialize()：发 initialize 请求 + notifications/initialized 通知
  - list_tools()：解析 result.tools[] → RemoteTool（含 read_only 缺省）
  - call_tool()：拼接 text 块；result.isError=true → MCPError；JSON-RPC error → MCPError
  - 乱序回包：多线程并发，各 id 精确配对
  - 超时：假 transport 不回包 → MCPError 超时
  - close() 后挂起请求被唤醒报错
"""

from __future__ import annotations

import threading
import time
from typing import Callable

import pytest

from wentian.mcp.client import MCPClient, MCPError, RemoteTool
from wentian.mcp.transport import Transport

# ---------------------------------------------------------------------------
# FakeTransport — 测试替身
# ---------------------------------------------------------------------------


class FakeTransport(Transport):
    """测试替身：记录发出的消息，并可按规则回投 response。

    send_rules: dict[str, Callable[[dict], dict | None]]
        key = method 名（如 "initialize"、"tools/list"）
        value = 回调 fn(sent_frame) → 要回投的 response dict，或 None（不回）

    乱序支持：可在 send_rules 里用线程 + delay 实现延迟/乱序回投。
    """

    def __init__(self) -> None:
        self._on_message: Callable[[dict], None] = lambda _: None
        self.sent_messages: list[dict] = []
        self.send_rules: dict[str, Callable[[dict], dict | None]] = {}
        self._lock = threading.Lock()
        self._started = False
        self._closed = False

    # ------------------------------------------------------------------
    # Transport ABC 实现
    # ------------------------------------------------------------------

    def set_on_message(self, cb: Callable[[dict], None]) -> None:
        self._on_message = cb

    def start(self) -> None:
        self._started = True

    def send(self, message: dict) -> None:
        with self._lock:
            self.sent_messages.append(message)
        method = message.get("method", "")
        rule = self.send_rules.get(method)
        if rule is not None:
            response = rule(message)
            if response is not None:
                self._on_message(response)

    def close(self) -> None:
        self._closed = True

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def sent_methods(self) -> list[str]:
        """返回所有已发出帧的 method 列表（包括通知和请求）。"""
        return [m.get("method", "") for m in self.sent_messages]


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _make_response(req_id: int, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _make_error_response(req_id: int, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------------------
# 测试：initialize()
# ---------------------------------------------------------------------------


class TestInitialize:
    def setup_method(self):
        self.transport = FakeTransport()

        # initialize → 回服务端能力
        def on_initialize(frame: dict) -> dict | None:
            return _make_response(
                frame["id"],
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "test-server", "version": "1.0"},
                },
            )

        self.transport.send_rules["initialize"] = on_initialize
        self.transport.start()
        self.client = MCPClient(self.transport, timeout_s=5.0)

    def test_initialize_returns_server_capabilities(self):
        result = self.client.initialize()
        assert result["protocolVersion"] == "2024-11-05"
        assert "capabilities" in result

    def test_initialize_sends_initialized_notification(self):
        self.client.initialize()
        methods = self.transport.sent_methods()
        assert "initialize" in methods
        assert "notifications/initialized" in methods

    def test_initialized_notification_has_no_id(self):
        self.client.initialize()
        notif_frames = [
            m
            for m in self.transport.sent_messages
            if m.get("method") == "notifications/initialized"
        ]
        assert len(notif_frames) == 1
        assert "id" not in notif_frames[0], "notifications/initialized 不应含 id 键"

    def test_initialize_notification_comes_after_request(self):
        self.client.initialize()
        methods = self.transport.sent_methods()
        init_idx = methods.index("initialize")
        notif_idx = methods.index("notifications/initialized")
        assert notif_idx > init_idx, (
            "notifications/initialized 必须在 initialize 请求之后发出"
        )


# ---------------------------------------------------------------------------
# 测试：list_tools()
# ---------------------------------------------------------------------------


class TestListTools:
    def _make_transport_with_tools(self, tools_payload: list[dict]) -> FakeTransport:
        transport = FakeTransport()

        def on_initialize(frame: dict) -> dict | None:
            return _make_response(
                frame["id"],
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "serverInfo": {"name": "s", "version": "1"},
                },
            )

        def on_list_tools(frame: dict) -> dict | None:
            return _make_response(frame["id"], {"tools": tools_payload})

        transport.send_rules["initialize"] = on_initialize
        transport.send_rules["tools/list"] = on_list_tools
        transport.start()
        return transport

    def test_list_tools_returns_remote_tool_list(self):
        tools_payload = [
            {
                "name": "echo",
                "description": "echo text",
                "inputSchema": {"type": "object"},
            },
        ]
        transport = self._make_transport_with_tools(tools_payload)
        client = MCPClient(transport, timeout_s=5.0)
        client.initialize()
        tools = client.list_tools()
        assert len(tools) == 1
        assert isinstance(tools[0], RemoteTool)
        assert tools[0].name == "echo"
        assert tools[0].description == "echo text"

    def test_list_tools_read_only_from_annotations(self):
        tools_payload = [
            {
                "name": "read_file",
                "description": "read",
                "inputSchema": {"type": "object"},
                "annotations": {"readOnlyHint": True},
            },
            {
                "name": "write_file",
                "description": "write",
                "inputSchema": {"type": "object"},
                "annotations": {"readOnlyHint": False},
            },
        ]
        transport = self._make_transport_with_tools(tools_payload)
        client = MCPClient(transport, timeout_s=5.0)
        client.initialize()
        tools = client.list_tools()
        by_name = {t.name: t for t in tools}
        assert by_name["read_file"].read_only is True
        assert by_name["write_file"].read_only is False

    def test_list_tools_read_only_defaults_false_when_no_annotations(self):
        tools_payload = [
            {
                "name": "tool_no_ann",
                "description": "no annotations",
                "inputSchema": {"type": "object"},
            },
        ]
        transport = self._make_transport_with_tools(tools_payload)
        client = MCPClient(transport, timeout_s=5.0)
        client.initialize()
        tools = client.list_tools()
        assert tools[0].read_only is False

    def test_list_tools_read_only_defaults_false_when_annotation_missing_key(self):
        tools_payload = [
            {
                "name": "partial_ann",
                "description": "has annotations but no readOnlyHint",
                "inputSchema": {"type": "object"},
                "annotations": {"title": "My Tool"},  # readOnlyHint 缺失
            },
        ]
        transport = self._make_transport_with_tools(tools_payload)
        client = MCPClient(transport, timeout_s=5.0)
        client.initialize()
        tools = client.list_tools()
        assert tools[0].read_only is False


# ---------------------------------------------------------------------------
# 测试：call_tool()
# ---------------------------------------------------------------------------


class TestCallTool:
    def _make_transport_with_call(
        self,
        result_payload: dict | None = None,
        error_payload: dict | None = None,
    ) -> FakeTransport:
        transport = FakeTransport()

        def on_initialize(frame: dict) -> dict | None:
            return _make_response(
                frame["id"],
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "serverInfo": {"name": "s", "version": "1"},
                },
            )

        def on_call_tool(frame: dict) -> dict | None:
            if error_payload is not None:
                return _make_error_response(
                    frame["id"], error_payload["code"], error_payload["message"]
                )
            return _make_response(frame["id"], result_payload)

        transport.send_rules["initialize"] = on_initialize
        transport.send_rules["tools/call"] = on_call_tool
        transport.start()
        return transport

    def test_call_tool_returns_text_content(self):
        result_payload = {
            "content": [
                {"type": "text", "text": "Hello"},
                {"type": "text", "text": " World"},
            ]
        }
        transport = self._make_transport_with_call(result_payload=result_payload)
        client = MCPClient(transport, timeout_s=5.0)
        client.initialize()
        result = client.call_tool("echo", {"text": "Hello World"})
        assert result == "Hello World"

    def test_call_tool_non_text_content_is_placeholder(self):
        result_payload = {
            "content": [
                {"type": "text", "text": "prefix"},
                {"type": "image", "url": "http://example.com/img.png"},
                {"type": "text", "text": "suffix"},
            ]
        }
        transport = self._make_transport_with_call(result_payload=result_payload)
        client = MCPClient(transport, timeout_s=5.0)
        client.initialize()
        result = client.call_tool("echo", {})
        assert "prefix" in result
        assert "suffix" in result
        assert "[非文本内容已省略]" in result

    def test_call_tool_is_error_raises_mcp_error(self):
        result_payload = {
            "isError": True,
            "content": [{"type": "text", "text": "Tool execution failed"}],
        }
        transport = self._make_transport_with_call(result_payload=result_payload)
        client = MCPClient(transport, timeout_s=5.0)
        client.initialize()
        with pytest.raises(MCPError):
            client.call_tool("failing_tool", {})

    def test_call_tool_jsonrpc_error_raises_mcp_error(self):
        transport = self._make_transport_with_call(
            error_payload={"code": -32601, "message": "Method not found"}
        )
        client = MCPClient(transport, timeout_s=5.0)
        client.initialize()
        with pytest.raises(MCPError):
            client.call_tool("nonexistent_tool", {})

    def test_call_tool_sends_correct_params(self):
        result_payload = {"content": [{"type": "text", "text": "ok"}]}
        transport = self._make_transport_with_call(result_payload=result_payload)
        client = MCPClient(transport, timeout_s=5.0)
        client.initialize()
        client.call_tool("my_tool", {"key": "value"})

        call_frames = [
            m for m in transport.sent_messages if m.get("method") == "tools/call"
        ]
        assert len(call_frames) == 1
        params = call_frames[0]["params"]
        assert params["name"] == "my_tool"
        assert params["arguments"] == {"key": "value"}


# ---------------------------------------------------------------------------
# 测试：乱序回包（核心 N20）
# ---------------------------------------------------------------------------


class TestOutOfOrderResponses:
    """多线程并发 call_tool，回包故意乱序，验证 id 精确配对不串位。"""

    def test_out_of_order_responses_paired_correctly(self):
        transport = FakeTransport()

        def on_initialize(frame: dict) -> dict | None:
            return _make_response(
                frame["id"],
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "serverInfo": {"name": "s", "version": "1"},
                },
            )

        # 按 id 累积发出的请求，稍后在独立线程中乱序回投
        pending_calls: list[dict] = []
        call_lock = threading.Lock()

        def on_call_tool(frame: dict) -> None:
            with call_lock:
                pending_calls.append(frame)
            # 不立即回——返回 None，在主线程确认所有请求发完后乱序回
            return None

        transport.send_rules["initialize"] = on_initialize
        transport.send_rules["tools/call"] = on_call_tool  # type: ignore[assignment]
        transport.start()

        client = MCPClient(transport, timeout_s=5.0)
        client.initialize()

        results: dict[str, str] = {}
        errors: list[Exception] = []

        def call_and_collect(name: str, expected_text: str) -> None:
            try:
                text = client.call_tool(name, {})
                results[name] = text
            except Exception as exc:
                errors.append(exc)

        # 先启动三个线程（它们各自 send 后挂起等待回应）
        threads = [
            threading.Thread(target=call_and_collect, args=("tool_a", "result_a")),
            threading.Thread(target=call_and_collect, args=("tool_b", "result_b")),
            threading.Thread(target=call_and_collect, args=("tool_c", "result_c")),
        ]
        for t in threads:
            t.start()

        # 等所有请求发出
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            with call_lock:
                if len(pending_calls) == 3:
                    break
            time.sleep(0.01)

        with call_lock:
            calls_snapshot = list(pending_calls)

        assert len(calls_snapshot) == 3, f"期望 3 个请求，实际 {len(calls_snapshot)}"

        # 乱序：按 id 降序回投（和发出顺序相反）
        calls_snapshot.sort(key=lambda f: f["id"], reverse=True)
        for frame in calls_snapshot:
            tool_name = frame["params"]["name"]
            # 每个工具的回复文本 = "result_" + tool_name 去掉 "tool_" 前缀
            suffix = tool_name.replace("tool_", "")
            resp = _make_response(
                frame["id"], {"content": [{"type": "text", "text": f"result_{suffix}"}]}
            )
            transport._on_message(resp)

        for t in threads:
            t.join(timeout=5.0)

        assert not errors, f"出现异常: {errors}"
        assert results.get("tool_a") == "result_a"
        assert results.get("tool_b") == "result_b"
        assert results.get("tool_c") == "result_c"


# ---------------------------------------------------------------------------
# 测试：超时
# ---------------------------------------------------------------------------


class TestTimeout:
    def test_call_tool_timeout_raises_mcp_error(self):
        transport = FakeTransport()

        def on_initialize(frame: dict) -> dict | None:
            return _make_response(
                frame["id"],
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "serverInfo": {"name": "s", "version": "1"},
                },
            )

        def on_call_no_reply(frame: dict) -> dict | None:
            return None  # 不回应，触发超时

        transport.send_rules["initialize"] = on_initialize
        transport.send_rules["tools/call"] = on_call_no_reply
        transport.start()

        client = MCPClient(transport, timeout_s=0.2)
        client.initialize()

        with pytest.raises(MCPError, match="[Tt]imeout|超时"):
            client.call_tool("slow_tool", {})

    def test_timeout_cleans_up_waiter(self):
        """超时后 _pending 中不残留 waiter（不泄漏）。"""
        transport = FakeTransport()

        def on_initialize(frame: dict) -> dict | None:
            return _make_response(
                frame["id"],
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "serverInfo": {"name": "s", "version": "1"},
                },
            )

        transport.send_rules["initialize"] = on_initialize
        transport.start()

        client = MCPClient(transport, timeout_s=0.2)
        client.initialize()

        with pytest.raises(MCPError):
            client.call_tool("no_reply", {})

        # 超时后 pending 应为空
        assert len(client._pending) == 0


# ---------------------------------------------------------------------------
# 测试：close() 唤醒挂起请求
# ---------------------------------------------------------------------------


class TestClose:
    def test_close_wakes_pending_requests_with_error(self):
        transport = FakeTransport()

        def on_initialize(frame: dict) -> dict | None:
            return _make_response(
                frame["id"],
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "serverInfo": {"name": "s", "version": "1"},
                },
            )

        def on_call_no_reply(frame: dict) -> dict | None:
            return None  # 不回应

        transport.send_rules["initialize"] = on_initialize
        transport.send_rules["tools/call"] = on_call_no_reply
        transport.start()

        client = MCPClient(transport, timeout_s=30.0)  # 超长超时
        client.initialize()

        error_holder: list[Exception] = []

        def call_in_background() -> None:
            try:
                client.call_tool("blocked_tool", {})
            except MCPError as exc:
                error_holder.append(exc)

        t = threading.Thread(target=call_in_background)
        t.start()

        # 等待请求进入挂起状态
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if len(client._pending) > 0:
                break
            time.sleep(0.01)

        # 关闭 client → 挂起请求应被唤醒
        client.close()
        t.join(timeout=3.0)

        assert not t.is_alive(), "线程应已退出"
        assert len(error_holder) == 1, "应收到一个 MCPError"
        assert isinstance(error_holder[0], MCPError)
