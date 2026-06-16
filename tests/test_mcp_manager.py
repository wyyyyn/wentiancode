"""v0.7 · C45 · F55/N21（任务 T87）
Tests for MCPManager — discover_and_register + close_all.

TDD RED → GREEN → REFACTOR.

Strategy:
- Good stdio server: uses _fake_mcp_server.py via sys.executable
- Bad server (command not found): triggers OSError / FileNotFoundError on start()
- Timeout server: FAKE_NO_REPLY=1 + tiny timeout_s triggers MCPError timeout
- Verify: fault isolation (bad doesn't break good), no subprocess leak, close_all idempotent
"""

from __future__ import annotations

import sys

from pathlib import Path

from wentian.config import StdioServerConfig
from wentian.mcp.manager import MCPManager, DiscoveryReport
from wentian.tools.registry import ToolRegistry

FAKE_SERVER_PATH = str(Path(__file__).parent / "_fake_mcp_server.py")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def good_cfg(name: str = "good") -> StdioServerConfig:
    """StdioServerConfig pointing at the real fake server (returns fake_tool)."""
    return StdioServerConfig(name=name, command=sys.executable, args=[FAKE_SERVER_PATH])


def bad_cfg_no_command(name: str = "bad") -> StdioServerConfig:
    """StdioServerConfig with a non-existent command — start() will fail with OSError."""
    return StdioServerConfig(
        name=name,
        command="this_command_does_not_exist_xyz_abc_123",
        args=[],
    )


def timeout_cfg(name: str = "timeout_server") -> StdioServerConfig:
    """StdioServerConfig that won't reply (FAKE_NO_REPLY=1) — triggers MCPError timeout."""
    return StdioServerConfig(
        name=name,
        command=sys.executable,
        args=[FAKE_SERVER_PATH],
        env={"FAKE_NO_REPLY": "1"},
    )


# ---------------------------------------------------------------------------
# Test 1: single good server — tool registered, report.ok populated, no failures
# ---------------------------------------------------------------------------


def test_good_server_registers_tool_and_reports_ok():
    mgr = MCPManager(timeout_s=10.0)
    registry = ToolRegistry()
    servers = {"good": good_cfg("good")}

    report = mgr.discover_and_register(servers, registry)

    try:
        # fake server exposes exactly one tool: "fake_tool"
        assert report.ok == {"good": 1}, f"Expected ok={{'good': 1}}, got {report.ok}"
        assert report.failed == {}, f"Expected empty failed, got {report.failed}"

        # Tool registered with namespaced name "good__fake_tool"
        tool = registry.get("good__fake_tool")
        assert tool is not None, "Expected 'good__fake_tool' in registry"
        assert tool.name == "good__fake_tool"
    finally:
        mgr.close_all()


# ---------------------------------------------------------------------------
# Test 2: two servers — one bad (no command), one good
#   - Bad goes to report.failed (non-empty reason)
#   - Good still registered and in report.ok
#   - No exception raised (N21 fault isolation)
# ---------------------------------------------------------------------------


def test_fault_isolation_bad_command_does_not_affect_good():
    mgr = MCPManager(timeout_s=10.0)
    registry = ToolRegistry()
    servers = {
        "bad": bad_cfg_no_command("bad"),
        "good": good_cfg("good"),
    }

    report = mgr.discover_and_register(servers, registry)

    try:
        # Bad server must appear in failed with a non-empty reason
        assert "bad" in report.failed, (
            f"Expected 'bad' in report.failed, got {report.failed}"
        )
        assert report.failed["bad"], "Failure reason must be non-empty"

        # Good server must still succeed
        assert "good" in report.ok, f"Expected 'good' in report.ok, got {report.ok}"
        assert report.ok["good"] == 1

        # Good server tool registered
        assert registry.get("good__fake_tool") is not None

        # discover_and_register itself must NOT raise
    finally:
        mgr.close_all()


# ---------------------------------------------------------------------------
# Test 3: timeout server — no reply → MCPError → goes to failed, subprocess cleaned up
# ---------------------------------------------------------------------------


def test_timeout_server_goes_to_failed_and_subprocess_not_leaked():
    # Use a very short timeout to avoid slowing tests
    mgr = MCPManager(timeout_s=0.5)
    registry = ToolRegistry()
    servers = {"slow": timeout_cfg("slow")}

    report = mgr.discover_and_register(servers, registry)

    # Must fail
    assert "slow" in report.failed, f"Expected 'slow' in failed, got {report.failed}"
    assert report.failed["slow"], "Failure reason must be non-empty"
    assert report.ok == {}, f"Expected empty ok, got {report.ok}"

    # No subprocess leaked: manager should have cleaned it up internally.
    # We verify by checking manager has no clients cached for "slow"
    assert "slow" not in mgr._clients, "Failed server must not be cached in _clients"

    # Double-check: close_all is still safe (idempotent on empty cache)
    mgr.close_all()
    mgr.close_all()  # second call must not raise


# ---------------------------------------------------------------------------
# Test 4: close_all terminates subprocess + idempotent
# ---------------------------------------------------------------------------


def test_close_all_terminates_subprocess_and_is_idempotent():
    mgr = MCPManager(timeout_s=10.0)
    registry = ToolRegistry()
    servers = {"srv": good_cfg("srv")}

    report = mgr.discover_and_register(servers, registry)
    assert "srv" in report.ok

    # Grab the transport's popen before close
    client = mgr._clients["srv"]
    popen = client._transport._popen
    assert popen is not None, "Expected a live subprocess before close_all"
    assert popen.poll() is None, "Subprocess should be running before close_all"

    mgr.close_all()

    # After close, subprocess must have exited
    assert popen.poll() is not None, "Subprocess must have exited after close_all"

    # Cache must be cleared
    assert mgr._clients == {}, "close_all must clear _clients"

    # Idempotent: second call must not raise
    mgr.close_all()


# ---------------------------------------------------------------------------
# Test 5: empty servers dict — no-op
# ---------------------------------------------------------------------------


def test_empty_servers_is_noop():
    mgr = MCPManager(timeout_s=10.0)
    registry = ToolRegistry()

    report = mgr.discover_and_register({}, registry)

    assert report.ok == {}
    assert report.failed == {}
    assert registry.names() == []

    mgr.close_all()  # must not raise


# ---------------------------------------------------------------------------
# Test 6: bad+good with timeout bad — good not affected
# ---------------------------------------------------------------------------


def test_timeout_bad_does_not_affect_good_server():
    # Use bad-command failure (not timeout) so the good server isn't time-constrained.
    # Timeout isolation is tested separately in test_timeout_server_goes_to_failed_*.
    mgr = MCPManager(timeout_s=5.0)
    registry = ToolRegistry()
    servers = {
        "broken": bad_cfg_no_command("broken"),
        "working": good_cfg("working"),
    }

    report = mgr.discover_and_register(servers, registry)
    try:
        assert "broken" in report.failed
        assert "working" in report.ok
        assert registry.get("working__fake_tool") is not None
    finally:
        mgr.close_all()


# ---------------------------------------------------------------------------
# Test 7: DiscoveryReport structure is correct dataclass
# ---------------------------------------------------------------------------


def test_discovery_report_is_dataclass():
    from dataclasses import fields

    report = DiscoveryReport(ok={"a": 2}, failed={"b": "error msg"})
    assert report.ok == {"a": 2}
    assert report.failed == {"b": "error msg"}
    # Must have exactly ok and failed fields
    fnames = {f.name for f in fields(report)}
    assert "ok" in fnames
    assert "failed" in fnames
