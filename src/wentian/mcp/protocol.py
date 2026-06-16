"""v0.7 · C40 · F51（任务 T81）
MCP JSON-RPC 2.0 编解码层 — leaf 纯包，零 IO，零线程。

设计约束（spec F51）：
- 只 import 标准库（json / dataclasses / typing）
- 禁止 import 任何 wentian.* 或第三方库
- 纯函数 + frozen dataclass，无副作用

解析语义（客户端下行视角）：
- 含 id + (result | error)  → Response
- 含 method 且无 id         → Notification
- 其余（含纯请求帧等畸形）  → None（容错降级，不抛）
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

JSONRPC_VERSION = "2.0"

# ---------------------------------------------------------------------------
# 数据模型（frozen dataclass，零行为）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Response:
    """JSON-RPC 2.0 回应帧（含 id，且含 result 或 error）。

    Attributes
    ----------
    id:
        与对应请求匹配的整数 id。
    result:
        成功时的结果对象；error 存在时为 None。
    error:
        JSON-RPC error 对象 {code, message, data?}；result 存在时为 None。
        原样保留，不做额外解析。
    """

    id: int
    result: dict | None
    error: dict | None


@dataclass(frozen=True)
class Notification:
    """JSON-RPC 2.0 通知帧（含 method，无 id）。

    Attributes
    ----------
    method:
        通知名称，如 "notifications/initialized"。
    params:
        参数对象；帧中无 params 键时为 None。
    """

    method: str
    params: dict | None


# ---------------------------------------------------------------------------
# 编码：构造发送帧
# ---------------------------------------------------------------------------


def build_request(method: str, params: dict | None, *, id: int) -> dict:
    """构造 JSON-RPC 2.0 请求帧。

    Parameters
    ----------
    method:
        RPC 方法名，如 "tools/list"。
    params:
        参数对象；为 None 时 **不添加** "params" 键（符合规范可选语义）。
    id:
        请求 id（关键字参数，强制命名，防止位置误用）。

    Returns
    -------
    dict
        可直接 json.dumps 的帧字典。
    """
    frame: dict = {"jsonrpc": JSONRPC_VERSION, "id": id, "method": method}
    if params is not None:
        frame["params"] = params
    return frame


def build_notification(method: str, params: dict | None) -> dict:
    """构造 JSON-RPC 2.0 通知帧（无 id 键）。

    Parameters
    ----------
    method:
        通知名称，如 "notifications/initialized"。
    params:
        参数对象；为 None 时 **不添加** "params" 键。

    Returns
    -------
    dict
        可直接 json.dumps 的帧字典，保证不含 "id" 键。
    """
    frame: dict = {"jsonrpc": JSONRPC_VERSION, "method": method}
    if params is not None:
        frame["params"] = params
    return frame


# ---------------------------------------------------------------------------
# 解码：解析收到的帧
# ---------------------------------------------------------------------------


def parse_message(raw: dict) -> Response | Notification | None:
    """将服务端下行的原始字典解析为强类型消息对象。

    解析规则（客户端下行视角）：
    1. 含 "id" 且含 "result" 或 "error"  → Response（缺的那个字段填 None）
    2. 含 "method" 且无 "id"             → Notification（无 params 时填 None）
    3. 其余所有情况（含纯请求帧）         → None（容错降级，不抛异常）

    Parameters
    ----------
    raw:
        已经过 json.loads 的字典；本函数不做网络 IO，不验证 "jsonrpc" 字段。

    Returns
    -------
    Response | Notification | None
    """
    has_id = "id" in raw
    has_result = "result" in raw
    has_error = "error" in raw
    has_method = "method" in raw

    # 回应帧：含 id 且至少含 result 或 error 之一
    if has_id and (has_result or has_error):
        return Response(
            id=raw["id"],
            result=raw.get("result"),
            error=raw.get("error"),
        )

    # 通知帧：含 method 且无 id
    if has_method and not has_id:
        return Notification(
            method=raw["method"],
            params=raw.get("params"),
        )

    # 其余：纯请求帧（含 id + method 但无 result/error）、畸形帧等
    return None
