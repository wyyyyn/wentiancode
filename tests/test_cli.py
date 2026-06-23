"""Tests for CLI assembly (T13).

Strategy:
- Use typer.testing.CliRunner (no real terminal needed).
- Point config to a tmp YAML via XDG_CONFIG_HOME env var.
- Point sessions to a tmp dir via XDG_DATA_HOME env var.
- Stub REPL.run so no interactive loop starts; record calls.
"""

from __future__ import annotations

from pathlib import Path

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
    (wentian_cfg / "config.yaml").write_text(yaml.dump(_GOOD_CONFIG), encoding="utf-8")

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

    result = runner.invoke(
        _get_app(), ["--provider", "deepseek"], catch_exceptions=False
    )

    assert result.exit_code == 0, result.output
    assert len(run_calls) == 1
    assert run_calls[0]._provider.name == "deepseek"


# ── T3: --continue → load_latest session; fallback to new if none ─────────────


def test_continue_loads_latest_session(tmp_env, runner, monkeypatch):
    """--continue with an existing session loads it."""
    from pathlib import Path as _Path
    from wentian.session import SessionStore, project_sessions_dir

    # Pre-create a session so load_latest has something to return
    store = SessionStore(project_sessions_dir(_Path.cwd()))
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
    from pathlib import Path as _Path
    from wentian.session import SessionStore, project_sessions_dir

    store = SessionStore(project_sessions_dir(_Path.cwd()))
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
    (cfg_dir / "config.yaml").write_text(
        "this: is: not: valid: config", encoding="utf-8"
    )

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


def _disable_skills(tmp_env):
    """v0.11 · C107a（任务 T134a）— 把 tmp_env 的默认 config 改成 skills 关闭。

    用于注入「缺标准工具」的假 registry 的旧测试：默认 skills.enabled=True 会发现
    打包内置 Skill 并对其 allowed_tools（run_command 等）做白名单 fail-fast，假
    registry 上必然抛 ValueError。这些测试只验工具注入透传，关掉 skills 隔离之。"""
    import yaml as _yaml

    cfg_dir, _ = tmp_env
    cfg = dict(_GOOD_CONFIG)
    cfg["skills"] = {"enabled": False}
    (cfg_dir / "wentian" / "config.yaml").write_text(_yaml.dump(cfg), encoding="utf-8")


# ── Banner (F13 / AC11) ────────────────────────────────────────────────────────


def test_banner_printed_new_session(tmp_env):
    """build_app prints the startup banner: version, provider, 新会话."""
    from wentian.cli import build_app

    console = _record_console()
    build_app(console=console)
    out = console.export_text()

    assert "0.14.0" in out
    assert "claude" in out
    assert "新会话" in out
    assert "已恢复" not in out


def test_banner_resumed_on_continue(tmp_env):
    """--continue loading an existing session shows 已恢复."""
    from pathlib import Path as _Path
    from wentian.cli import build_app
    from wentian.session import SessionStore, project_sessions_dir

    store = SessionStore(project_sessions_dir(_Path.cwd()))
    existing = store.create(provider="claude")
    store.save(existing)

    console = _record_console()
    build_app(console=console, continue_=True)
    out = console.export_text()

    assert "已恢复" in out
    assert existing.id in out


def test_banner_resumed_on_resume_id(tmp_env):
    """--resume <id> loading an existing session shows 已恢复."""
    from pathlib import Path as _Path
    from wentian.cli import build_app
    from wentian.session import SessionStore, project_sessions_dir

    store = SessionStore(project_sessions_dir(_Path.cwd()))
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
    # v0.6/F47：状态栏首段改为权限模式（占原 provider 名位置），不再显示 provider 名。
    # v0.10/C91/AC88：模式标记改括号式 [DEFAULT]（status_line 唯一行为变更）。
    assert "[DEFAULT]" in fake.status_provider()


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
    standard tools (read/write/edit file, run_command, find/search).

    v0.11 · C107a（任务 T134a）— 默认 skills.enabled=True 且打包内置 Skill 可发现
    时，``load_skill`` 系统级工具也会注册，故六标准工具后追加 ``load_skill``。"""
    from wentian.cli import build_app

    repl = build_app(console=_record_console(), show_banner=False)

    assert repl._registry is not None
    assert repl._registry.names() == [*_SIX_TOOLS, "load_skill", "Agent"]


def test_default_build_app_wires_executor(tmp_env):
    """v0.3 · C13（任务 T45）— default build_app also wires a ToolExecutor."""
    from wentian.cli import build_app
    from wentian.tools.executor import ToolExecutor

    repl = build_app(console=_record_console(), show_banner=False)

    assert isinstance(repl._executor, ToolExecutor)


# ── Injection passthrough ──────────────────────────────────────────────────────


def test_injected_registry_and_executor_passed_through(tmp_env):
    """v0.3 · C13（任务 T45）— injected tool_registry/tool_executor reach REPL.
    v0.5 · T67 — registry must support .names() (used by build_system_prompt path).

    v0.11 · C107a（任务 T134a）— 注入只含空 names() 的假 registry，关掉 skills
    避免打包内置 Skill 白名单在假 registry 上验证失败（本测试只验注入透传）。"""
    from wentian.cli import build_app

    _disable_skills(tmp_env)

    class _FakeRegistry:
        """Minimal duck-type: specs() for provider, names() for system prompt."""

        def specs(self):
            return []

        def names(self):
            return []

        def get(self, name):
            return None

        def register(self, tool):  # v0.13 · T145 — AgentTool always registered (N50)
            pass

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
    """v0.4 · C20（任务 T57）— minimal tool double for AgentLoop classify.

    v0.6 · T79 — build_app now wires a real permission gate; this read-only
    double declares READ_ONLY so the gate's DEFAULT-mode fallback ALLOWs it
    (no confirmation) and the multi-round loop runs as before."""

    requires_confirmation = False

    from wentian.permissions.decision import Category as _Category

    category = _Category.READ_ONLY
    friendly_name = "Read"
    path_args = ("path",)


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

    def register(self, tool):  # v0.13 · T145 — AgentTool always registered (N50)
        pass


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
    REPL.run(): both tools executed, history fully paired, ⏺ markers on screen.

    v0.9 · C58（任务 T106）— memory disabled here so the background extraction
    thread doesn't add a non-deterministic 4th provider.stream() call (the
    extraction provider is built via the same monkeypatched create_provider)."""
    from conftest import ScriptedProvider
    from wentian.cli import build_app
    from wentian.providers.base import Done, TextDelta, ToolCallEvent, ToolSpec

    cfg_dir, _ = tmp_env
    cfg_with_no_memory = dict(_GOOD_CONFIG)
    cfg_with_no_memory["memory"] = {"enabled": False}
    # v0.11 · C107a（任务 T134a）— 此测试注入只含 "read" 的假 registry，关掉 skills
    # 以免打包内置 Skill 的白名单（引用 run_command 等标准工具）在假 registry 上
    # 验证失败；本测试只验多轮循环，不涉 skills。
    cfg_with_no_memory["skills"] = {"enabled": False}
    (cfg_dir / "wentian" / "config.yaml").write_text(
        yaml.dump(cfg_with_no_memory), encoding="utf-8"
    )

    scripted = ScriptedProvider(
        [
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
        ]
    )
    monkeypatch.setattr("wentian.cli.create_provider", lambda cfg: scripted)

    spec = ToolSpec(
        name="read", description="read a file", parameters={"type": "object"}
    )
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
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
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
    for heading in (
        "# 身份",
        "# 系统约束",
        "# 任务模式",
        "# 动作执行",
        "# 工具使用",
        "# 语气风格",
        "# 文本输出",
    ):
        assert heading in system, (
            f"Expected module heading '{heading}' in system prompt"
        )

    # All six default tool names must appear (from _render_tool_usage)
    for tool_name in _SIX_TOOLS:
        assert tool_name in system, f"Expected tool name '{tool_name}' in system prompt"


def test_build_app_system_no_registry_still_uses_seven_modules(tmp_env):
    """v0.5 · T67 — injecting empty registry (None/None injection path) still
    produces a seven-module system prompt with empty tool list note.

    v0.11 · C107a（任务 T134a）— 空 registry 上发现内置 Skill 会触发白名单
    fail-fast（引用 run_command 等不存在的工具），故此测试关掉 skills 隔离。"""
    from wentian.cli import build_app
    from wentian.tools.registry import ToolRegistry

    _disable_skills(tmp_env)

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
    # v0.13 · T145 — Agent 工具恒注册（N50），空 registry 现含 Agent，
    # 故 "暂无注册工具" 占位符不再出现；改验 Agent 工具名在系统提示中。
    assert "Agent" in system


def test_build_app_system_is_non_empty(tmp_env):
    """v0.5 · T67 — build_app 产出的 system 是结构化的非空提示。

    cwd / 环境信息在 v0.5 已迁到 ``<system-reminder>`` 消息通道（F39），故
    system 块本身不再嵌入 cwd——这与旧的 ``_tools_system_prompt`` 行为不同。
    本测试只做 sanity：system 非空、足够长（七模块拼装后远超阈值）。"""
    from wentian.cli import build_app

    repl = build_app(console=_record_console(), show_banner=False)

    assert repl._system is not None
    assert len(repl._system) > 200  # sanity: not an empty/trivial string


# ═══════════════════════════════════════════════════════════════════════════════
# v0.6 · C39 · F44（任务 T79）— 装配与版本收口：
# build_app 装配 PermissionPipeline(load_settings(cwd)) + 权限门注入 AgentLoop；
# 初始 _mode == settings.default_mode；非 TTY ask 恒 Deny（N16/AC55）；版本 0.6.0；
# .gitignore 含 settings.local（AC57）。
# ═══════════════════════════════════════════════════════════════════════════════


def test_version_is_0_8_0():  # noqa: N802 — legacy name kept; asserts current
    """v0.14（2026-06-23）— __version__ must be the current 0.14.0."""
    import wentian

    assert wentian.__version__ == "0.14.0"


def test_build_app_wires_permission_pipeline(tmp_env):
    """v0.6 · T79 — default build_app assembles a PermissionPipeline rooted at
    cwd and injects it into the REPL (so _build_gate() yields a real gate)."""
    from wentian.cli import build_app
    from wentian.permissions.pipeline import PermissionPipeline

    repl = build_app(console=_record_console(), show_banner=False)

    assert isinstance(repl._pipeline, PermissionPipeline)
    assert repl._pipeline.project_root == Path.cwd()
    # A real pipeline + registry → _build_gate() must produce a non-None gate
    # (this is what AgentLoop receives as permission_gate in _chat_once).
    assert repl._build_gate() is not None


def test_build_app_initial_mode_matches_settings_default(tmp_env, monkeypatch):
    """v0.6 · T79 — initial REPL _mode == settings.default_mode (AC58)."""
    from wentian.cli import build_app
    from wentian.permissions.decision import Mode

    # default config → no settings files → Mode.DEFAULT
    repl = build_app(console=_record_console(), show_banner=False)
    assert repl._mode is Mode.DEFAULT


def test_build_app_initial_mode_honours_project_default_mode(tmp_path, monkeypatch):
    """v0.6 · T79 — a project-level settings.yaml with defaultMode=plan makes
    the REPL start in PLAN mode (AC58: defaultMode applied at startup)."""
    from wentian.cli import build_app
    from wentian.permissions.decision import Mode

    # minimal valid config so build_app can construct a provider
    cfg = {
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
    cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")

    # project root is cwd → write .wentian/settings.yaml under cwd
    work = tmp_path / "work"
    work.mkdir()
    wt = work / ".wentian"
    wt.mkdir()
    (wt / "settings.yaml").write_text(
        yaml.dump({"defaultMode": "plan"}), encoding="utf-8"
    )
    monkeypatch.chdir(work)

    repl = build_app(
        cfg_path,
        tmp_path / "sessions",
        console=_record_console(),
        show_banner=False,
    )
    assert repl._mode is Mode.PLAN


def test_non_tty_ask_denies_side_effect_via_gate(tmp_env, monkeypatch, tmp_path):
    """v0.6 · T79 — N16/AC55 rebuilt at the gate layer: in a non-TTY build_app,
    a side-effect tool whose pipeline verdict is Ask gets DENIED (the gate's ask
    callback resolves to Choice.DENY → a formed is_error refusal), and the
    filesystem is untouched.

    This replaces the retired executor-level confirm gate test."""
    import asyncio

    from wentian.cli import build_app
    from wentian.providers.base import ToolCallEvent

    work = tmp_path / "proj"
    work.mkdir()
    monkeypatch.chdir(work)

    repl = build_app(console=_record_console(), show_banner=False)

    gate = repl._build_gate()
    assert gate is not None

    target = work / "should_not_exist.txt"
    call = ToolCallEvent(
        id="w1",
        name="write_file",
        arguments={"path": "should_not_exist.txt", "content": "boom"},
    )
    outcome = asyncio.run(gate(call))

    # Gate returned a formed refusal (non-None) — never silently allowed.
    assert outcome is not None
    assert outcome.is_error is True
    assert outcome.denied is True
    # No side effect: file never created.
    assert not target.exists()


def test_non_tty_ask_denies_unknown_tool_via_gate(tmp_env, monkeypatch, tmp_path):
    """v0.6 · T79 — N16/AC55: an unregistered tool is treated as command_exec
    (strictest) and denied on non-TTY (ask → DENY)."""
    import asyncio

    from wentian.cli import build_app
    from wentian.providers.base import ToolCallEvent

    work = tmp_path / "proj2"
    work.mkdir()
    monkeypatch.chdir(work)

    repl = build_app(console=_record_console(), show_banner=False)
    gate = repl._build_gate()

    call = ToolCallEvent(id="x1", name="totally_unknown", arguments={})
    outcome = asyncio.run(gate(call))

    assert outcome is not None
    assert outcome.is_error is True
    assert outcome.denied is True


def test_gitignore_excludes_local_settings():
    """v0.6 · T79 / AC57 — .gitignore must exclude .wentian/settings.local.yaml
    (it may carry sensitive allow rules and must never be committed)."""
    repo_root = Path(__file__).resolve().parent.parent
    gitignore = (repo_root / ".gitignore").read_text(encoding="utf-8")
    assert ".wentian/settings.local.yaml" in gitignore


# ═══════════════════════════════════════════════════════════════════════════════
# v0.7 · C46 · F55/N23（任务 T88）— CLI 装配 MCPManager + 生命周期接线
# ═══════════════════════════════════════════════════════════════════════════════


def _make_config_with_mcp(tmp_path) -> Path:
    """写一份含 mcpServers 的 config YAML 到 tmp_path，返回路径。"""
    cfg = {
        "default": "claude",
        "providers": {
            "claude": {
                "protocol": "anthropic",
                "model": "claude-opus-4-8",
                "api_key": "sk-ant-test",
            },
        },
        "mcpServers": {
            "fake": {
                "command": "python",
                "args": [str(Path(__file__).parent / "_fake_mcp_server.py")],
            }
        },
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    return p


def test_build_app_no_mcp_servers_registry_has_six_tools(tmp_env):
    """v0.7 · T88 / N23 — 无 mcpServers 时 registry 有 6 内置工具。

    v0.11 · C107a（任务 T134a）— 默认 skills.enabled=True 且打包内置 Skill 可发现，
    额外注册 ``load_skill`` 系统级工具 ⇒ 共 7（6 标准 + load_skill）。"""
    from wentian.cli import build_app

    repl = build_app(console=_record_console(), show_banner=False)

    names = list(repl._registry.names())
    assert len(names) == 8
    assert "load_skill" in names
    assert "Agent" in names
    assert names[:6] == _SIX_TOOLS


def test_build_app_with_mcp_servers_calls_discover(tmp_env, tmp_path, monkeypatch):
    """v0.7 · T88 / C46 — 有 mcpServers 时 build_app 调 manager.discover_and_register。

    用 mock manager 验证装配逻辑（不拉真子进程，快）。
    """
    from unittest.mock import MagicMock, patch

    from wentian.cli import build_app
    from wentian.mcp.manager import DiscoveryReport

    cfg = _make_config_with_mcp(tmp_path)

    mock_manager = MagicMock()
    mock_manager.discover_and_register.return_value = DiscoveryReport(ok={"fake": 1})

    with patch("wentian.cli.MCPManager", return_value=mock_manager):
        build_app(cfg, console=_record_console(), show_banner=False)

    mock_manager.discover_and_register.assert_called_once()
    # 第一个位置参数是 mcp_servers dict，第二个是 registry
    call_args = mock_manager.discover_and_register.call_args
    servers_arg = call_args[0][0]
    assert "fake" in servers_arg


def test_build_app_with_mcp_passes_manager_to_repl(tmp_env, tmp_path, monkeypatch):
    """v0.7 · T88 / C46 — build_app 把 manager 传给 REPL（_mcp_manager 属性存在）。"""
    from unittest.mock import MagicMock, patch

    from wentian.cli import build_app
    from wentian.mcp.manager import DiscoveryReport

    cfg = _make_config_with_mcp(tmp_path)

    mock_manager = MagicMock()
    mock_manager.discover_and_register.return_value = DiscoveryReport(ok={"fake": 1})

    with patch("wentian.cli.MCPManager", return_value=mock_manager):
        repl = build_app(cfg, console=_record_console(), show_banner=False)

    assert repl._mcp_manager is mock_manager


def test_build_app_no_mcp_servers_manager_is_none(tmp_env):
    """v0.7 · T88 / N23 — 无 mcpServers 时 REPL._mcp_manager 为 None。"""
    from wentian.cli import build_app

    repl = build_app(console=_record_console(), show_banner=False)
    assert repl._mcp_manager is None


def test_repl_exit_calls_close_all(tmp_env):
    """v0.7 · T88 / F55 — REPL.run() 退出时调 manager.close_all()（正常 /exit 路径）。"""
    from unittest.mock import MagicMock

    from wentian.cli import build_app

    mock_manager = MagicMock()

    cfg = {
        "default": "claude",
        "providers": {
            "claude": {
                "protocol": "anthropic",
                "model": "claude-opus-4-8",
                "api_key": "sk-ant-test",
            },
        },
    }
    # 直接构建 repl，注入 mock manager
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        cfg_path = Path(td) / "config.yaml"
        cfg_path.write_text(yaml.dump(cfg))
        sessions_dir = Path(td) / "sessions"
        sessions_dir.mkdir()

        repl = build_app(
            cfg_path,
            sessions_dir,
            console=_record_console(),
            show_banner=False,
            input_fn=lambda _: "/exit",
        )
        repl._mcp_manager = mock_manager

    repl.run()
    mock_manager.close_all.assert_called_once()


def test_repl_exit_calls_close_all_on_eoferror(tmp_env):
    """v0.7 · T88 / F55 — REPL.run() EOFError 退出时也调 manager.close_all()。"""
    from unittest.mock import MagicMock
    from wentian.cli import build_app
    import tempfile

    mock_manager = MagicMock()
    cfg = {
        "default": "claude",
        "providers": {
            "claude": {
                "protocol": "anthropic",
                "model": "claude-opus-4-8",
                "api_key": "sk-ant-test",
            },
        },
    }
    with tempfile.TemporaryDirectory() as td:
        cfg_path = Path(td) / "config.yaml"
        cfg_path.write_text(yaml.dump(cfg))
        sessions_dir = Path(td) / "sessions"
        sessions_dir.mkdir()

        repl = build_app(
            cfg_path,
            sessions_dir,
            console=_record_console(),
            show_banner=False,
            input_fn=lambda _: (_ for _ in ()).throw(EOFError()),
        )
        repl._mcp_manager = mock_manager

    repl.run()
    mock_manager.close_all.assert_called_once()


def test_repl_exit_close_all_skipped_when_manager_none(tmp_env):
    """v0.7 · T88 / N23 — _mcp_manager=None 时 run() 不崩溃（跳过 close_all）。"""
    from wentian.cli import build_app
    import tempfile

    cfg = {
        "default": "claude",
        "providers": {
            "claude": {
                "protocol": "anthropic",
                "model": "claude-opus-4-8",
                "api_key": "sk-ant-test",
            },
        },
    }
    with tempfile.TemporaryDirectory() as td:
        cfg_path = Path(td) / "config.yaml"
        cfg_path.write_text(yaml.dump(cfg))
        sessions_dir = Path(td) / "sessions"
        sessions_dir.mkdir()

        repl = build_app(
            cfg_path,
            sessions_dir,
            console=_record_console(),
            show_banner=False,
            input_fn=lambda _: "/exit",
        )

    assert repl._mcp_manager is None
    repl.run()  # must not raise


# ── v0.8 · C52 · F61/F62（任务 T96）— compactor assembly ──────────────────────


def _build_app_with(cfg: dict):
    """Helper: build_app over a temp config dir; returns the REPL (no run)."""
    from wentian.cli import build_app
    import tempfile

    td = tempfile.mkdtemp()
    cfg_path = Path(td) / "config.yaml"
    cfg_path.write_text(yaml.dump(cfg))
    sessions_dir = Path(td) / "sessions"
    sessions_dir.mkdir()
    repl = build_app(
        cfg_path,
        sessions_dir,
        console=_record_console(),
        show_banner=False,
        input_fn=lambda _: "/exit",
    )
    return repl, sessions_dir


def test_build_app_injects_compactor():
    """RED1: build_app constructs a Compactor and injects it into the REPL."""
    from wentian.context.compactor import Compactor

    repl, _ = _build_app_with(
        {
            "default": "claude",
            "providers": {
                "claude": {
                    "protocol": "anthropic",
                    "model": "claude-opus-4-8",
                    "api_key": "sk-ant-test",
                },
            },
        }
    )
    assert isinstance(repl._compactor, Compactor)


def test_build_app_resolves_context_window_from_provider():
    """RED1: provider context_window wins over ContextConfig.default_window."""
    repl, _ = _build_app_with(
        {
            "default": "claude",
            "providers": {
                "claude": {
                    "protocol": "anthropic",
                    "model": "claude-opus-4-8",
                    "api_key": "sk-ant-test",
                    "context_window": 333_333,
                },
            },
        }
    )
    assert repl._compactor._context_window == 333_333


def test_build_app_falls_back_to_default_window():
    """RED1: no provider context_window → ContextConfig.default_window (200K)."""
    repl, _ = _build_app_with(
        {
            "default": "claude",
            "providers": {
                "claude": {
                    "protocol": "anthropic",
                    "model": "claude-opus-4-8",
                    "api_key": "sk-ant-test",
                },
            },
        }
    )
    assert repl._compactor._context_window == 200_000


def test_build_app_artifacts_dir_under_session():
    """RED1: artifacts dir = <sessions_dir>/<session_id>.artifacts/."""
    repl, sessions_dir = _build_app_with(
        {
            "default": "claude",
            "providers": {
                "claude": {
                    "protocol": "anthropic",
                    "model": "claude-opus-4-8",
                    "api_key": "sk-ant-test",
                },
            },
        }
    )
    expected = Path(sessions_dir) / f"{repl._session.id}.artifacts"
    assert Path(repl._compactor._artifacts_dir) == expected


# ── v0.9 · C59 · F63/F65/F68/F69/N29（任务 T106）— assembly wiring ─────────────


def _mem_cfg_dir(tmp_path, monkeypatch):
    """Point XDG config/data at tmp and write a good config; return paths."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    wentian_cfg = cfg_dir / "wentian"
    wentian_cfg.mkdir()
    (wentian_cfg / "config.yaml").write_text(yaml.dump(_GOOD_CONFIG), encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_dir))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_dir))
    return cfg_dir, data_dir


def test_build_app_injects_project_instructions(tmp_path, monkeypatch):
    """build_app reads <cwd>/WENTIAN.md into ctx.project_instructions → system."""
    from wentian.cli import build_app

    _mem_cfg_dir(tmp_path, monkeypatch)
    work = tmp_path / "work"
    work.mkdir()
    (work / "WENTIAN.md").write_text("务必先跑测试。", encoding="utf-8")
    monkeypatch.chdir(work)

    repl = build_app(console=_record_console(), show_banner=False)
    assert "# 项目/自定义指令" in repl._system
    assert "务必先跑测试。" in repl._system


def test_build_app_injects_memory_index(tmp_path, monkeypatch):
    """build_app reads user+project INDEX.md into ctx.memory → system."""
    from wentian.cli import build_app

    cfg_dir, _ = _mem_cfg_dir(tmp_path, monkeypatch)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    # user-scope INDEX under XDG config wentian/memory/
    user_mem = cfg_dir / "wentian" / "memory"
    user_mem.mkdir(parents=True)
    (user_mem / "INDEX.md").write_text("- 用户偏好: 喜欢中文\n", encoding="utf-8")
    # project-scope INDEX under <cwd>/.wentian/memory/
    proj_mem = work / ".wentian" / "memory"
    proj_mem.mkdir(parents=True)
    (proj_mem / "INDEX.md").write_text("- 项目知识: 用 uv\n", encoding="utf-8")

    repl = build_app(console=_record_console(), show_banner=False)
    assert "# 长期记忆" in repl._system
    assert "喜欢中文" in repl._system
    assert "用 uv" in repl._system


def test_build_app_no_instructions_no_memory_v08_regression(tmp_path, monkeypatch):
    """No WENTIAN.md / no memory → two slots absent, no residue (v0.8 system)."""
    from wentian.cli import build_app

    _mem_cfg_dir(tmp_path, monkeypatch)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    repl = build_app(console=_record_console(), show_banner=False)
    assert "# 项目/自定义指令" not in repl._system
    assert "# 长期记忆" not in repl._system
    assert "\n\n\n" not in repl._system


def test_build_app_uses_project_partition_dir(tmp_path, monkeypatch):
    """Default sessions dir is project_sessions_dir(cwd), not the flat legacy dir."""
    from wentian.cli import build_app
    from wentian.session import project_sessions_dir

    _mem_cfg_dir(tmp_path, monkeypatch)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    repl = build_app(console=_record_console(), show_banner=False)
    expected = project_sessions_dir(work)
    assert Path(repl._store._dir) == expected


def test_build_app_injected_sessions_dir_wins(tmp_path, monkeypatch):
    """Injected sessions_dir still takes precedence over the partition default."""
    from wentian.cli import build_app

    _mem_cfg_dir(tmp_path, monkeypatch)
    custom = tmp_path / "custom_sessions"
    custom.mkdir()
    repl = build_app(None, custom, console=_record_console(), show_banner=False)
    assert Path(repl._store._dir) == custom


def test_build_app_prunes_expired_on_startup(tmp_path, monkeypatch):
    """build_app calls prune_expired on the current partition at startup."""
    import wentian.cli as cli_mod
    from wentian.cli import build_app

    _mem_cfg_dir(tmp_path, monkeypatch)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    calls = []
    real = cli_mod.prune_expired

    def _spy(sessions_dir, retention_days, **kw):
        calls.append((Path(sessions_dir), retention_days))
        return real(sessions_dir, retention_days, **kw)

    monkeypatch.setattr(cli_mod, "prune_expired", _spy)
    build_app(console=_record_console(), show_banner=False)
    assert len(calls) == 1
    assert calls[0][1] == 30  # default retention_days


def test_build_app_resume_expired_session_still_loads(tmp_path, monkeypatch):
    """#11: resuming a session whose mtime exceeds retention_days must still load
    (the target id is exempt from / pruned after startup pruning) — not deleted
    out from under the resume and then FileNotFoundError'd."""
    import os
    import time

    from wentian.cli import build_app
    from wentian.session import SessionStore, project_sessions_dir

    _mem_cfg_dir(tmp_path, monkeypatch)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    store = SessionStore(project_sessions_dir(work))
    sess = store.create(provider="claude")
    sess.messages.append({"role": "user", "content": "旧问"})
    store.save(sess)
    # Backdate well past the 30-day default retention so prune would target it.
    old = time.time() - 40 * 86400
    os.utime(store._path(sess.id), (old, old))

    # Must not raise FileNotFoundError; the resumed session is preserved + loaded.
    repl = build_app(console=_record_console(), show_banner=False, resume_id=sess.id)
    assert repl._session.id == sess.id
    assert any(m.get("content") == "旧问" for m in repl._session.messages)
    # The session file still exists on disk (resume exempted it from pruning).
    assert store._path(sess.id).exists()


def test_build_app_continue_expired_session_still_loads(tmp_path, monkeypatch):
    """#11: --continue onto an expired latest session also survives pruning."""
    import os
    import time

    from wentian.cli import build_app
    from wentian.session import SessionStore, project_sessions_dir

    _mem_cfg_dir(tmp_path, monkeypatch)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    store = SessionStore(project_sessions_dir(work))
    sess = store.create(provider="claude")
    sess.messages.append({"role": "user", "content": "续上"})
    store.save(sess)
    old = time.time() - 40 * 86400
    os.utime(store._path(sess.id), (old, old))

    repl = build_app(console=_record_console(), show_banner=False, continue_=True)
    assert repl._session.id == sess.id
    assert store._path(sess.id).exists()


def test_build_app_constructs_memory_runner(tmp_path, monkeypatch):
    """memory.enabled (default True) → a MemoryRunner is injected into the REPL."""
    from wentian.cli import build_app
    from wentian.memory.runner import MemoryRunner

    _mem_cfg_dir(tmp_path, monkeypatch)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    repl = build_app(console=_record_console(), show_banner=False)
    assert isinstance(repl._memory_runner, MemoryRunner)


def test_build_app_memory_disabled_runner_none(tmp_path, monkeypatch):
    """memory.enabled: false → runner is None (no extraction)."""
    from wentian.cli import build_app

    cfg = dict(_GOOD_CONFIG)
    cfg["memory"] = {"enabled": False}
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    wentian_cfg = cfg_dir / "wentian"
    wentian_cfg.mkdir()
    (wentian_cfg / "config.yaml").write_text(yaml.dump(cfg), encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_dir))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_dir))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    repl = build_app(console=_record_console(), show_banner=False)
    assert repl._memory_runner is None


def test_build_app_memory_disabled_skips_injection(tmp_path, monkeypatch):
    """AC82 (Major #2): memory.enabled:false must disable BOTH extraction AND
    startup index injection. Even with INDEX.md on disk, the system prompt must
    not contain the 长期记忆 module or any index content."""
    from wentian.cli import build_app

    cfg = dict(_GOOD_CONFIG)
    cfg["memory"] = {"enabled": False}
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    wentian_cfg = cfg_dir / "wentian"
    wentian_cfg.mkdir()
    (wentian_cfg / "config.yaml").write_text(yaml.dump(cfg), encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_dir))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_dir))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    # Seed BOTH indexes on disk — they must NOT be injected when disabled.
    user_mem = cfg_dir / "wentian" / "memory"
    user_mem.mkdir(parents=True)
    (user_mem / "INDEX.md").write_text("- 用户偏好: 绝密索引内容\n", encoding="utf-8")
    proj_mem = work / ".wentian" / "memory"
    proj_mem.mkdir(parents=True)
    (proj_mem / "INDEX.md").write_text("- 项目知识: 另一条索引\n", encoding="utf-8")

    repl = build_app(console=_record_console(), show_banner=False)
    assert "# 长期记忆" not in repl._system
    assert "绝密索引内容" not in repl._system
    assert "另一条索引" not in repl._system


def test_build_app_resume_reminder_from_gap(tmp_path, monkeypatch):
    """Resuming a session whose updated_at is old → a resume_reminder is set."""
    import os
    import time
    from wentian.cli import build_app
    from wentian.session import SessionStore, project_sessions_dir

    _mem_cfg_dir(tmp_path, monkeypatch)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    store = SessionStore(project_sessions_dir(work))
    sess = store.create(provider="claude")
    sess.messages.append({"role": "user", "content": "旧问"})
    store.save(sess)
    # Backdate the file mtime ~10 hours so the gap exceeds the 4h default.
    old = time.time() - 10 * 3600
    os.utime(store._path(sess.id), (old, old))

    repl = build_app(console=_record_console(), show_banner=False, resume_id=sess.id)
    assert repl._resume_reminder is not None
    assert "距上次对话" in repl._resume_reminder


def test_build_app_resume_no_reminder_when_recent(tmp_path, monkeypatch):
    """Resuming a fresh session (recent updated_at) → no reminder."""
    from wentian.cli import build_app
    from wentian.session import SessionStore, project_sessions_dir

    _mem_cfg_dir(tmp_path, monkeypatch)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    store = SessionStore(project_sessions_dir(work))
    sess = store.create(provider="claude")
    sess.messages.append({"role": "user", "content": "新问"})
    store.save(sess)

    repl = build_app(console=_record_console(), show_banner=False, resume_id=sess.id)
    assert repl._resume_reminder is None


def test_build_app_resume_truncates_unpaired(tmp_path, monkeypatch):
    """Resume runs truncate_unpaired on the loaded messages (dangling tool_call)."""
    from wentian.cli import build_app
    from wentian.session import SessionStore, project_sessions_dir

    _mem_cfg_dir(tmp_path, monkeypatch)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    store = SessionStore(project_sessions_dir(work))
    sess = store.create(provider="claude")
    sess.messages.append({"role": "user", "content": "q"})
    # trailing assistant tool_call with no matching tool result → unpaired
    sess.messages.append(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "x1", "name": "read", "arguments": {}}],
        }
    )
    store.save(sess)

    repl = build_app(console=_record_console(), show_banner=False, resume_id=sess.id)
    # The dangling tool_call turn is truncated; only the user message remains.
    assert [m["role"] for m in repl._session.messages] == ["user"]


# ── v0.9 review fix — AC77: resume integration (overflow → compact; converts) ──


def test_build_app_resume_overflow_triggers_compaction(tmp_path, monkeypatch):
    """AC77(a): a recovered history whose estimate exceeds
    window - reserved_output - auto_margin → build_app calls Compactor.compact
    once on the resume path (integration, not just the pure function)."""
    import wentian.context.compactor as comp_mod
    from wentian.cli import build_app
    from wentian.session import SessionStore, project_sessions_dir

    # Tiny window so even a modest history overflows the budget.
    cfg = dict(_GOOD_CONFIG)
    cfg["providers"] = dict(cfg["providers"])
    cfg["providers"]["claude"] = dict(cfg["providers"]["claude"])
    cfg["providers"]["claude"]["context_window"] = 200
    cfg["context"] = {"reserved_output": 50, "auto_margin": 10}
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    wentian_cfg = cfg_dir / "wentian"
    wentian_cfg.mkdir()
    (wentian_cfg / "config.yaml").write_text(yaml.dump(cfg), encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_dir))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_dir))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    store = SessionStore(project_sessions_dir(work))
    sess = store.create(provider="claude")
    # Large history → estimate well over the 200-50-10 = 140 token budget.
    for i in range(20):
        sess.messages.append({"role": "user", "content": "长内容" * 50})
        sess.messages.append({"role": "assistant", "content": "回答内容" * 50})
    store.save(sess)

    calls = []
    real_compact = comp_mod.Compactor.compact

    def _spy(self, messages, last_usage, *, manual=False):
        calls.append(manual)
        return real_compact(self, messages, last_usage, manual=manual)

    monkeypatch.setattr(comp_mod.Compactor, "compact", _spy)
    build_app(console=_record_console(), show_banner=False, resume_id=sess.id)
    assert calls, "expected Compactor.compact on the overflow resume path"
    assert calls[0] is False  # automatic (manual=False) pre-compaction


def test_build_app_resume_truncated_history_converts_for_both_providers(
    tmp_path, monkeypatch
):
    """AC77(b): the resume-hygiene history (after truncate_unpaired) converts
    cleanly for BOTH provider message conversions (anthropic + openai), no
    exception, well-formed output."""
    from wentian.cli import build_app
    from wentian.providers.anthropic import AnthropicProvider
    from wentian.providers.openai_compat import OpenAICompatProvider
    from wentian.session import SessionStore, project_sessions_dir

    _mem_cfg_dir(tmp_path, monkeypatch)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    store = SessionStore(project_sessions_dir(work))
    sess = store.create(provider="claude")
    sess.messages.extend(
        [
            {"role": "user", "content": "读文件"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "name": "read", "arguments": {}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "结果"},
            {"role": "assistant", "content": "完成"},
            # trailing unpaired turn that truncate_unpaired must drop
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c2", "name": "read", "arguments": {}}],
            },
        ]
    )
    store.save(sess)

    repl = build_app(console=_record_console(), show_banner=False, resume_id=sess.id)
    msgs = repl._session.messages
    # the trailing unpaired turn was truncated → tail ends on a paired round
    assert msgs[-1].get("role") == "assistant" and "tool_calls" not in msgs[-1]
    # both conversions must succeed without raising
    a = AnthropicProvider._convert_messages(msgs)
    assert isinstance(a, list) and a
    from wentian.config import ProviderConfig

    oai_provider = OpenAICompatProvider(
        ProviderConfig(
            name="deepseek",
            protocol="openai",
            model="deepseek-chat",
            api_key="sk-x",
            base_url="https://api.deepseek.com",
        )
    )
    oai = oai_provider._build_messages(msgs, system=None)
    assert isinstance(oai, list) and oai


def test_version_is_0_9_0():
    """v0.14（2026-06-23）— Version bumped to 0.14.0."""
    import wentian

    assert wentian.__version__ == "0.14.0"


# ═══════════════════════════════════════════════════════════════════════════════
# v0.10 · C92 · F70/N37/N38（任务 T115）— cli.build_app 装配命令注册中心 +
# CommandCompleter 注入 PromptInput + 版本 0.10.0
# ═══════════════════════════════════════════════════════════════════════════════


def test_version_is_0_10_0():
    """v0.14（2026-06-23）— __version__ must be 0.14.0."""
    import wentian

    assert wentian.__version__ == "0.14.0"


def test_build_app_repl_has_13_visible_commands(tmp_env):
    """v0.10 · C92 · F70/N37（任务 T115）— build_app 注入命令注册中心后，
    REPL._commands.visible() 应非空，包含全部内置可见命令。

    v0.11 · C107b（任务 T134b）— skills 启用时会额外注册 /skills + skill→PROMPT
    斜杠命令，命令数 > 13；本测试只验「v0.10 基线命令 + v0.13 /agents」，故关掉
    skills 隔离（skills.enabled=false ⇒ 仅内置 13 条命令）。

    v0.13 · C116（任务 T144）— /agents 为第 13 条内置命令，基线从 12→13。"""
    from wentian.cli import build_app

    _disable_skills(tmp_env)
    repl = build_app(console=_record_console(), show_banner=False)

    assert repl._commands is not None
    visible = repl._commands.visible()
    assert len(visible) == 13


def test_build_app_promptinput_has_command_completer(tmp_env, tmp_path):
    """v0.10 · C92 · F70/N38（任务 T115）— history_path 注入时，PromptInput 的
    PromptSession 中注入了 CommandCompleter（不再是 None 补全）。"""
    from wentian.cli import build_app
    from wentian.ui.completion import CommandCompleter

    hist = tmp_path / "hist.txt"
    repl = build_app(
        console=_record_console(),
        show_banner=False,
        history_path=hist,
    )
    # input_fn 是 PromptInput 实例
    prompt_input = repl._input_fn
    assert hasattr(prompt_input, "_session"), "expected a PromptInput, not plain input"
    session_completer = prompt_input._session.completer
    assert isinstance(session_completer, CommandCompleter), (
        f"expected CommandCompleter, got {type(session_completer)}"
    )


def test_build_app_repl_has_memory_store_injected(tmp_env):
    """v0.10 · C92 · F70（任务 T115）— REPL._memory_store 已注入（非 None）。"""
    from wentian.cli import build_app
    from wentian.memory.store import MemoryStore

    repl = build_app(console=_record_console(), show_banner=False)

    assert isinstance(repl._memory_store, MemoryStore)


def test_build_app_startup_panic_propagates(tmp_env, monkeypatch):
    """v0.10 · C92 · F70/N37（任务 T115）— startup panic：build_builtin_registry
    抛 ValueError 时 build_app 不吞异常，直接向上传播（N37 启动恐慌）。"""
    import wentian.cli as cli_mod

    def _bad_registry():
        raise ValueError("registry init failed")

    monkeypatch.setattr(cli_mod, "build_builtin_registry", _bad_registry)

    with pytest.raises(ValueError, match="registry init failed"):
        from wentian.cli import build_app

        build_app(console=_record_console(), show_banner=False)


# ═══════════════════════════════════════════════════════════════════════════════
# v0.11 · C107a · F73/F87（任务 T134a）— Skill 系统装配（core assembly）
#
# build_app gated on config.skills.enabled：发现三层 Skill → 注册 load_skill →
# 构造 SkillActivator 注入 REPL → menu 渲进系统提示。白名单 fail-fast、版本号、
# 内置三 Skill 可发现、enabled=false 回归 v0.10。
# ═══════════════════════════════════════════════════════════════════════════════


def _write_project_skill(work: Path, name: str, *, allowed_tools=None) -> None:
    """Write a single-file skill under ``<work>/.wentian/skills/<name>.md``."""
    skills_dir = work / ".wentian" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"name: {name}", f"description: 测试技能 {name}", "mode: shared"]
    if allowed_tools is not None:
        inner = ", ".join(allowed_tools)
        lines.append(f"allowed_tools: [{inner}]")
    lines.append("---")
    lines.append(f"# Skill {name}\n这是一个测试 SOP，参数 $ARGUMENTS。")
    (skills_dir / f"{name}.md").write_text("\n".join(lines), encoding="utf-8")


def test_build_app_skill_assembly_wires_activator_and_menu(
    tmp_env, monkeypatch, tmp_path
):
    """build_app 装配：有 project skill 时 REPL._activator 非 None、registry 含
    load_skill、PromptContext menu 含该 skill、系统提示含「可用 Skill」+ 名字。"""
    from wentian.cli import build_app
    from wentian.skill_activator import SkillActivator

    work = tmp_path / "skillwork"
    work.mkdir()
    _write_project_skill(work, "deploy", allowed_tools=["read_file"])
    monkeypatch.chdir(work)

    repl = build_app(console=_record_console(), show_banner=False)

    # activator 注入。
    assert isinstance(repl._activator, SkillActivator)

    # registry 含 load_skill。
    assert "load_skill" in repl._registry.names()

    # 系统提示含「可用 Skill」+ skill 名。
    assert repl._system is not None
    assert "可用 Skill" in repl._system
    assert "deploy" in repl._system


def test_build_app_skill_whitelist_fail_fast_unknown_tool(
    tmp_env, monkeypatch, tmp_path
):
    """白名单 fail-fast：skill 引用不存在的工具 → build_app 抛 ValueError，
    错误串带 skill 名 + 工具名。"""
    from wentian.cli import build_app

    work = tmp_path / "badskill"
    work.mkdir()
    _write_project_skill(work, "broken", allowed_tools=["no_such_tool"])
    monkeypatch.chdir(work)

    with pytest.raises(ValueError, match="broken") as exc:
        build_app(console=_record_console(), show_banner=False)
    assert "no_such_tool" in str(exc.value)


def test_build_app_skill_whitelist_existing_tool_builds_fine(
    tmp_env, monkeypatch, tmp_path
):
    """白名单引用存在的工具（read_file）→ 正常装配，不抛。"""
    from wentian.cli import build_app

    work = tmp_path / "goodskill"
    work.mkdir()
    _write_project_skill(work, "ok", allowed_tools=["read_file"])
    monkeypatch.chdir(work)

    repl = build_app(console=_record_console(), show_banner=False)
    assert repl._activator is not None


def test_build_app_no_skills_no_menu_activator_none(tmp_env, monkeypatch, tmp_path):
    """无 user/project skill 且关掉内置发现时 → activator None / 无 menu，回归 v0.10。

    通过 builtin_dir 注入空目录验证「discovery yields empty → activator None」。
    """
    from wentian.cli import build_app

    work = tmp_path / "emptywork"
    work.mkdir()
    monkeypatch.chdir(work)

    # builtin 层指向空目录 → 三层皆空 → 无 skill。
    empty_builtin = tmp_path / "empty_builtin"
    empty_builtin.mkdir()
    import wentian.cli as cli_mod

    _orig = cli_mod.discover_skills

    def _discover_empty(project_dir, user_dir, **kwargs):
        return _orig(project_dir, user_dir, builtin_dir=empty_builtin)

    monkeypatch.setattr(cli_mod, "discover_skills", _discover_empty)

    repl = build_app(console=_record_console(), show_banner=False)
    assert repl._activator is None
    assert repl._system is not None
    assert "可用 Skill" not in repl._system


def test_build_app_skills_disabled_regresses_to_v010(tmp_env, monkeypatch, tmp_path):
    """config.skills.enabled=false → activator None、无 menu、load_skill 不注册。"""
    import yaml

    from wentian.cli import build_app

    cfg = dict(_GOOD_CONFIG)
    cfg["skills"] = {"enabled": False}
    cfg_path = tmp_path / "noskill.yaml"
    cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")

    work = tmp_path / "disabledwork"
    work.mkdir()
    _write_project_skill(work, "deploy", allowed_tools=["read_file"])
    monkeypatch.chdir(work)

    repl = build_app(
        cfg_path, tmp_path / "sessions", console=_record_console(), show_banner=False
    )
    assert repl._activator is None
    assert "load_skill" not in repl._registry.names()
    assert repl._system is not None
    assert "可用 Skill" not in repl._system


def test_build_app_builtin_skills_discoverable(tmp_env, monkeypatch, tmp_path):
    """内置三 Skill（commit/review/test）随包发现：无 user/project skill 时，
    discover_skills（如 build_app 调用）能找到三者；commit 正文含 Co-Authored-By 禁令。"""
    from wentian.cli import _project_skills_dir, _user_skills_dir
    from wentian.skills.loader import discover_skills

    work = tmp_path / "builtinwork"
    work.mkdir()
    monkeypatch.chdir(work)

    registry = discover_skills(_project_skills_dir(work), _user_skills_dir())
    names = {s.name for s in registry.list()}
    assert {"commit", "review", "test"} <= names

    commit = registry.get("commit")
    assert commit is not None
    assert "Co-Authored-By" in commit.body


def test_version_is_0_14_0():
    """版本号已升 0.14.0（2026-06-23：v0.14 显示层；接 0.13.0 后续号）。"""
    import wentian

    assert wentian.__version__ == "0.14.0"


def test_build_app_skills_default_enabled_uses_builtins(tmp_env, monkeypatch, tmp_path):
    """默认 skills.enabled=True 且无 user/project skill → 内置三 Skill 进 menu，
    activator 非 None（builtin 层自动并入）。"""
    from wentian.cli import build_app

    work = tmp_path / "defaultwork"
    work.mkdir()
    monkeypatch.chdir(work)

    repl = build_app(console=_record_console(), show_banner=False)
    assert repl._activator is not None
    assert "可用 Skill" in repl._system
    assert "commit" in repl._system


# ═══════════════════════════════════════════════════════════════════════════════
# v0.11 · C107b · F73（任务 T134b）— Skill 斜杠命令面：
# 每个发现的 Skill 注册成 PROMPT 斜杠命令（冲突策略：PROMPT 替换 / 控制命令跳过+警告）
# + /skills 列表 + /skills reload 重载。skills.enabled=false ⇒ 一概不注册（回归 v0.10）。
# ═══════════════════════════════════════════════════════════════════════════════


def _empty_builtin_discovery(monkeypatch, tmp_path):
    """把 build_app 用的 discover_skills 重定向到空 builtin 层——隔离内置三 Skill，
    令测试里只有 user/project 层写入的 Skill 被发现。返回空 builtin 目录路径。"""
    import wentian.cli as cli_mod

    empty_builtin = tmp_path / "empty_builtin_layer"
    empty_builtin.mkdir(exist_ok=True)
    _orig = cli_mod.discover_skills

    def _discover(project_dir, user_dir, **kwargs):
        return _orig(project_dir, user_dir, builtin_dir=empty_builtin)

    monkeypatch.setattr(cli_mod, "discover_skills", _discover)
    return empty_builtin


def test_skill_registers_visible_prompt_command(tmp_env, monkeypatch, tmp_path):
    """发现的 project skill `deploy` → 命令注册中心有可见 /deploy（PROMPT 类）。"""
    from wentian.cli import build_app
    from wentian.commands.spec import CommandType

    _empty_builtin_discovery(monkeypatch, tmp_path)
    work = tmp_path / "deploywork"
    work.mkdir()
    _write_project_skill(work, "deploy", allowed_tools=["read_file"])
    monkeypatch.chdir(work)

    repl = build_app(console=_record_console(), show_banner=False)

    spec = repl._commands.lookup("deploy")
    assert spec is not None
    assert spec.type == CommandType.PROMPT
    assert spec in repl._commands.visible()


def test_skill_command_handler_shared_calls_send_user_message(
    tmp_env, monkeypatch, tmp_path
):
    """/deploy x（SHARED skill）handler：激活 skill + 调 send_user_message。"""
    from wentian.cli import build_app

    _empty_builtin_discovery(monkeypatch, tmp_path)
    work = tmp_path / "deploywork2"
    work.mkdir()
    _write_project_skill(work, "deploy", allowed_tools=["read_file"])
    monkeypatch.chdir(work)

    repl = build_app(console=_record_console(), show_banner=False)

    sent: list[str] = []
    monkeypatch.setattr(repl, "send_user_message", lambda text: sent.append(text))

    spec = repl._commands.lookup("deploy")
    spec.handler(repl, "arg1 arg2")

    # SHARED skill 已被激活（进激活集）。
    assert "deploy" in dict(repl._activator.active_bodies())
    # 触发了一轮 AI（send_user_message 被调，正文即 args）。
    assert sent == ["arg1 arg2"]


def test_skill_command_handler_shared_empty_args_default_message(
    tmp_env, monkeypatch, tmp_path
):
    """裸 /deploy（无 args，SHARED）→ send_user_message 收到默认触发串。"""
    from wentian.cli import build_app

    _empty_builtin_discovery(monkeypatch, tmp_path)
    work = tmp_path / "deploywork3"
    work.mkdir()
    _write_project_skill(work, "deploy", allowed_tools=["read_file"])
    monkeypatch.chdir(work)

    repl = build_app(console=_record_console(), show_banner=False)

    sent: list[str] = []
    monkeypatch.setattr(repl, "send_user_message", lambda text: sent.append(text))

    spec = repl._commands.lookup("deploy")
    spec.handler(repl, "")

    assert len(sent) == 1
    assert "deploy" in sent[0]


def test_skill_command_isolated_prints_reflux(tmp_env, monkeypatch, tmp_path):
    """ISOLATED skill → handler 走 ctx.print（回流摘要），不调 send_user_message。"""
    from wentian.cli import build_app

    _empty_builtin_discovery(monkeypatch, tmp_path)
    work = tmp_path / "isowork"
    work.mkdir()
    skills_dir = work / ".wentian" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "audit.md").write_text(
        "---\nname: audit\ndescription: 隔离审计\nmode: isolated\n---\n# audit\n做事 $ARGUMENTS",
        encoding="utf-8",
    )
    monkeypatch.chdir(work)

    repl = build_app(console=_record_console(), show_banner=False)

    printed: list[object] = []
    monkeypatch.setattr(repl, "print", lambda r: printed.append(r))
    monkeypatch.setattr(repl, "send_user_message", lambda text: pytest.fail("不应触发"))
    # 桩掉 activator.activate 避免真跑子对话（worker 线程 + provider）。
    monkeypatch.setattr(repl._activator, "activate", lambda name, args: "回流摘要XYZ")

    spec = repl._commands.lookup("audit")
    spec.handler(repl, "目标")

    assert any("回流摘要XYZ" in str(p) for p in printed)


def test_skill_review_replaces_stock_review_command(tmp_env, monkeypatch, tmp_path):
    """冲突替换：project skill `review`（PROMPT skill）替换内置 /review（PROMPT 控制）。
    lookup('review') 变成 skill 的 handler、类型仍 PROMPT；build_app 不抛。"""
    from wentian.cli import build_app
    from wentian.commands.spec import CommandType

    _empty_builtin_discovery(monkeypatch, tmp_path)
    work = tmp_path / "reviewwork"
    work.mkdir()
    _write_project_skill(work, "review", allowed_tools=["read_file"])
    monkeypatch.chdir(work)

    repl = build_app(console=_record_console(), show_banner=False)

    spec = repl._commands.lookup("review")
    assert spec is not None
    assert spec.type == CommandType.PROMPT
    # 是 skill handler 而非内置 _h_review：激活后进激活集（内置 _h_review 不会）。
    sent: list[str] = []
    monkeypatch.setattr(repl, "send_user_message", lambda text: sent.append(text))
    spec.handler(repl, "")
    assert "review" in dict(repl._activator.active_bodies())


def test_skill_named_exit_skipped_with_warning(tmp_env, monkeypatch, tmp_path):
    """冲突跳过：project skill `exit`（与内置控制命令同名）→ 不注册斜杠、警告打印、
    lookup('exit') 仍是内置控制命令、build_app 不抛、load_skill 激活仍可用。"""
    from wentian.cli import build_app
    from wentian.commands.spec import CommandType

    _empty_builtin_discovery(monkeypatch, tmp_path)
    work = tmp_path / "exitwork"
    work.mkdir()
    _write_project_skill(work, "exit", allowed_tools=["read_file"])
    monkeypatch.chdir(work)

    console = _record_console()
    repl = build_app(console=console, show_banner=False)  # 不抛

    spec = repl._commands.lookup("exit")
    assert spec is not None
    # 仍是内置控制命令（LOCAL），不是 PROMPT skill。
    assert spec.type == CommandType.LOCAL
    # 警告已打印。
    out = console.export_text()
    assert "exit" in out and ("跳过" in out or "同名" in out)
    # skill 仍经 load_skill / activator 可加载（activate 不报「未找到」）。
    result = repl._activator.activate("exit", "")
    assert "未找到" not in result


def test_skill_named_skills_skipped_protected(tmp_env, monkeypatch, tmp_path):
    """`/skills` 是受保护控制命令：同名 user/project skill 跳过斜杠注册 + 警告。"""
    from wentian.cli import build_app
    from wentian.commands.spec import CommandType

    _empty_builtin_discovery(monkeypatch, tmp_path)
    work = tmp_path / "skillsclash"
    work.mkdir()
    _write_project_skill(work, "skills", allowed_tools=["read_file"])
    monkeypatch.chdir(work)

    repl = build_app(console=_record_console(), show_banner=False)  # 不抛

    spec = repl._commands.lookup("skills")
    assert spec is not None
    assert spec.type == CommandType.LOCAL  # 是 /skills 控制命令，未被 skill 覆盖


def test_skills_command_lists_discovered(tmp_env, monkeypatch, tmp_path):
    """/skills 列出发现的 skill（含 source·mode），零 provider 调用。"""
    from wentian.cli import build_app

    work = tmp_path / "listwork"
    work.mkdir()
    _write_project_skill(work, "deploy", allowed_tools=["read_file"])
    monkeypatch.chdir(work)

    console = _record_console()
    repl = build_app(console=console, show_banner=False)

    # provider.stream 被调即失败（/skills 必须纯本地）。
    monkeypatch.setattr(
        repl._provider, "stream", lambda *a, **k: pytest.fail("不应调 provider")
    )

    spec = repl._commands.lookup("skills")
    assert spec is not None
    spec.handler(repl, "")

    out = console.export_text()
    # 含内置 commit/review/test + project deploy，且带 source/mode 标注。
    assert "deploy" in out
    assert "commit" in out
    assert "project" in out
    assert "shared" in out


def test_skills_reload_picks_up_new_file(tmp_env, monkeypatch, tmp_path):
    """/skills reload：新增一个 skill 文件后重载 → 命令注册中心出现新命令。"""
    from wentian.cli import build_app

    _empty_builtin_discovery(monkeypatch, tmp_path)
    work = tmp_path / "reloadwork"
    work.mkdir()
    _write_project_skill(work, "deploy", allowed_tools=["read_file"])
    monkeypatch.chdir(work)

    repl = build_app(console=_record_console(), show_banner=False)
    assert repl._commands.lookup("deploy") is not None
    assert repl._commands.lookup("ship") is None

    # 新增 ship.md。
    _write_project_skill(work, "ship", allowed_tools=["read_file"])

    spec = repl._commands.lookup("skills")
    spec.handler(repl, "reload")

    assert repl._commands.lookup("ship") is not None
    assert repl._commands.lookup("deploy") is not None


def test_skills_reload_removes_deleted_file(tmp_env, monkeypatch, tmp_path):
    """/skills reload：删掉一个 skill 文件后重载 → 命令注册中心移除该命令。"""
    from wentian.cli import build_app

    _empty_builtin_discovery(monkeypatch, tmp_path)
    work = tmp_path / "reloaddel"
    work.mkdir()
    _write_project_skill(work, "deploy", allowed_tools=["read_file"])
    _write_project_skill(work, "ship", allowed_tools=["read_file"])
    monkeypatch.chdir(work)

    repl = build_app(console=_record_console(), show_banner=False)
    assert repl._commands.lookup("ship") is not None

    (work / ".wentian" / "skills" / "ship.md").unlink()

    spec = repl._commands.lookup("skills")
    spec.handler(repl, "reload")

    assert repl._commands.lookup("ship") is None
    assert repl._commands.lookup("deploy") is not None


def test_skills_reload_invalid_tool_keeps_old(tmp_env, monkeypatch, tmp_path):
    """/skills reload：新 skill 引用不存在的工具 → 保留旧 registry，打印错误，不崩。"""
    from wentian.cli import build_app

    _empty_builtin_discovery(monkeypatch, tmp_path)
    work = tmp_path / "reloadbad"
    work.mkdir()
    _write_project_skill(work, "deploy", allowed_tools=["read_file"])
    monkeypatch.chdir(work)

    console = _record_console()
    repl = build_app(console=console, show_banner=False)

    # 新增引用不存在工具的 skill。
    _write_project_skill(work, "broken", allowed_tools=["no_such_tool"])

    spec = repl._commands.lookup("skills")
    spec.handler(repl, "reload")  # 不抛

    # 旧命令仍在；坏 skill 未注册。
    assert repl._commands.lookup("deploy") is not None
    assert repl._commands.lookup("broken") is None
    out = console.export_text()
    assert "broken" in out and "no_such_tool" in out


def test_skills_reload_disabled_friendly(tmp_env, monkeypatch, tmp_path):
    """skills 系统未启用时 /skills reload → 友好提示，不崩。"""
    import yaml as _yaml

    from wentian.cli import build_app

    cfg = dict(_GOOD_CONFIG)
    cfg["skills"] = {"enabled": False}
    cfg_path = tmp_path / "noskill2.yaml"
    cfg_path.write_text(_yaml.dump(cfg), encoding="utf-8")

    work = tmp_path / "disabledreload"
    work.mkdir()
    monkeypatch.chdir(work)

    repl = build_app(
        cfg_path, tmp_path / "sess2", console=_record_console(), show_banner=False
    )
    # skills 关闭 ⇒ /skills 命令也不注册。
    assert repl._commands.lookup("skills") is None


def test_skills_disabled_no_skill_commands(tmp_env, monkeypatch, tmp_path):
    """skills.enabled=false → /skills、/skills reload、skill 斜杠命令一概不注册；
    v0.10 命令集不变（/review 仍是内置）。"""
    import yaml as _yaml

    from wentian.cli import build_app
    from wentian.commands.spec import CommandType

    cfg = dict(_GOOD_CONFIG)
    cfg["skills"] = {"enabled": False}
    cfg_path = tmp_path / "noskill3.yaml"
    cfg_path.write_text(_yaml.dump(cfg), encoding="utf-8")

    work = tmp_path / "disabledcmds"
    work.mkdir()
    _write_project_skill(work, "deploy", allowed_tools=["read_file"])
    monkeypatch.chdir(work)

    repl = build_app(
        cfg_path, tmp_path / "sess3", console=_record_console(), show_banner=False
    )
    assert repl._commands.lookup("skills") is None
    assert repl._commands.lookup("deploy") is None
    # /review 仍是内置 PROMPT 控制命令（未被 skill 替换，因为 skills 关）。
    review = repl._commands.lookup("review")
    assert review is not None
    assert review.type == CommandType.PROMPT


# v0.11 · F84/F89（T135 review-fix）— isolated 子对话 provider 应用 skill.model 覆盖
def test_skill_provider_cfg_applies_model_override():
    """`_skill_provider_cfg` 在 skill.model 非空时覆盖模型、其余字段不变；None 则原样。"""
    from wentian.cli import _skill_provider_cfg
    from wentian.config import ProviderConfig

    cfg = ProviderConfig(
        name="p", protocol="anthropic", model="base-model", api_key="k", base_url="u"
    )
    overridden = _skill_provider_cfg(cfg, "skill-model")
    assert overridden.model == "skill-model"
    assert overridden.name == "p"
    assert overridden.protocol == "anthropic"
    assert overridden.api_key == "k"
    assert overridden.base_url == "u"
    # 原 cfg 不被 mutate。
    assert cfg.model == "base-model"
    # None / 空 → 原样模型。
    assert _skill_provider_cfg(cfg, None).model == "base-model"
    assert _skill_provider_cfg(cfg, "").model == "base-model"


# ═══════════════════════════════════════════════════════════════════════════════
# v0.12 · C99 · T124 — hook engine assembly in build_app
# ═══════════════════════════════════════════════════════════════════════════════


def _hooks_config() -> dict:
    """A minimal valid YAML config that includes a hooks section."""
    import copy

    cfg = copy.deepcopy(_GOOD_CONFIG)
    cfg["hooks"] = [
        {
            "event": "SessionStart",
            "action": {"type": "prompt", "text": "hello"},
        }
    ]
    return cfg


def _write_hooks_config(path: Path) -> None:
    """Write a hooks-enabled config to path."""
    (path / "wentian" / "config.yaml").write_text(
        yaml.dump(_hooks_config()), encoding="utf-8"
    )


def test_build_app_with_hooks_config_injects_hook_engine(tmp_env):
    """build_app with hooks config → REPL._hooks is a non-None HookEngine."""
    from wentian.cli import build_app
    from wentian.hooks.engine import HookEngine

    cfg_dir, _ = tmp_env
    _write_hooks_config(cfg_dir)

    repl = build_app(console=_record_console(), show_banner=False)

    assert repl._hooks is not None
    assert isinstance(repl._hooks, HookEngine)


def test_build_app_without_hooks_config_has_none_hooks(tmp_env):
    """build_app with no hooks config → REPL._hooks is None."""
    from wentian.cli import build_app

    # tmp_env has _GOOD_CONFIG without hooks section
    repl = build_app(console=_record_console(), show_banner=False)

    assert repl._hooks is None


def test_build_app_with_hooks_compactor_has_on_pre_compact(tmp_env):
    """When hooks present, compactor gets a non-None on_pre_compact callback."""
    from wentian.cli import build_app

    cfg_dir, _ = tmp_env
    _write_hooks_config(cfg_dir)

    repl = build_app(console=_record_console(), show_banner=False)

    # The compactor should have been constructed with on_pre_compact
    assert repl._compactor is not None
    assert repl._compactor._on_pre_compact is not None


# ═══════════════════════════════════════════════════════════════════════════════
# v0.13 · C117 · F91/F93/F98/F99/N50（任务 T145）— Agent 工具装配接线
# ═══════════════════════════════════════════════════════════════════════════════


def test_build_app_registers_agent_tool_in_registry(tmp_env):
    """T145 · N50 — build_app 后工具注册表含 'Agent' 工具（恒在，N50 有意新默认）。"""
    from wentian.cli import build_app

    repl = build_app(console=_record_console(), show_banner=False)

    assert repl._registry is not None
    assert "Agent" in repl._registry.names()


def test_build_app_agent_tool_is_last_in_default_registry(tmp_env):
    """T145 — Agent 工具注册在 load_skill 之后（注册顺序：6标准工具 + load_skill + Agent）。"""
    from wentian.cli import build_app

    repl = build_app(console=_record_console(), show_banner=False)

    names = repl._registry.names()
    assert names == [*_SIX_TOOLS, "load_skill", "Agent"]


def test_build_app_command_registry_has_agents_command(tmp_env):
    """T145 — 命令注册表含 /agents 命令。"""
    from wentian.cli import build_app

    repl = build_app(console=_record_console(), show_banner=False)

    assert repl._commands is not None
    visible_names = {cmd.name for cmd in repl._commands.visible()}
    assert "agents" in visible_names


def test_build_app_repl_holds_agents_manager_handle(tmp_env):
    """T145 · F98/F99 — 默认配置（agents.enabled=True）下，REPL 持有 manager 句柄（非 None）。"""
    from wentian.cli import build_app

    repl = build_app(console=_record_console(), show_banner=False)

    assert repl.agents_manager() is not None


def test_build_app_agent_tool_injected_with_correct_cfg(tmp_env):
    """T145 — AgentTool 持有正确 cfg（AgentsConfig 实例）。"""
    from wentian.cli import build_app
    from wentian.agents.tool import AgentTool
    from wentian.config import AgentsConfig

    repl = build_app(console=_record_console(), show_banner=False)

    agent_tool = repl._registry.get("Agent")
    assert isinstance(agent_tool, AgentTool)
    assert isinstance(agent_tool._cfg, AgentsConfig)


def test_build_app_agents_disabled_manager_is_none(tmp_env):
    """T145 · N50 — config.agents.enabled=False → manager=None；
    'Agent' 工具仍注册（fork-only 可用，N50）。"""
    import yaml

    from wentian.cli import build_app

    cfg_dir, _ = tmp_env
    cfg = dict(_GOOD_CONFIG)
    cfg["agents"] = {"enabled": False}
    (cfg_dir / "wentian" / "config.yaml").write_text(yaml.dump(cfg), encoding="utf-8")

    repl = build_app(console=_record_console(), show_banner=False)

    # Manager 为 None（agents 禁用）
    assert repl.agents_manager() is None
    # 但 Agent 工具仍在注册表（N50：fork-only 恒可用）
    assert "Agent" in repl._registry.names()


def test_build_app_agents_disabled_agent_tool_no_role_graceful(tmp_env):
    """T145 · N50 — agents.enabled=False 时，Agent 工具 type=definition + 无已知角色 →
    返「无此角色」优雅错误，不崩溃。"""
    import yaml

    from wentian.cli import build_app

    cfg_dir, _ = tmp_env
    cfg = dict(_GOOD_CONFIG)
    cfg["agents"] = {"enabled": False}
    (cfg_dir / "wentian" / "config.yaml").write_text(yaml.dump(cfg), encoding="utf-8")

    repl = build_app(console=_record_console(), show_banner=False)

    agent_tool = repl._registry.get("Agent")
    assert agent_tool is not None
    result = agent_tool.run(
        {"type": "definition", "agent_type": "nonexistent", "prompt": "test"}
    )
    assert "无此角色" in result


def test_build_app_six_tools_count_after_agent_tool(tmp_env):
    """T145 — 默认 build_app 后工具总数为 8（6 标准 + load_skill + Agent）。

    N50 意图：Agent 工具是新的「+1 默认工具」，令工具集从 7 升到 8。"""
    from wentian.cli import build_app

    _disable_skills(tmp_env)  # 关掉 skills → 只有 6 标准工具 + Agent（共 7）

    repl = build_app(console=_record_console(), show_banner=False)
    names = list(repl._registry.names())
    # skills 关掉时：6 标准工具 + Agent（无 load_skill）
    assert len(names) == 7
    assert "Agent" in names
