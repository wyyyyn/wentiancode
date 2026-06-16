"""Tests for CLI assembly (T13).

Strategy:
- Use typer.testing.CliRunner (no real terminal needed).
- Point config to a tmp YAML via XDG_CONFIG_HOME env var.
- Point sessions to a tmp dir via XDG_DATA_HOME env var.
- Stub REPL.run so no interactive loop starts; record calls.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

# ── minimal valid config YAML content ─────────────────────────────────────────

_GOOD_CONFIG = {
    "default": "claude",
    "providers": {
        "claude": {
            "protocol": "anthropic",
            "model": "claude-opus-4-8",
            "api_key": "sk-ant-test",
        },
        "deepseek": {
            "protocol": "openai",
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com",
            "api_key": "sk-ds-test",
        },
    },
}


@pytest.fixture()
def tmp_env(tmp_path, monkeypatch):
    """Set up tmp XDG dirs and write a valid config; return (cfg_dir, sessions_dir)."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # Write good config
    wentian_cfg = cfg_dir / "wentian"
    wentian_cfg.mkdir()
    (wentian_cfg / "config.yaml").write_text(
        yaml.dump(_GOOD_CONFIG), encoding="utf-8"
    )

    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_dir))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_dir))

    return cfg_dir, data_dir


@pytest.fixture()
def runner():
    return CliRunner()


# ── import app lazily so monkeypatching env vars happens first ─────────────────

def _get_app():
    from wentian.cli import app
    return app


# ── T1: no args → default provider, new session, REPL.run called ──────────────

def test_default_provider_new_session(tmp_env, runner, monkeypatch):
    """No args: uses default provider (claude), creates a new session, calls REPL.run."""
    run_calls = []

    def fake_run(self):
        run_calls.append(self)

    monkeypatch.setattr("wentian.repl.REPL.run", fake_run)

    result = runner.invoke(_get_app(), [], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert len(run_calls) == 1
    repl = run_calls[0]
    assert repl._provider.name == "claude"
    assert repl._session is not None


# ── T2: --provider deepseek → correct provider wired ──────────────────────────

def test_provider_flag(tmp_env, runner, monkeypatch):
    """--provider deepseek: provider name is deepseek."""
    run_calls = []

    def fake_run(self):
        run_calls.append(self)

    monkeypatch.setattr("wentian.repl.REPL.run", fake_run)

    result = runner.invoke(_get_app(), ["--provider", "deepseek"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert len(run_calls) == 1
    assert run_calls[0]._provider.name == "deepseek"


# ── T3: --continue → load_latest session; fallback to new if none ─────────────

def test_continue_loads_latest_session(tmp_env, runner, monkeypatch):
    """--continue with an existing session loads it."""
    from wentian.session import SessionStore, default_sessions_dir

    # Pre-create a session so load_latest has something to return
    store = SessionStore(default_sessions_dir())
    existing = store.create(provider="claude")
    store.save(existing)

    run_calls = []

    def fake_run(self):
        run_calls.append(self)

    monkeypatch.setattr("wentian.repl.REPL.run", fake_run)

    result = runner.invoke(_get_app(), ["--continue"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert len(run_calls) == 1
    assert run_calls[0]._session.id == existing.id


def test_continue_no_history_falls_back_to_new(tmp_env, runner, monkeypatch):
    """--continue with no sessions: falls back to new session and prints a hint."""
    run_calls = []

    def fake_run(self):
        run_calls.append(self)

    monkeypatch.setattr("wentian.repl.REPL.run", fake_run)

    result = runner.invoke(_get_app(), ["--continue"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert len(run_calls) == 1
    # Hint text should mention something about no history / new session
    assert "new" in result.output.lower() or "session" in result.output.lower()


# ── T4: --resume <id> → specific session or error ─────────────────────────────

def test_resume_valid_id(tmp_env, runner, monkeypatch):
    """--resume <valid-id> loads that specific session."""
    from wentian.session import SessionStore, default_sessions_dir

    store = SessionStore(default_sessions_dir())
    sess = store.create(provider="claude")
    store.save(sess)

    run_calls = []

    def fake_run(self):
        run_calls.append(self)

    monkeypatch.setattr("wentian.repl.REPL.run", fake_run)

    result = runner.invoke(_get_app(), ["--resume", sess.id], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert len(run_calls) == 1
    assert run_calls[0]._session.id == sess.id


def test_resume_bad_id_exits_nonzero(tmp_env, runner, monkeypatch):
    """--resume <nonexistent-id>: exits with non-zero code, no traceback."""
    monkeypatch.setattr("wentian.repl.REPL.run", lambda self: None)

    result = runner.invoke(_get_app(), ["--resume", "bad-id-that-does-not-exist"])

    assert result.exit_code != 0
    # Should not show a Python traceback
    assert "Traceback" not in result.output


# ── T5: config missing / invalid → friendly error, non-zero exit ──────────────

def test_missing_config_exits_nonzero(tmp_path, runner, monkeypatch):
    """Config file absent: friendly error, non-zero exit, no traceback."""
    # Point XDG to a dir with NO config file
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty_cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    result = runner.invoke(_get_app(), [])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    # Should have some human-readable message
    assert len(result.output.strip()) > 0


def test_invalid_config_exits_nonzero(tmp_path, runner, monkeypatch):
    """Config file present but invalid YAML structure: friendly error, non-zero exit."""
    cfg_dir = tmp_path / "config" / "wentian"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.yaml").write_text("this: is: not: valid: config", encoding="utf-8")

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    result = runner.invoke(_get_app(), [])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert len(result.output.strip()) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# v0.2 · C7 · F13-F18（任务 T25）— build_app assembly wiring:
# banner, provider selector, PromptInput status wiring, interrupt listener.
# CliRunner / direct calls are non-TTY → all real UI components injected as fakes.
# ═══════════════════════════════════════════════════════════════════════════════


def _record_console():
    from rich.console import Console
    return Console(record=True, width=100)


# ── Banner (F13 / AC11) ────────────────────────────────────────────────────────

def test_banner_printed_new_session(tmp_env):
    """build_app prints the startup banner: version, provider, 新会话."""
    from wentian.cli import build_app

    console = _record_console()
    build_app(console=console)
    out = console.export_text()

    assert "0.5.0" in out
    assert "claude" in out
    assert "新会话" in out
    assert "已恢复" not in out


def test_banner_resumed_on_continue(tmp_env):
    """--continue loading an existing session shows 已恢复."""
    from wentian.cli import build_app
    from wentian.session import SessionStore, default_sessions_dir

    store = SessionStore(default_sessions_dir())
    existing = store.create(provider="claude")
    store.save(existing)

    console = _record_console()
    build_app(console=console, continue_=True)
    out = console.export_text()

    assert "已恢复" in out
    assert existing.id in out


def test_banner_resumed_on_resume_id(tmp_env):
    """--resume <id> loading an existing session shows 已恢复."""
    from wentian.cli import build_app
    from wentian.session import SessionStore, default_sessions_dir

    store = SessionStore(default_sessions_dir())
    existing = store.create(provider="claude")
    store.save(existing)

    console = _record_console()
    build_app(console=console, resume_id=existing.id)

    assert "已恢复" in console.export_text()


def test_banner_continue_fallback_shows_new_session(tmp_env):
    """--continue with no history: banner shows 新会话, not 已恢复."""
    from wentian.cli import build_app

    console = _record_console()
    build_app(console=console, continue_=True)
    out = console.export_text()

    assert "新会话" in out
    assert "已恢复" not in out


def test_show_banner_false_suppresses_banner(tmp_env):
    """show_banner=False: no banner content printed."""
    from wentian.cli import build_app

    console = _record_console()
    build_app(console=console, show_banner=False)
    out = console.export_text()

    assert "0.2.0" not in out
    assert "新会话" not in out


def test_banner_printed_via_typer_main(tmp_env, runner, monkeypatch):
    """Banner prints through the typer entry even on non-TTY (pipe) stdout."""
    monkeypatch.setattr("wentian.repl.REPL.run", lambda self: None)

    result = runner.invoke(_get_app(), [], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert "新会话" in result.output


# ── Provider selector (F14 / AC12) ─────────────────────────────────────────────

def test_selector_called_multi_provider_no_flag(tmp_env):
    """Multi-provider config + no -p → selector called once; its pick wins."""
    from wentian.cli import build_app

    calls = []

    def fake_selector(names, default):
        calls.append((names, default))
        return "deepseek"

    repl = build_app(
        console=_record_console(),
        show_banner=False,
        provider_selector=fake_selector,
    )

    assert calls == [(["claude", "deepseek"], "claude")]
    assert repl._provider.name == "deepseek"


def test_selector_skipped_with_provider_flag(tmp_env):
    """-p given → selector NOT called."""
    from wentian.cli import build_app

    calls = []

    def fake_selector(names, default):
        calls.append((names, default))
        return "deepseek"

    repl = build_app(
        console=_record_console(),
        show_banner=False,
        provider_name="claude",
        provider_selector=fake_selector,
    )

    assert calls == []
    assert repl._provider.name == "claude"


def test_selector_skipped_single_provider(tmp_path):
    """Single-provider config → selector NOT called even when provided."""
    from wentian.cli import build_app

    single_cfg = {
        "default": "claude",
        "providers": {
            "claude": {
                "protocol": "anthropic",
                "model": "claude-opus-4-8",
                "api_key": "sk-ant-test",
            },
        },
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.dump(single_cfg), encoding="utf-8")

    calls = []

    def fake_selector(names, default):
        calls.append((names, default))
        return "claude"

    repl = build_app(
        cfg_path,
        tmp_path / "sessions",
        console=_record_console(),
        show_banner=False,
        provider_selector=fake_selector,
    )

    assert calls == []
    assert repl._provider.name == "claude"


# ── PromptInput status_provider wiring (F15/F16) ───────────────────────────────

def test_promptinput_like_status_provider_wired(tmp_env):
    """input_fn with a status_provider attr gets repl.status_line bound to it."""
    from wentian.cli import build_app

    class FakePromptInput:
        def __init__(self):
            self.status_provider = None

        def __call__(self, prompt: str = "") -> str:
            return "/exit"

    fake = FakePromptInput()
    repl = build_app(console=_record_console(), show_banner=False, input_fn=fake)

    assert repl._input_fn is fake
    assert fake.status_provider == repl.status_line  # bound-method equality
    assert "claude" in fake.status_provider()


def test_plain_input_fn_passed_through(tmp_env):
    """A plain callable without status_provider is wired as-is, untouched."""
    from wentian.cli import build_app

    def fn(prompt: str = "") -> str:
        return "/exit"

    repl = build_app(console=_record_console(), show_banner=False, input_fn=fn)

    assert repl._input_fn is fn


# ── Interrupt listener (F18) ───────────────────────────────────────────────────

def test_interrupt_listener_param_passed_to_repl(tmp_env):
    """interrupt_listener param lands on the REPL."""
    from wentian.cli import build_app

    class FakeListener:
        def __enter__(self):
            return None

        def __exit__(self, *args):
            return False

    fake = FakeListener()
    repl = build_app(
        console=_record_console(), show_banner=False, interrupt_listener=fake
    )

    assert repl._interrupt_listener is fake


# ── Non-TTY default path (v0.1 equivalence) ────────────────────────────────────

def test_default_path_no_new_params(tmp_env):
    """Without the new params: builtins.input + NullListener (v0.1 behavior)."""
    from wentian.cli import build_app
    from wentian.ui.interrupt import NullListener

    repl = build_app(console=_record_console(), show_banner=False)

    assert repl._input_fn is input
    assert isinstance(repl._interrupt_listener, NullListener)


# ═══════════════════════════════════════════════════════════════════════════════
# v0.3 · C13（任务 T45）— cli 装配收口: six tools + executor + confirm + system.
# CliRunner / direct calls are non-TTY → default confirm denies side effects.
# ═══════════════════════════════════════════════════════════════════════════════


_SIX_TOOLS = [
    "read_file",
    "write_file",
    "edit_file",
    "run_command",
    "find_files",
    "search_text",
]


# ── Default assembly: six tools registered into REPL's registry ────────────────

def test_default_build_app_registers_six_tools(tmp_env):
    """v0.3 · C13（任务 T45）— default build_app wires a registry with the six
    standard tools (read/write/edit file, run_command, find/search)."""
    from wentian.cli import build_app

    repl = build_app(console=_record_console(), show_banner=False)

    assert repl._registry is not None
    assert repl._registry.names() == _SIX_TOOLS


def test_default_build_app_wires_executor(tmp_env):
    """v0.3 · C13（任务 T45）— default build_app also wires a ToolExecutor."""
    from wentian.cli import build_app
    from wentian.tools.executor import ToolExecutor

    repl = build_app(console=_record_console(), show_banner=False)

    assert isinstance(repl._executor, ToolExecutor)


# ── Injection passthrough ──────────────────────────────────────────────────────

def test_injected_registry_and_executor_passed_through(tmp_env):
    """v0.3 · C13（任务 T45）— injected tool_registry/tool_executor reach REPL.
    v0.5 · T67 — registry must support .names() (used by build_system_prompt path)."""
    from wentian.cli import build_app

    class _FakeRegistry:
        """Minimal duck-type: specs() for provider, names() for system prompt."""
        def specs(self):
            return []

        def names(self):
            return []

        def get(self, name):
            return None

    fake_registry = _FakeRegistry()
    fake_executor = object()

    repl = build_app(
        console=_record_console(),
        show_banner=False,
        tool_registry=fake_registry,
        tool_executor=fake_executor,
    )

    assert repl._registry is fake_registry
    assert repl._executor is fake_executor


# ── _make_confirm factory (module-level, testable) ─────────────────────────────

def test_make_confirm_non_interactive_always_false():
    """v0.3 · C13（任务 T45）— non-interactive confirm callable is always False."""
    from wentian.cli import _make_confirm

    confirm = _make_confirm(False)
    assert confirm("anything?") is False
    assert confirm("Run write_file?") is False


def test_make_confirm_interactive_yes(monkeypatch):
    """v0.3 · C13（任务 T45）— interactive confirm: 'y' → True."""
    from wentian.cli import _make_confirm

    monkeypatch.setattr("builtins.input", lambda _prompt="": "y")
    confirm = _make_confirm(True)
    assert confirm("Run write_file?") is True


def test_make_confirm_interactive_yes_word_case_insensitive(monkeypatch):
    """v0.3 · C13（任务 T45）— interactive confirm: 'YES' → True."""
    from wentian.cli import _make_confirm

    monkeypatch.setattr("builtins.input", lambda _prompt="": "YES")
    confirm = _make_confirm(True)
    assert confirm("Run write_file?") is True


def test_make_confirm_interactive_no(monkeypatch):
    """v0.3 · C13（任务 T45）— interactive confirm: 'n' → False."""
    from wentian.cli import _make_confirm

    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")
    confirm = _make_confirm(True)
    assert confirm("Run write_file?") is False


def test_make_confirm_interactive_empty(monkeypatch):
    """v0.3 · C13（任务 T45）— interactive confirm: empty input → False (default N)."""
    from wentian.cli import _make_confirm

    monkeypatch.setattr("builtins.input", lambda _prompt="": "")
    confirm = _make_confirm(True)
    assert confirm("Run write_file?") is False


# ── Default executor denies side-effect tools on non-TTY ───────────────────────

def test_default_executor_denies_side_effects_non_tty(tmp_env):
    """v0.3 · C13（任务 T45）— under CliRunner/non-TTY the default confirm denies
    write_file (a requires_confirmation tool) without running it."""
    from wentian.cli import build_app

    repl = build_app(console=_record_console(), show_banner=False)

    outcome = repl._executor.execute(
        "call-1", "write_file", {"path": "x.txt", "content": "hi"}
    )
    assert outcome.is_error is True
    assert outcome.denied is True


# ── System prompt: tools enabled → cwd + usage note ────────────────────────────

def test_default_system_prompt_mentions_cwd_and_tools(tmp_env):
    """v0.3 · C13（任务 T45）— with tools enabled the REPL system prompt is
    non-None and mentions the registered tools (note: v0.5 replaces the old
    _tools_system_prompt so cwd is no longer embedded directly; tool names are)."""
    from wentian.cli import build_app

    repl = build_app(console=_record_console(), show_banner=False)

    assert repl._system is not None
    # At least one of the six tool names must appear
    assert any(name in repl._system for name in _SIX_TOOLS)


# ═══════════════════════════════════════════════════════════════════════════════
# v0.4 · C20（任务 T57）— assembly close-out: the default build_app wiring drives
# a full multi-round agent loop end-to-end (tool round → tool round → final text).
# Provider is faked via wentian.cli.create_provider; registry/executor use the
# existing injection points; input_fn feeds one chat turn then /exit.
# ═══════════════════════════════════════════════════════════════════════════════


class _E2EFakeTool:
    """v0.4 · C20（任务 T57）— minimal tool double for AgentLoop classify."""

    requires_confirmation = True


class _E2EFakeRegistry:
    """v0.4 · C20（任务 T57）— minimal registry: specs() for the provider
    advertisement, get() for AgentLoop classification."""

    def __init__(self, specs):
        self._specs = specs
        self._tools = {s.name: _E2EFakeTool() for s in specs}

    def specs(self):
        return self._specs

    def get(self, name):
        return self._tools.get(name)

    def names(self):
        return [s.name for s in self._specs]


class _E2EFakeExecutor:
    """v0.4 · C20（任务 T57）— records execute() calls, returns success
    outcomes (ToolOutcome duck-type)."""

    class _Outcome:
        def __init__(self, call_id, name):
            self.call_id = call_id
            self.name = name
            self.content = f"ran {name}"
            self.is_error = False
            self.denied = False

    def __init__(self):
        self.calls = []

    def execute(self, call_id, name, arguments):
        self.calls.append((call_id, name, arguments))
        return self._Outcome(call_id, name)


def test_build_app_default_wiring_runs_multi_round_loop_e2e(tmp_env, monkeypatch):
    """v0.4 · C20（任务 T57）— end-to-end proof: default build_app wiring drives
    a three-round agent loop (tool_calls → tool_calls → final text) through
    REPL.run(): both tools executed, history fully paired, ⏺ markers on screen."""
    from conftest import ScriptedProvider
    from wentian.cli import build_app
    from wentian.providers.base import Done, TextDelta, ToolCallEvent, ToolSpec

    scripted = ScriptedProvider([
        [
            TextDelta("第一轮正文"),
            ToolCallEvent(id="c1", name="read", arguments={"path": "a"}),
            Done(),
        ],
        [
            TextDelta("第二轮正文"),
            ToolCallEvent(id="c2", name="read", arguments={"path": "b"}),
            Done(),
        ],
        [TextDelta("最终答复"), Done()],
    ])
    monkeypatch.setattr("wentian.cli.create_provider", lambda cfg: scripted)

    spec = ToolSpec(name="read", description="read a file", parameters={"type": "object"})
    registry = _E2EFakeRegistry([spec])
    executor = _E2EFakeExecutor()

    inputs = iter(["做点事", "/exit"])
    console = _record_console()

    repl = build_app(
        console=console,
        input_fn=lambda prompt="": next(inputs),
        tool_registry=registry,
        tool_executor=executor,
    )
    repl.run()

    # Multi-round: provider streamed three times, both tools executed in order.
    assert len(scripted.calls) == 3
    assert executor.calls == [
        ("c1", "read", {"path": "a"}),
        ("c2", "read", {"path": "b"}),
    ]
    # History fully paired: user → (assistant+tool_calls → tool) ×2 → assistant.
    session = repl._session
    assert [m["role"] for m in session.messages] == [
        "user", "assistant", "tool", "assistant", "tool", "assistant",
    ]
    assert session.messages[5] == {"role": "assistant", "content": "最终答复"}
    # Screen output carries the tool marker and all round texts.
    out = console.export_text()
    assert "⏺" in out
    for text in ("第一轮正文", "第二轮正文", "最终答复"):
        assert text in out


# ═══════════════════════════════════════════════════════════════════════════════
# v0.5 · C21（任务 T67）— 装配收口: build_app 用 build_system_prompt(PromptContext)
# 替换旧的 _tools_system_prompt；工具名出现在 system 中；七模块结构验证；版本 0.5.0。
# ═══════════════════════════════════════════════════════════════════════════════


def test_version_is_0_5_0():
    """v0.5 · T67 — __version__ must be 0.5.0."""
    import wentian
    assert wentian.__version__ == "0.5.0"


def test_build_app_system_uses_seven_module_structure(tmp_env):
    """v0.5 · T67 — default build_app system prompt is generated by
    build_system_prompt(PromptContext(...)): it must contain the canonical
    seven-module headings defined in system.py and list the registered tool
    names (proving the new path is taken, not the old _tools_system_prompt)."""
    from wentian.cli import build_app

    repl = build_app(console=_record_console(), show_banner=False)

    system = repl._system
    assert system is not None

    # Seven fixed module headings (exact strings from system.py)
    for heading in ("# 身份", "# 系统约束", "# 任务模式", "# 动作执行",
                    "# 工具使用", "# 语气风格", "# 文本输出"):
        assert heading in system, f"Expected module heading '{heading}' in system prompt"

    # All six default tool names must appear (from _render_tool_usage)
    for tool_name in _SIX_TOOLS:
        assert tool_name in system, f"Expected tool name '{tool_name}' in system prompt"


def test_build_app_system_no_registry_still_uses_seven_modules(tmp_env):
    """v0.5 · T67 — injecting empty registry (None/None injection path) still
    produces a seven-module system prompt with empty tool list note."""
    from wentian.cli import build_app
    from wentian.tools.registry import ToolRegistry
    from wentian.tools.executor import ToolExecutor

    empty_registry = ToolRegistry()  # no tools registered
    # Need a matching executor; use a minimal duck-type
    class _NullExecutor:
        def execute(self, call_id, name, arguments):
            raise NotImplementedError

    repl = build_app(
        console=_record_console(),
        show_banner=False,
        tool_registry=empty_registry,
        tool_executor=_NullExecutor(),
    )

    system = repl._system
    assert system is not None
    assert "# 身份" in system
    assert "# 工具使用" in system
    # Empty registry → the "暂无注册工具" placeholder
    assert "暂无注册工具" in system


def test_build_app_system_is_non_empty(tmp_env):
    """v0.5 · T67 — build_app 产出的 system 是结构化的非空提示。

    cwd / 环境信息在 v0.5 已迁到 ``<system-reminder>`` 消息通道（F39），故
    system 块本身不再嵌入 cwd——这与旧的 ``_tools_system_prompt`` 行为不同。
    本测试只做 sanity：system 非空、足够长（七模块拼装后远超阈值）。"""
    from wentian.cli import build_app

    repl = build_app(console=_record_console(), show_banner=False)

    assert repl._system is not None
    assert len(repl._system) > 200  # sanity: not an empty/trivial string
