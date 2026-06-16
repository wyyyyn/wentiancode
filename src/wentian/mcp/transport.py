"""v0.7 · C41 · F52（任务 T83/T84）
MCP Transport 层 — 两种传输实现：StdioTransport + HttpTransport。

设计约束：
- 只 import 标准库 + wentian.config（StdioServerConfig / HttpServerConfig）
- 禁止第三方库（无 httpx / requests / anyio / sdk）
- 禁止 asyncio ——全同步 + threading
- 对上层 client 暴露统一的 Transport ABC（四个方法）

Public API：
- Transport       ABC
- StdioTransport  子进程 stdio，带读取线程 + stderr 抽干线程
- HttpTransport   urllib.request，支持 application/json + text/event-stream（SSE）
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
    """上层 client 统一依赖的传输抽象。

    上层只调用这四个方法，对 stdio/http 传输实现无感。
    """

    @abstractmethod
    def start(self) -> None:
        """拉起子进程或准备 HTTP；起后台读取线程。"""

    @abstractmethod
    def send(self, message: dict) -> None:
        """写出一条 JSON-RPC 消息（线程安全）。"""

    @abstractmethod
    def set_on_message(self, cb: Callable[[dict], None]) -> None:
        """注册收到消息时的回调函数（由读取线程调用）。"""

    @abstractmethod
    def close(self) -> None:
        """终止子进程或关 HTTP；停读取线程（幂等）。"""


# ---------------------------------------------------------------------------
# StdioTransport
# ---------------------------------------------------------------------------


def _drain_stderr(proc: subprocess.Popen) -> None:
    """抽干 stderr，防止管道灌满阻塞子进程（转 debug 日志）。"""
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
    """从子进程 stdout 逐行读取 JSON-RPC 帧，投 on_message 回调。

    - 空行跳过
    - JSON 解析失败跳过（不崩溃）
    - stop_event.set() 或 stdout 关闭后退出
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
    """通过子进程 stdio 与 MCP server 通信。

    Parameters
    ----------
    cfg:
        ``StdioServerConfig`` —— 包含 command/args/env。
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
    # Transport 接口实现
    # ------------------------------------------------------------------

    def set_on_message(self, cb: Callable[[dict], None]) -> None:
        self._on_message = cb

    def start(self) -> None:
        """启动子进程 + 读取线程 + stderr 抽干线程。"""
        cfg = self._cfg
        env = {**os.environ, **cfg.env}
        self._popen = subprocess.Popen(
            [cfg.command, *cfg.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            bufsize=1,  # 行缓冲
        )
        self._stop_event.clear()

        # 后台读取线程：stdout → on_message
        self._read_thread = threading.Thread(
            target=_read_stdout,
            args=(self._popen, self._on_message, self._stop_event),
            daemon=True,
            name=f"mcp-stdio-read-{cfg.name}",
        )
        self._read_thread.start()

        # 后台 stderr 抽干线程：防止管道阻塞
        self._stderr_thread = threading.Thread(
            target=_drain_stderr,
            args=(self._popen,),
            daemon=True,
            name=f"mcp-stdio-stderr-{cfg.name}",
        )
        self._stderr_thread.start()

    def send(self, message: dict) -> None:
        """序列化为 JSON 行并写入子进程 stdin（加锁，线程安全）。"""
        if self._popen is None or self._popen.stdin is None:
            raise RuntimeError("StdioTransport.send() called before start()")
        line = json.dumps(message) + "\n"
        with self._write_lock:
            self._popen.stdin.write(line)
            self._popen.stdin.flush()

    def close(self) -> None:
        """终止子进程；join 读取/stderr 线程（幂等）。"""
        if self._popen is None:
            return
        self._stop_event.set()

        # 关闭 stdin 通知子进程
        try:
            if self._popen.stdin:
                self._popen.stdin.close()
        except Exception:
            pass

        # terminate → 等待 → kill 兜底
        try:
            self._popen.terminate()
            try:
                self._popen.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._popen.kill()
                self._popen.wait(timeout=2)
        except Exception:
            pass

        # join 后台线程（带超时）
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
    """从 SSE 响应流逐行解析 data: 事件，返回所有有效 JSON-RPC 帧列表。

    SSE 格式：
      data: {json}\n
      \n
      (空行表示事件结束)

    特殊值 ``data: [DONE]`` 或连接关闭时停止。
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
                # 空行 → 事件边界
                if data_lines:
                    combined = "".join(data_lines)
                    try:
                        frame = json.loads(combined)
                        frames.append(frame)
                    except json.JSONDecodeError:
                        logger.debug("[mcp-sse-parse-error] %r", combined)
                    data_lines = []
            # 其他字段（event:, id:, retry:）暂时忽略
    except Exception as exc:
        logger.debug("[mcp-sse-stream-error] %s", exc)

    # 收尾：若流结束时还有未提交的 data 行
    if data_lines:
        combined = "".join(data_lines)
        try:
            frame = json.loads(combined)
            frames.append(frame)
        except json.JSONDecodeError:
            pass

    return frames


class HttpTransport(Transport):
    """通过 HTTP POST 与 MCP server 通信（urllib.request，零第三方依赖）。

    每次 ``send()`` 发起一个 HTTP 请求，根据响应 Content-Type 分流：
    - ``application/json``      → 直接 json.loads 并投 on_message
    - ``text/event-stream``     → SSE 逐行解析，每个完整事件投 on_message

    Parameters
    ----------
    cfg:
        ``HttpServerConfig`` —— 包含 url 和 headers。
    """

    def __init__(self, cfg: HttpServerConfig) -> None:
        self._cfg = cfg
        self._on_message: Callable[[dict], None] = lambda _: None
        self._response = None  # 当前打开的响应流（close 时关闭）
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Transport 接口实现
    # ------------------------------------------------------------------

    def set_on_message(self, cb: Callable[[dict], None]) -> None:
        self._on_message = cb

    def start(self) -> None:
        """HTTP transport 无需后台进程，start 是 no-op。"""

    def send(self, message: dict) -> None:
        """POST message → 解析响应 → 投 on_message（同步执行）。"""
        cfg = self._cfg
        body = json.dumps(message).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        # 合并用户配置的自定义 headers（用户头可覆盖 Accept，但不覆盖 Content-Type 以保安全）
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
                # 默认按 application/json 处理
                raw_body = resp.read()
                try:
                    frame = json.loads(raw_body)
                    self._on_message(frame)
                except (json.JSONDecodeError, Exception) as exc:
                    logger.debug("[mcp-http-parse-error] %s", exc)

            with self._lock:
                self._response = None

    def close(self) -> None:
        """关闭当前打开的响应流（幂等）。"""
        with self._lock:
            resp = self._response
            self._response = None
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
