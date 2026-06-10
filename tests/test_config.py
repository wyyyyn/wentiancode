"""Tests for config.py: load_config, Config, ProviderConfig, ConfigError."""
import pytest
from pathlib import Path

from wentian.config import load_config, Config, ProviderConfig, ConfigError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_YAML = """\
default: claude
providers:
  claude:
    protocol: anthropic
    model: claude-opus-4-8
    api_key: sk-ant-test
    thinking: true
  deepseek:
    protocol: openai
    model: deepseek-chat
    base_url: https://api.deepseek.com
    api_key: sk-ds-test
"""


def write_yaml(tmp_path: Path, content: str) -> Path:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(content)
    return cfg


# ---------------------------------------------------------------------------
# T3-1  load_config returns a valid Config with correct field values
# ---------------------------------------------------------------------------

class TestLoadConfigValid:
    def test_returns_config_instance(self, tmp_path):
        path = write_yaml(tmp_path, VALID_YAML)
        cfg = load_config(path)
        assert isinstance(cfg, Config)

    def test_default_is_claude(self, tmp_path):
        path = write_yaml(tmp_path, VALID_YAML)
        cfg = load_config(path)
        assert cfg.default == "claude"

    def test_providers_keys(self, tmp_path):
        path = write_yaml(tmp_path, VALID_YAML)
        cfg = load_config(path)
        assert set(cfg.providers.keys()) == {"claude", "deepseek"}

    def test_claude_provider_fields(self, tmp_path):
        path = write_yaml(tmp_path, VALID_YAML)
        cfg = load_config(path)
        p = cfg.providers["claude"]
        assert isinstance(p, ProviderConfig)
        assert p.name == "claude"
        assert p.protocol == "anthropic"
        assert p.model == "claude-opus-4-8"
        assert p.api_key == "sk-ant-test"
        assert p.base_url is None

    def test_thinking_parsed_as_bool_true(self, tmp_path):
        path = write_yaml(tmp_path, VALID_YAML)
        cfg = load_config(path)
        assert cfg.providers["claude"].thinking is True

    def test_deepseek_provider_fields(self, tmp_path):
        path = write_yaml(tmp_path, VALID_YAML)
        cfg = load_config(path)
        p = cfg.providers["deepseek"]
        assert isinstance(p, ProviderConfig)
        assert p.name == "deepseek"
        assert p.protocol == "openai"
        assert p.model == "deepseek-chat"
        assert p.api_key == "sk-ds-test"
        assert p.base_url == "https://api.deepseek.com"

    def test_thinking_defaults_to_false(self, tmp_path):
        path = write_yaml(tmp_path, VALID_YAML)
        cfg = load_config(path)
        assert cfg.providers["deepseek"].thinking is False


# ---------------------------------------------------------------------------
# T3-2  Config.get()
# ---------------------------------------------------------------------------

class TestConfigGet:
    def setup_method(self):
        self.claude = ProviderConfig(
            name="claude",
            protocol="anthropic",
            model="claude-opus-4-8",
            api_key="sk-ant-test",
            thinking=True,
        )
        self.deepseek = ProviderConfig(
            name="deepseek",
            protocol="openai",
            model="deepseek-chat",
            api_key="sk-ds-test",
            base_url="https://api.deepseek.com",
        )
        self.cfg = Config(
            providers={"claude": self.claude, "deepseek": self.deepseek},
            default="claude",
        )

    def test_get_no_arg_returns_default(self):
        assert self.cfg.get() is self.claude

    def test_get_by_name_claude(self):
        assert self.cfg.get("claude") is self.claude

    def test_get_by_name_deepseek(self):
        assert self.cfg.get("deepseek") is self.deepseek

    def test_get_missing_name_raises_config_error(self):
        with pytest.raises(ConfigError) as exc_info:
            self.cfg.get("nonexistent")
        # error message should mention the provider name
        assert "nonexistent" in str(exc_info.value)


# ---------------------------------------------------------------------------
# T3-3  Validation errors
# ---------------------------------------------------------------------------

class TestValidationErrors:
    def test_missing_api_key_raises_config_error(self, tmp_path):
        yaml = """\
default: claude
providers:
  claude:
    protocol: anthropic
    model: claude-opus-4-8
"""
        path = write_yaml(tmp_path, yaml)
        with pytest.raises(ConfigError) as exc_info:
            load_config(path)
        assert "api_key" in str(exc_info.value)

    def test_invalid_protocol_raises_config_error(self, tmp_path):
        yaml = """\
default: claude
providers:
  claude:
    protocol: grpc
    model: claude-opus-4-8
    api_key: sk-ant-test
"""
        path = write_yaml(tmp_path, yaml)
        with pytest.raises(ConfigError) as exc_info:
            load_config(path)
        assert "protocol" in str(exc_info.value)

    def test_default_pointing_to_nonexistent_provider_raises(self, tmp_path):
        yaml = """\
default: missing
providers:
  claude:
    protocol: anthropic
    model: claude-opus-4-8
    api_key: sk-ant-test
"""
        path = write_yaml(tmp_path, yaml)
        with pytest.raises(ConfigError) as exc_info:
            load_config(path)
        assert "default" in str(exc_info.value)

    def test_file_not_found_raises_config_error(self, tmp_path):
        missing = tmp_path / "does_not_exist.yaml"
        with pytest.raises(ConfigError):
            load_config(missing)

    def test_missing_model_raises_config_error(self, tmp_path):
        yaml = """\
default: claude
providers:
  claude:
    protocol: anthropic
    api_key: sk-ant-test
"""
        path = write_yaml(tmp_path, yaml)
        with pytest.raises(ConfigError) as exc_info:
            load_config(path)
        assert "model" in str(exc_info.value)
