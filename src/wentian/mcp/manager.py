"""v0.7 · C45 · F55/N21（任务 T87）
MCPManager — 逐 Server 发现注册 + 故障隔离 + 生命周期 close_all。

分层铁律：
- 只 import 标准库 + wentian.config + wentian.mcp.transport/client/adapter
- 禁第三方、禁 asyncio
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field

from wentian.config import HttpServerConfig, MCPServerConfig, StdioServerConfig
from wentian.mcp.adapter import MCPTool
from wentian.mcp.client import MCPClient
from wentian.mcp.transport import HttpTransport, StdioTransport
from wentian.tools.registry import ToolRegistry

__all__ = ["MCPManager", "DiscoveryReport"]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class DiscoveryReport:
    """Result of a discover_and_register call.

    Attributes
    ----------
    ok:
        Mapping from server name to number of tools successfully registered.
    failed:
        Mapping from server name to the error reason string.
    """

    ok: dict[str, int] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# MCPManager
# ---------------------------------------------------------------------------


class MCPManager:
    """Manages the lifecycle of MCP server connections and tool registration.

    Parameters
    ----------
    timeout_s:
        Timeout (seconds) passed to each MCPClient for initialize / list_tools.
    """

    def __init__(self, *, timeout_s: float = 30.0) -> None:
        self._timeout_s = timeout_s
        self._clients: dict[str, MCPClient] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def discover_and_register(
        self,
        servers: dict[str, MCPServerConfig],
        registry: ToolRegistry,
    ) -> DiscoveryReport:
        """Connect to each server, list its tools, and register them in *registry*.

        Fault-isolated (N21): a failure in one server is captured in
        ``report.failed`` and does not affect other servers.  No exception is
        raised regardless of individual server failures.

        Parameters
        ----------
        servers:
            Mapping of server name → config (StdioServerConfig | HttpServerConfig).
        registry:
            ToolRegistry to register the discovered MCPTool instances into.

        Returns
        -------
        DiscoveryReport
            ``ok`` maps successful server names to tool counts;
            ``failed`` maps failed server names to error reason strings.
        """
        report = DiscoveryReport()

        for name, scfg in servers.items():
            try:
                n_tools = self._connect_one(name, scfg, registry)
                report.ok[name] = n_tools
            except Exception as exc:  # noqa: BLE001  (fault isolation: catch all)
                reason = str(exc)
                logger.warning("MCPManager: server %r failed: %s", name, reason)
                report.failed[name] = reason

        return report

    def close_all(self) -> None:
        """Close all cached MCPClient connections and clear the cache.

        Idempotent: safe to call multiple times.
        """
        clients = dict(self._clients)
        self._clients.clear()

        for name, client in clients.items():
            try:
                client.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("MCPManager: error closing client %r: %s", name, exc)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _connect_one(
        self,
        name: str,
        scfg: MCPServerConfig,
        registry: ToolRegistry,
    ) -> int:
        """Connect to a single MCP server, register its tools, cache the client.

        Parameters
        ----------
        name:
            Server name (used as registry namespace prefix).
        scfg:
            Server config (StdioServerConfig or HttpServerConfig).
        registry:
            ToolRegistry to register tools into.

        Returns
        -------
        int
            Number of tools registered from this server.

        Raises
        ------
        Exception
            Any failure (OS error, MCPError, timeout) is re-raised so the
            caller (discover_and_register) can record it in report.failed.
        """
        transport = None
        client = None
        try:
            # Build the appropriate transport
            if isinstance(scfg, StdioServerConfig):
                transport = StdioTransport(scfg)
            elif isinstance(scfg, HttpServerConfig):
                transport = HttpTransport(scfg)
            else:
                raise TypeError(f"Unknown server config type: {type(scfg)}")

            # IMPORTANT: create MCPClient before transport.start() so that
            # set_on_message is registered before the read thread is spawned.
            # StdioTransport passes self._on_message BY VALUE to the read thread
            # at start() time; late registration would miss all incoming frames.
            client = MCPClient(transport, timeout_s=self._timeout_s)
            transport.start()

            client.initialize()

            tools = client.list_tools()

            registered = 0
            for remote_tool in tools:
                mcp_tool = MCPTool(name, remote_tool, client)
                try:
                    registry.register(mcp_tool)
                    registered += 1
                except ValueError as dup_exc:
                    # Duplicate name: log a warning and skip (namespace collision
                    # is theoretically impossible under normal operation, but guard
                    # against it without breaking the whole server)
                    warnings.warn(
                        f"MCPManager: duplicate tool name {mcp_tool.name!r} from "
                        f"server {name!r}, skipping: {dup_exc}",
                        UserWarning,
                        stacklevel=2,
                    )

            self._clients[name] = client
            return registered

        except Exception:
            # Ensure transport/client are cleaned up to avoid subprocess leaks.
            # client.close() calls transport.close() internally; if client was
            # never created (TypeError on config), close transport directly.
            if client is not None:
                _safe_close(client)
            else:
                _safe_close_transport(transport)
            raise


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_close(client: MCPClient | None) -> None:
    """Call client.close() swallowing all exceptions."""
    if client is None:
        return
    try:
        client.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("MCPManager: _safe_close client error: %s", exc)


def _safe_close_transport(transport: object) -> None:
    """Call transport.close() swallowing all exceptions."""
    if transport is None:
        return
    try:
        transport.close()  # type: ignore[union-attr]
    except Exception as exc:  # noqa: BLE001
        logger.debug("MCPManager: _safe_close transport error: %s", exc)
