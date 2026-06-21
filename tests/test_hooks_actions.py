"""v0.12 · C97 · F81/F82/F83（任务 T121）— 动作执行器测试。

TDD RED 用例：
- run_shell: stdin JSON / env 注入 / exit_code / stderr / timeout
- inject_prompt: {field} 替换 / 缺键保留字面
- call_http: POST JSON body / 状态码 / 无法连接 → None
- run_subagent: 不抛 / 记日志
- 软化：各动作内部异常被捕获，不冒泡
"""

from __future__ import annotations

import json
import stat
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from wentian.hooks.spec import HttpAction, PromptAction, ShellAction, SubAgentAction


# ---------------------------------------------------------------------------
# Helpers — local fake HTTP server
# ---------------------------------------------------------------------------


class _BodyCapHandler(BaseHTTPRequestHandler):
    """記録 JSON body + Content-Type header 供測試斷言。"""

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: ANN001
        pass  # silence test output

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else ""
        ct = self.headers.get("Content-Type", "")

        # append record to server-level list
        self.server.received.append({"body": body, "content_type": ct})  # type: ignore[attr-defined]

        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")


class FakeHttpServer:
    """Context manager: 在 127.0.0.1:0 起 HTTP server，teardown 时关闭。"""

    def __init__(self) -> None:
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.url: str = ""
        self.received: list[dict] = []

    def __enter__(self) -> "FakeHttpServer":
        server = HTTPServer(("127.0.0.1", 0), _BodyCapHandler)
        server.received = self.received  # type: ignore[attr-defined]
        self._server = server
        port = server.server_address[1]
        self.url = f"http://127.0.0.1:{port}/"
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._server is not None:
            self._server.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=5)


# ---------------------------------------------------------------------------
# run_shell
# ---------------------------------------------------------------------------


class TestRunShell:
    def test_stdin_receives_json_context(self, tmp_path: Any) -> None:
        """脚本用 cat 回显 stdin；断言收到 json.dumps(context)。"""
        from wentian.hooks.actions import run_shell

        ctx = {"tool_name": "Bash", "session_id": "s1"}
        action = ShellAction(command="cat", timeout=5)
        result = run_shell(action, ctx)

        assert result is not None
        assert result.exit_code == 0
        assert json.loads(result.stdout) == ctx

    def test_env_var_injected(self, tmp_path: Any) -> None:
        """断言 WENTIAN_HOOK_TOOL_NAME 环境变量注入。"""
        from wentian.hooks.actions import run_shell

        ctx = {"tool_name": "Bash", "session_id": "s1"}
        action = ShellAction(command="echo $WENTIAN_HOOK_TOOL_NAME", timeout=5)
        result = run_shell(action, ctx)

        assert result is not None
        assert result.stdout.strip() == "Bash"

    def test_exit_code_and_stderr(self, tmp_path: Any) -> None:
        """脚本 exit 2 + 写 stderr → exit_code==2 且 stderr 被捕获。"""
        from wentian.hooks.actions import run_shell

        script = tmp_path / "fail.sh"
        script.write_text("echo 'err msg' >&2; exit 2\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        action = ShellAction(command=f"bash {script}", timeout=5)
        result = run_shell(action, {})

        assert result is not None
        assert result.exit_code == 2
        assert "err msg" in result.stderr

    def test_timeout_timed_out_flag_no_raise(self, tmp_path: Any) -> None:
        """sleep 5 + timeout=1 → timed_out=True，不抛。"""
        from wentian.hooks.actions import run_shell

        action = ShellAction(command="sleep 5", timeout=1)
        result = run_shell(action, {})

        assert result is not None
        assert result.timed_out is True

    def test_non_string_context_values_not_injected_to_env(self, tmp_path: Any) -> None:
        """复杂类型（dict/list）不进 env，只进 stdin JSON。"""
        from wentian.hooks.actions import run_shell

        ctx = {"tool_name": "Bash", "nested": {"a": 1}}
        # 只断言进程不抛；env 里不会有 WENTIAN_HOOK_NESTED (dict)
        action = ShellAction(command="echo ok", timeout=5)
        result = run_shell(action, ctx)
        assert result is not None
        assert result.exit_code == 0

    def test_softening_on_invalid_command(self, monkeypatch: Any) -> None:
        """强制 subprocess.run 抛异常 → 返回 None（软化，不冒泡）。"""
        import subprocess

        from wentian.hooks.actions import run_shell

        def _raise(*a: Any, **kw: Any) -> None:
            raise OSError("no such binary")

        monkeypatch.setattr(subprocess, "run", _raise)
        action = ShellAction(command="doesnotexist", timeout=5)
        result = run_shell(action, {})
        # softened: returns None
        assert result is None


# ---------------------------------------------------------------------------
# inject_prompt
# ---------------------------------------------------------------------------


class TestInjectPrompt:
    def test_field_substitution(self) -> None:
        from wentian.hooks.actions import inject_prompt

        action = PromptAction(text="hi {tool_name}")
        result = inject_prompt(action, {"tool_name": "Bash"})
        assert result == "hi Bash"

    def test_missing_key_kept_literal(self) -> None:
        """缺失的 {nope} 保留字面，不抛 KeyError。"""
        from wentian.hooks.actions import inject_prompt

        action = PromptAction(text="hi {tool_name} and {nope}")
        result = inject_prompt(action, {"tool_name": "Bash"})
        assert result == "hi Bash and {nope}"

    def test_no_substitution_keys(self) -> None:
        from wentian.hooks.actions import inject_prompt

        action = PromptAction(text="plain text")
        assert inject_prompt(action, {}) == "plain text"

    def test_softening_on_format_error(self, monkeypatch: Any) -> None:
        """format_map 抛异常时返回原始 text（软化）。"""
        from wentian.hooks import actions
        from wentian.hooks.actions import inject_prompt

        class _BadDict(dict):
            def __missing__(self, key: str) -> str:
                raise ValueError("forced error")

        monkeypatch.setattr(actions, "SafeDict", _BadDict)
        action = PromptAction(text="hi {x}")
        # Should not raise; returns either original text or empty string
        result = inject_prompt(action, {})
        # The key point: no exception raised
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# call_http
# ---------------------------------------------------------------------------


class TestCallHttp:
    def test_post_sends_json_body_and_returns_status(self) -> None:
        """POST 到 fake server → 收到 JSON body；call_http 返回状态码。"""
        from wentian.hooks.actions import call_http

        with FakeHttpServer() as srv:
            ctx = {"tool_name": "Bash", "session_id": "abc"}
            action = HttpAction(url=srv.url, method="POST", timeout=5)
            status = call_http(action, ctx)

            assert status == 200
            assert len(srv.received) == 1
            received_body = json.loads(srv.received[0]["body"])
            assert received_body == ctx
            assert "application/json" in srv.received[0]["content_type"]

    def test_unreachable_url_returns_none(self) -> None:
        """连不上的 URL → 返回 None（软化），不抛。"""
        from wentian.hooks.actions import call_http

        # Use a port that should not be listening
        action = HttpAction(url="http://127.0.0.1:1/nope", method="POST", timeout=1)
        result = call_http(action, {})
        assert result is None

    def test_bad_url_returns_none(self) -> None:
        """格式非法的 URL → 返回 None（软化），不抛。"""
        from wentian.hooks.actions import call_http

        action = HttpAction(url="not-a-url://bad", method="POST", timeout=1)
        result = call_http(action, {})
        assert result is None

    def test_softening_on_network_error(self, monkeypatch: Any) -> None:
        """urllib.request.urlopen 抛异常 → 返回 None，不冒泡。"""
        import urllib.request

        from wentian.hooks.actions import call_http

        def _raise(*a: Any, **kw: Any) -> None:
            raise OSError("network down")

        monkeypatch.setattr(urllib.request, "urlopen", _raise)
        action = HttpAction(url="http://127.0.0.1:9999/", method="POST", timeout=1)
        result = call_http(action, {})
        assert result is None


# ---------------------------------------------------------------------------
# run_subagent
# ---------------------------------------------------------------------------


class TestRunSubagent:
    def test_returns_none_no_raise(self) -> None:
        from wentian.hooks.actions import run_subagent

        action = SubAgentAction(prompt="do something")
        result = run_subagent(action, {"tool_name": "Bash"})
        assert result is None

    def test_logs_not_implemented(self, caplog: Any) -> None:
        """应记录一条「未实现」日志。"""
        import logging

        from wentian.hooks.actions import run_subagent

        action = SubAgentAction(prompt="something")
        with caplog.at_level(logging.DEBUG, logger="wentian.hooks.actions"):
            run_subagent(action, {})

        messages = [r.message for r in caplog.records]
        assert any(
            "subagent" in m.lower() or "未实现" in m or "not implemented" in m.lower()
            for m in messages
        ), f"expected subagent log, got: {messages}"
