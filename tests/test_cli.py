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
