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

    assert "0.2.0" in out
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
