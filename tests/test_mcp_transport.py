"""v0.7 · C41 · F52（任务 T83/T84）
Transport 层测试：StdioTransport + HttpTransport

设计约束（spec F52）：
- 端到端 stdio 测试用真子进程（sys.executable 跑 _fake_mcp_server.py）
- 所有时序等待用 threading.Event + 超时，不用裸 sleep
- http 测试用 FakeMcpHttpServer（本地回环，端口 0 自动分配）

覆盖场景：
  stdio-1  end-to-end：send initialize → on_message 收到 Response
  stdio-2  stderr flood 不阻塞，不污染 on_message
  stdio-3  close() 后子进程已终止（poll() 非 None）
  http-1   即时 JSON 分支：send → on_message 收到 response
  http-2   SSE 分支：text/event-stream → 正确解析并投 on_message
  http-3   cfg.headers 出现在假 server 收到的请求头里
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from wentian.config import HttpServerConfig, StdioServerConfig
from wentian.mcp.transport import HttpTransport, StdioTransport

# ---------------------------------------------------------------------------
# 将 tests/ 目录加入 sys.path，使 _fake_mcp_server 可直接 import
# ---------------------------------------------------------------------------
_TESTS_DIR = str(Path(__file__).parent)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

# 假 MCP 服务脚本绝对路径
_FAKE_SERVER_SCRIPT = str(Path(__file__).parent / "_fake_mcp_server.py")

# 测试等待超时（秒）
_TIMEOUT = 10.0


def _collect_messages(transport, count: int, timeout: float = _TIMEOUT) -> list[dict]:
    """等待 transport 通过 on_message 回调收到 count 条消息，返回收集到的列表。

    超时仍未收足则返回已收到的部分（让 assert 失败并给出有用信息）。
    """
    received: list[dict] = []
    ready = threading.Event()

    def _on_msg(msg: dict) -> None:
        received.append(msg)
        if len(received) >= count:
            ready.set()

    transport.set_on_message(_on_msg)
    ready.wait(timeout=timeout)
    return received


# ===========================================================================
# stdio 测试
# ===========================================================================


class TestStdioTransport:
    def _make_cfg(self, extra_env: dict | None = None) -> StdioServerConfig:
        env = extra_env or {}
        return StdioServerConfig(
            name="fake-stdio",
            command=sys.executable,
            args=[_FAKE_SERVER_SCRIPT],
            env=env,
        )

    # -----------------------------------------------------------------------
    # stdio-1：端到端 send → response
    # -----------------------------------------------------------------------

    def test_send_and_receive_response(self):
        """StdioTransport start → send initialize → on_message 收到对应 Response 帧。"""
        cfg = self._make_cfg()
        transport = StdioTransport(cfg)
        try:
            received = _collect_messages(transport, count=1)
            # 注：set_on_message 需在 start 之前或 start 之后调用都可以；
            # 但为了保证不遗漏，先 set_on_message 再 start 再 send。
            # 重置收集器
            received.clear()
            ready = threading.Event()
            msgs: list[dict] = []

            def on_msg(msg: dict) -> None:
                msgs.append(msg)
                ready.set()

            transport.set_on_message(on_msg)
            transport.start()

            request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
            transport.send(request)

            ready.wait(timeout=_TIMEOUT)
            assert len(msgs) == 1, f"Expected 1 message, got {msgs}"
            msg = msgs[0]
            assert msg["jsonrpc"] == "2.0"
            assert msg["id"] == 1
            assert "result" in msg
            assert msg["result"]["protocolVersion"] == "2024-11-05"
        finally:
            transport.close()

    # -----------------------------------------------------------------------
    # stdio-2：stderr flood 不阻塞、不污染 on_message
    # -----------------------------------------------------------------------

    def test_stderr_flood_does_not_block_or_pollute(self):
        """FAKE_STDERR_FLOOD=1：stderr 被独立抽干，不阻塞，on_message 只收到 JSON-RPC。"""
        cfg = self._make_cfg(extra_env={"FAKE_STDERR_FLOOD": "1"})
        transport = StdioTransport(cfg)
        msgs: list[dict] = []
        ready = threading.Event()

        def on_msg(msg: dict) -> None:
            msgs.append(msg)
            ready.set()

        transport.set_on_message(on_msg)
        transport.start()
        try:
            transport.send(
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
            )
            ready.wait(timeout=_TIMEOUT)
            assert len(msgs) == 1
            msg = msgs[0]
            # 确保不是 stderr 内容（stderr 写的是 X*65536，不是 JSON-RPC）
            assert msg.get("jsonrpc") == "2.0"
            assert "result" in msg
        finally:
            transport.close()

    # -----------------------------------------------------------------------
    # stdio-3：close() 后子进程已终止
    # -----------------------------------------------------------------------

    def test_close_terminates_process(self):
        """close() 后子进程 poll() 返回非 None（进程已退出）。"""
        cfg = self._make_cfg()
        transport = StdioTransport(cfg)
        transport.set_on_message(lambda _: None)
        transport.start()
        popen = transport._popen  # 访问内部 popen 以验证终止
        assert popen is not None
        assert popen.poll() is None, "进程在 close 前应仍在运行"
        transport.close()
        # 等一小会儿让进程彻底退出
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if popen.poll() is not None:
                break
            time.sleep(0.05)
        assert popen.poll() is not None, "close() 后进程应已终止"

    def test_close_is_idempotent(self):
        """多次调用 close() 不报错（幂等）。"""
        cfg = self._make_cfg()
        transport = StdioTransport(cfg)
        transport.set_on_message(lambda _: None)
        transport.start()
        transport.close()
        transport.close()  # 第二次不应抛出


# ===========================================================================
# http 测试
# ===========================================================================


class TestHttpTransport:
    # -----------------------------------------------------------------------
    # http-1：即时 JSON 分支
    # -----------------------------------------------------------------------

    def test_send_receives_json_response(self):
        """HttpTransport send → application/json 分支 → on_message 收到 response。"""
        from _fake_mcp_server import FakeMcpHttpServer

        with FakeMcpHttpServer(mode="json") as srv:
            cfg = HttpServerConfig(name="fake-http", url=srv.url)
            transport = HttpTransport(cfg)
            transport.set_on_message(lambda _: None)
            transport.start()

            msgs: list[dict] = []
            ready = threading.Event()

            def on_msg(msg: dict) -> None:
                msgs.append(msg)
                ready.set()

            transport.set_on_message(on_msg)

            request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
            transport.send(request)

            ready.wait(timeout=_TIMEOUT)
            assert len(msgs) == 1
            msg = msgs[0]
            assert msg["jsonrpc"] == "2.0"
            assert msg["id"] == 1
            assert "result" in msg

            transport.close()

    # -----------------------------------------------------------------------
    # http-2：SSE 分支
    # -----------------------------------------------------------------------

    def test_send_receives_sse_response(self):
        """HttpTransport send → text/event-stream 分支 → 正确解析并投 on_message。"""
        from _fake_mcp_server import FakeMcpHttpServer

        with FakeMcpHttpServer(mode="sse") as srv:
            cfg = HttpServerConfig(name="fake-sse", url=srv.url)
            transport = HttpTransport(cfg)

            msgs: list[dict] = []
            ready = threading.Event()

            def on_msg(msg: dict) -> None:
                msgs.append(msg)
                ready.set()

            transport.set_on_message(on_msg)
            transport.start()

            request = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
            transport.send(request)

            ready.wait(timeout=_TIMEOUT)
            assert len(msgs) == 1
            msg = msgs[0]
            assert msg["jsonrpc"] == "2.0"
            assert msg["id"] == 2
            assert "result" in msg
            assert "tools" in msg["result"]

            transport.close()

    # -----------------------------------------------------------------------
    # http-3：cfg.headers 透传到假 server 请求头
    # -----------------------------------------------------------------------

    def test_headers_forwarded_to_server(self):
        """HttpServerConfig.headers 出现在假 server 收到的请求头中。"""
        from _fake_mcp_server import FakeMcpHttpServer

        with FakeMcpHttpServer(mode="json") as srv:
            cfg = HttpServerConfig(
                name="fake-auth",
                url=srv.url,
                headers={"Authorization": "Bearer test-token-xyz"},
            )
            transport = HttpTransport(cfg)
            msgs: list[dict] = []
            ready = threading.Event()

            def on_msg(msg: dict) -> None:
                msgs.append(msg)
                ready.set()

            transport.set_on_message(on_msg)
            transport.start()

            request = {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}
            transport.send(request)

            ready.wait(timeout=_TIMEOUT)
            assert len(msgs) == 1, "Should have received a response"

            # 验证假 server 收到了自定义请求头
            assert len(srv.received_headers) >= 1
            last_headers = {k.lower(): v for k, v in srv.received_headers[-1].items()}
            assert "authorization" in last_headers, (
                f"Authorization header not found. Got: {list(last_headers.keys())}"
            )
            assert "test-token-xyz" in last_headers["authorization"]

            transport.close()

    def test_close_is_idempotent(self):
        """多次 close() 不抛异常（幂等）。"""
        from _fake_mcp_server import FakeMcpHttpServer

        with FakeMcpHttpServer(mode="json") as srv:
            cfg = HttpServerConfig(name="fake-http2", url=srv.url)
            transport = HttpTransport(cfg)
            transport.set_on_message(lambda _: None)
            transport.start()
            transport.close()
            transport.close()
