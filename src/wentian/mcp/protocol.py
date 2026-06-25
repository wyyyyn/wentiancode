"""v0.7 · C40 · F51 (task T81)
MCP JSON-RPC 2.0 encode/decode layer — leaf pure package, zero IO, zero threads.

Design constraints (spec F51):
- Only import stdlib (json / dataclasses / typing)
- No import of any wentian.* or third-party libraries
- Pure functions + frozen dataclass, no side effects

Parsing semantics (client downstream view):
- Contains id + (result | error)  → Response
- Contains method and no id         → Notification
- Anything else (including malformed frames)  → None (fault-tolerant fallback, no raise)
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

JSONRPC_VERSION = "2.0"

# ---------------------------------------------------------------------------
# Data models (frozen dataclass, no behavior)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Response:
    """JSON-RPC 2.0 response frame (contains id, and result or error).

    Attributes
    ----------
    id:
        Integer id matching the corresponding request.
    result:
        Result object on success; None when error is present.
    error:
        JSON-RPC error object {code, message, data?}; None when result is present.
        Preserved as-is, no additional parsing.
    """

    id: int
    result: dict | None
    error: dict | None


@dataclass(frozen=True)
class Notification:
    """JSON-RPC 2.0 notification frame (contains method, no id).

    Attributes
    ----------
    method:
        Notification name, e.g. "notifications/initialized".
    params:
        Parameter object; None when the frame has no params key.
    """

    method: str
    params: dict | None


# ---------------------------------------------------------------------------
# Encoding: construct outgoing frames
# ---------------------------------------------------------------------------


def build_request(method: str, params: dict | None, *, id: int) -> dict:
    """Construct a JSON-RPC 2.0 request frame.

    Parameters
    ----------
    method:
        RPC method name, e.g. "tools/list".
    params:
        Parameter object; when None, the "params" key is **not added** (per spec optional semantics).
    id:
        Request id (keyword-only argument, enforced naming to prevent positional misuse).

    Returns
    -------
    dict
        Frame dict ready for json.dumps.
    """
    frame: dict = {"jsonrpc": JSONRPC_VERSION, "id": id, "method": method}
    if params is not None:
        frame["params"] = params
    return frame


def build_notification(method: str, params: dict | None) -> dict:
    """Construct a JSON-RPC 2.0 notification frame (no id key).

    Parameters
    ----------
    method:
        Notification name, e.g. "notifications/initialized".
    params:
        Parameter object; when None, the "params" key is **not added**.

    Returns
    -------
    dict
        Frame dict ready for json.dumps, guaranteed not to contain an "id" key.
    """
    frame: dict = {"jsonrpc": JSONRPC_VERSION, "method": method}
    if params is not None:
        frame["params"] = params
    return frame


# ---------------------------------------------------------------------------
# Decoding: parse received frames
# ---------------------------------------------------------------------------


def parse_message(raw: dict) -> Response | Notification | None:
    """Parse a raw dict from the server downstream into a strongly-typed message object.

    Parsing rules (client downstream view):
    1. Contains "id" and "result" or "error"  → Response (missing field filled with None)
    2. Contains "method" and no "id"             → Notification (None when no params)
    3. All other cases (including pure request frames)         → None (fault-tolerant fallback, no exception raised)

    Parameters
    ----------
    raw:
        Dict already processed by json.loads; this function does no network IO and does not validate the "jsonrpc" field.

    Returns
    -------
    Response | Notification | None
    """
    has_id = "id" in raw
    has_result = "result" in raw
    has_error = "error" in raw
    has_method = "method" in raw

    # Response frame: contains id and at least one of result or error
    if has_id and (has_result or has_error):
        return Response(
            id=raw["id"],
            result=raw.get("result"),
            error=raw.get("error"),
        )

    # Notification frame: contains method and no id
    if has_method and not has_id:
        return Notification(
            method=raw["method"],
            params=raw.get("params"),
        )

    # Anything else: pure request frames (contains id + method but no result/error), malformed frames, etc.
    return None
