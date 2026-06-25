"""v0.7 · C41 · F52 (task T83/T84)
MCP Transport layer — two transport implementations: StdioTransport + HttpTransport.

Design constraints:
- Only import stdlib + wentian.config (StdioServerConfig / HttpServerConfig)
- No third-party libraries (no httpx / requests / anyio / sdk)
- No asyncio — fully synchronous + threading
- Expose a unified Transport ABC (four methods) to upper-layer clients

Public API:
- Transport       ABC
- StdioTransport  subprocess stdio, with reader thread + stderr drain thread
- HttpTransport   urllib.request, supports application/json + text/event-stream (SSE)
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import urllib.request
from abc import ABC, abstractmethod
from typing import Callable

from wentian.config import HttpServerConfig, StdioServerConfig

__all__ = ["Transport", "StdioTransport", "HttpTransport"]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Transport ABC
# ---------------------------------------------------------------------------


class Transport(ABC):
    """Transport abstraction that upper-layer clients depend on uniformly.

    Upper-layer code calls only these four methods, transport-implementation-agnostic.
    """

    @abstractmethod
    def start(self) -> None:
        """Start subprocess or prepare HTTP; launch background reader thread."""

    @abstractmethod
    def send(self, message: dict) -> None:
        """Write out a JSON-RPC message (thread-safe)."""

    @abstractmethod
    def set_on_message(self, cb: Callable[[dict], None]) -> None:
        """Register callback function invoked when a message is received (called by the reader thread)."""

    @abstractmethod
    def close(self) -> None:
        """Terminate subprocess or close HTTP; stop reader thread (idempotent)."""


# ---------------------------------------------------------------------------
# StdioTransport
# ---------------------------------------------------------------------------


def _drain_stderr(proc: subprocess.Popen) -> None:
    """Drain stderr to prevent pipe buffer from filling and blocking the subprocess (forwarded to debug log)."""
    try:
        assert proc.stderr is not None
        for line in proc.stderr:
            logger.debug("[mcp-stderr] %s", line.rstrip("\n"))
    except Exception:
        pass


def _read_stdout(
    proc: subprocess.Popen,
    on_message: Callable[[dict], None],
    stop_event: threading.Event,
) -> None:
    """Read JSON-RPC frames line by line from subprocess stdout and dispatch to on_message callback.

    - Empty lines are skipped
    - JSON parse failures are skipped (no crash)
    - Exits when stop_event.set() is called or stdout is closed
    """
    try:
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            if stop_event.is_set():
                break
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                frame = json.loads(raw_line)
            except json.JSONDecodeError:
                logger.debug("[mcp-stdout-parse-error] %r", raw_line)
                continue
            try:
                on_message(frame)
            except Exception as exc:
                logger.debug("[mcp-on_message-error] %s", exc)
    except Exception:
        pass


class StdioTransport(Transport):
    """Communicate with the MCP server via subprocess stdio.

    Parameters
    ----------
    cfg:
        ``StdioServerConfig`` — contains command/args/env.
    """

    def __init__(self, cfg: StdioServerConfig) -> None:
        self._cfg = cfg
        self._popen: subprocess.Popen | None = None
        self._read_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._write_lock = threading.Lock()
        self._on_message: Callable[[dict], None] = lambda _: None

    # ------------------------------------------------------------------
    # Transport interface implementation
    # ------------------------------------------------------------------

    def set_on_message(self, cb: Callable[[dict], None]) -> None:
        self._on_message = cb

    def start(self) -> None:
        """Start subprocess + reader thread + stderr drain thread."""
        cfg = self._cfg
        env = {**os.environ, **cfg.env}
        self._popen = subprocess.Popen(
            [cfg.command, *cfg.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            bufsize=1,  # line-buffered
        )
        self._stop_event.clear()

        # Background reader thread: stdout → on_message
        self._read_thread = threading.Thread(
            target=_read_stdout,
            args=(self._popen, self._on_message, self._stop_event),
            daemon=True,
            name=f"mcp-stdio-read-{cfg.name}",
        )
        self._read_thread.start()

        # Background stderr drain thread: prevents pipe blocking
        self._stderr_thread = threading.Thread(
            target=_drain_stderr,
            args=(self._popen,),
            daemon=True,
            name=f"mcp-stdio-stderr-{cfg.name}",
        )
        self._stderr_thread.start()

    def send(self, message: dict) -> None:
        """Serialize to a JSON line and write to subprocess stdin (locked, thread-safe)."""
        if self._popen is None or self._popen.stdin is None:
            raise RuntimeError("StdioTransport.send() called before start()")
        line = json.dumps(message) + "\n"
        with self._write_lock:
            self._popen.stdin.write(line)
            self._popen.stdin.flush()

    def close(self) -> None:
        """Terminate subprocess; join reader/stderr threads (idempotent)."""
        if self._popen is None:
            return
        self._stop_event.set()

        # Close stdin to signal the subprocess
        try:
            if self._popen.stdin:
                self._popen.stdin.close()
        except Exception:
            pass

        # terminate → wait → kill as fallback
        try:
            self._popen.terminate()
            try:
                self._popen.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._popen.kill()
                self._popen.wait(timeout=2)
        except Exception:
            pass

        # Join background threads (with timeout)
        if self._read_thread is not None:
            self._read_thread.join(timeout=3)
            self._read_thread = None
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=3)
            self._stderr_thread = None

        self._popen = None


# ---------------------------------------------------------------------------
# HttpTransport
# ---------------------------------------------------------------------------


def _parse_sse_stream(response) -> list[dict]:
    """Parse data: events line by line from an SSE response stream; return a list of all valid JSON-RPC frames.

    SSE format:
      data: {json}\n
      \n
      (empty line signals end of event)

    Stops on the special value ``data: [DONE]`` or when the connection closes.
    """
    frames: list[dict] = []
    data_lines: list[str] = []

    try:
        for raw_bytes in response:
            line = (
                raw_bytes.decode("utf-8")
                if isinstance(raw_bytes, (bytes, bytearray))
                else raw_bytes
            )
            line = line.rstrip("\r\n")

            if line.startswith("data:"):
                value = line[5:].lstrip(" ")
                if value == "[DONE]":
                    break
                data_lines.append(value)
            elif line == "":
                # empty line → event boundary
                if data_lines:
                    combined = "".join(data_lines)
                    try:
                        frame = json.loads(combined)
                        frames.append(frame)
                    except json.JSONDecodeError:
                        logger.debug("[mcp-sse-parse-error] %r", combined)
                    data_lines = []
            # Other fields (event:, id:, retry:) are ignored for now
    except Exception as exc:
        logger.debug("[mcp-sse-stream-error] %s", exc)

    # Cleanup: flush any uncommitted data lines when the stream ends
    if data_lines:
        combined = "".join(data_lines)
        try:
            frame = json.loads(combined)
            frames.append(frame)
        except json.JSONDecodeError:
            pass

    return frames


class HttpTransport(Transport):
    """Communicate with the MCP server via HTTP POST (urllib.request, zero third-party dependencies).

    Each ``send()`` issues one HTTP request; response is dispatched by Content-Type:
    - ``application/json``      → json.loads directly and dispatch to on_message
    - ``text/event-stream``     → SSE parsed line by line; each complete event dispatched to on_message

    Parameters
    ----------
    cfg:
        ``HttpServerConfig`` — contains url and headers.
    """

    def __init__(self, cfg: HttpServerConfig) -> None:
        self._cfg = cfg
        self._on_message: Callable[[dict], None] = lambda _: None
        self._response = None  # Currently open response stream (closed on close())
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Transport interface implementation
    # ------------------------------------------------------------------

    def set_on_message(self, cb: Callable[[dict], None]) -> None:
        self._on_message = cb

    def start(self) -> None:
        """HTTP transport requires no background process; start is a no-op."""

    def send(self, message: dict) -> None:
        """POST message → parse response → dispatch to on_message (synchronous)."""
        cfg = self._cfg
        body = json.dumps(message).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        # Merge user-configured custom headers (user headers may override Accept, but not Content-Type for safety)
        for k, v in cfg.headers.items():
            headers[k] = v

        req = urllib.request.Request(
            cfg.url,
            data=body,
            headers=headers,
            method="POST",
        )

        with urllib.request.urlopen(req) as resp:
            with self._lock:
                self._response = resp

            content_type: str = resp.headers.get("Content-Type", "")

            if "text/event-stream" in content_type:
                frames = _parse_sse_stream(resp)
                for frame in frames:
                    try:
                        self._on_message(frame)
                    except Exception as exc:
                        logger.debug("[mcp-http-on_message-error] %s", exc)
            else:
                # Default: treat as application/json
                raw_body = resp.read()
                try:
                    frame = json.loads(raw_body)
                    self._on_message(frame)
                except (json.JSONDecodeError, Exception) as exc:
                    logger.debug("[mcp-http-parse-error] %s", exc)

            with self._lock:
                self._response = None

    def close(self) -> None:
        """Close the currently open response stream (idempotent)."""
        with self._lock:
            resp = self._response
            self._response = None
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
