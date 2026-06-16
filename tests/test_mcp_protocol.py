"""v0.7 · C40 · F51（任务 T81）
MCP JSON-RPC 2.0 编解码层测试。

覆盖范围：
- build_request  —— 含/不含 params 的请求帧构造
- build_notification  —— 含/不含 params 的通知帧构造（无 id 键）
- parse_message  —— Response / Notification / None 的解析分派
- 往返语义：build_request 输出的帧不含 result/error，parse_message 应返回 None
"""

import pytest

from wentian.mcp import (
    JSONRPC_VERSION,
    Notification,
    Response,
    build_notification,
    build_request,
    parse_message,
)

# ---------------------------------------------------------------------------
# 1. build_request
# ---------------------------------------------------------------------------


class TestBuildRequest:
    def test_no_params_omits_params_key(self):
        """params=None 时 params 键不出现在帧里。"""
        frame = build_request("tools/list", None, id=1)
        assert frame == {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        assert "params" not in frame

    def test_with_params_includes_params_key(self):
        """params 非 None 时帧中含正确的 params 值。"""
        frame = build_request("tools/call", {"name": "bash"}, id=2)
        assert frame["jsonrpc"] == "2.0"
        assert frame["id"] == 2
        assert frame["method"] == "tools/call"
        assert frame["params"] == {"name": "bash"}

    def test_jsonrpc_version_constant(self):
        """JSONRPC_VERSION 常量值为 '2.0'。"""
        assert JSONRPC_VERSION == "2.0"
        frame = build_request("ping", None, id=0)
        assert frame["jsonrpc"] == JSONRPC_VERSION


# ---------------------------------------------------------------------------
# 2. build_notification
# ---------------------------------------------------------------------------


class TestBuildNotification:
    def test_no_id_key(self):
        """通知帧一律不含 id 键。"""
        frame = build_notification("notifications/initialized", None)
        assert "id" not in frame

    def test_no_params_omits_params_key(self):
        """params=None 时 params 键不出现在帧里。"""
        frame = build_notification("notifications/initialized", None)
        assert frame == {"jsonrpc": "2.0", "method": "notifications/initialized"}

    def test_with_params_includes_params_key(self):
        """params 非 None 时帧中含正确的 params 值。"""
        frame = build_notification("$/progress", {"value": 50})
        assert frame["jsonrpc"] == "2.0"
        assert frame["method"] == "$/progress"
        assert frame["params"] == {"value": 50}
        assert "id" not in frame


# ---------------------------------------------------------------------------
# 3. parse_message — Response
# ---------------------------------------------------------------------------


class TestParseMessageResponse:
    def test_result_response(self):
        """含 id + result 的帧解析为 Response，error 填 None。"""
        raw = {"jsonrpc": "2.0", "id": 1, "result": {"x": 1}}
        msg = parse_message(raw)
        assert isinstance(msg, Response)
        assert msg.id == 1
        assert msg.result == {"x": 1}
        assert msg.error is None

    def test_error_response(self):
        """含 id + error 的帧解析为 Response，result 填 None。"""
        raw = {
            "jsonrpc": "2.0",
            "id": 3,
            "error": {"code": -32600, "message": "Invalid Request"},
        }
        msg = parse_message(raw)
        assert isinstance(msg, Response)
        assert msg.id == 3
        assert msg.result is None
        assert msg.error == {"code": -32600, "message": "Invalid Request"}

    def test_error_with_data_preserved(self):
        """error 对象的 data 字段原样保留。"""
        raw = {
            "jsonrpc": "2.0",
            "id": 5,
            "error": {"code": -1, "message": "oops", "data": {"detail": "x"}},
        }
        msg = parse_message(raw)
        assert isinstance(msg, Response)
        assert msg.error == {"code": -1, "message": "oops", "data": {"detail": "x"}}

    def test_response_is_frozen(self):
        """Response 是 frozen dataclass，不可修改字段。"""
        msg = parse_message({"jsonrpc": "2.0", "id": 1, "result": {}})
        with pytest.raises((AttributeError, TypeError)):
            msg.id = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 4. parse_message — Notification
# ---------------------------------------------------------------------------


class TestParseMessageNotification:
    def test_with_params(self):
        """含 method + params、无 id 的帧解析为 Notification。"""
        raw = {"jsonrpc": "2.0", "method": "x", "params": {}}
        msg = parse_message(raw)
        assert isinstance(msg, Notification)
        assert msg.method == "x"
        assert msg.params == {}

    def test_without_params(self):
        """无 params 字段的通知帧，Notification.params 为 None。"""
        raw = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        msg = parse_message(raw)
        assert isinstance(msg, Notification)
        assert msg.method == "notifications/initialized"
        assert msg.params is None

    def test_notification_is_frozen(self):
        """Notification 是 frozen dataclass，不可修改字段。"""
        msg = parse_message({"jsonrpc": "2.0", "method": "x"})
        with pytest.raises((AttributeError, TypeError)):
            msg.method = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 5. parse_message — 畸形帧容错
# ---------------------------------------------------------------------------


class TestParseMessageMalformed:
    def test_no_id_no_method_returns_none(self):
        """无 id 无 method 的帧返回 None（容错降级，不抛异常）。"""
        assert parse_message({"jsonrpc": "2.0"}) is None

    def test_empty_dict_returns_none(self):
        """空字典返回 None。"""
        assert parse_message({}) is None

    def test_only_id_no_result_no_error_returns_none(self):
        """有 id 但无 result 也无 error 的帧（即纯请求帧）返回 None。

        本客户端只解析「收到的回应/通知」，不处理纯请求帧。
        """
        assert (
            parse_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) is None
        )


# ---------------------------------------------------------------------------
# 6. 往返语义
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_build_request_is_not_parsed_as_notification(self):
        """build_request 输出的帧含 id 但无 result/error，parse_message 应返回 None。

        本客户端只解析服务端下行的回应/通知，不解析自己发出的上行请求帧。
        """
        frame = build_request("tools/list", None, id=1)
        result = parse_message(frame)
        # 含 id 但无 result/error → 既非 Response 也非 Notification
        assert result is None

    def test_build_notification_parsed_as_notification(self):
        """build_notification 输出的帧应能被 parse_message 识别为 Notification。"""
        frame = build_notification("notifications/initialized", None)
        msg = parse_message(frame)
        assert isinstance(msg, Notification)
        assert msg.method == "notifications/initialized"
        assert msg.params is None
