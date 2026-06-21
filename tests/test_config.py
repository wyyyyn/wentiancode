"""Tests for config.py: load_config, Config, ProviderConfig, ConfigError."""

import warnings
import pytest
from pathlib import Path

from wentian.config import (
    load_config,
    Config,
    ProviderConfig,
    ConfigError,
    StdioServerConfig,
    HttpServerConfig,
    # v0.8 · C51 · F56/F58（任务 T91）
    ContextConfig,
)


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


# ---------------------------------------------------------------------------
# v0.7 · C44 · F50/N23（任务 T82）—— 两层深合并 + MCP Server + ${VAR} 展开
# ---------------------------------------------------------------------------

# 用户级基础 YAML（含两个 provider）
USER_YAML = """\
default: claude
providers:
  claude:
    protocol: anthropic
    model: claude-opus-4-8
    api_key: sk-ant-user
  deepseek:
    protocol: openai
    model: deepseek-chat
    base_url: https://api.deepseek.com
    api_key: sk-ds-user
"""

# 项目级 YAML：覆盖 claude provider，并新增 local provider
PROJECT_YAML = """\
default: local
providers:
  claude:
    protocol: anthropic
    model: claude-sonnet-proj
    api_key: sk-ant-proj
  local:
    protocol: openai
    model: local-llm
    api_key: sk-local
"""

# 含 mcpServers 的用户级 YAML
USER_YAML_WITH_MCP = """\
default: claude
providers:
  claude:
    protocol: anthropic
    model: claude-opus-4-8
    api_key: sk-ant-user
mcpServers:
  user-fs:
    command: npx
    args: ["-y", "@mcp/server-fs", "/home"]
    env:
      TOKEN: static-token
"""

# 含 mcpServers 的项目级 YAML（覆盖 user-fs，新增 remote）
PROJECT_YAML_WITH_MCP = """\
mcpServers:
  user-fs:
    command: npx
    args: ["-y", "@mcp/server-fs", "/proj"]
    env: {}
  remote:
    url: https://example.com/mcp
    headers:
      Authorization: Bearer ${API_KEY}
"""


def _write_two_layer(tmp_path: Path, user_content: str, project_content: str):
    """Write user-level and project-level config files, return (user_path, project_dir)."""
    user_cfg = tmp_path / "user_cfg.yaml"
    user_cfg.write_text(user_content)
    proj_dir = tmp_path / "project"
    proj_dir.mkdir()
    proj_wentian = proj_dir / ".wentian"
    proj_wentian.mkdir()
    (proj_wentian / "config.yaml").write_text(project_content)
    return user_cfg, proj_dir


class TestTwoLayerLoadBackwardCompat:
    """N23：仅用户级文件存在时，结果与旧单文件加载完全等价。"""

    def test_only_user_level_equiv_single_file(self, tmp_path):
        """仅用户级文件时，providers/default 不变，mcp_servers 为空 dict。"""
        user_cfg = tmp_path / "user.yaml"
        user_cfg.write_text(USER_YAML)
        # 传 _user_path，不传 _project_path（不存在则跳过）
        cfg = load_config(_user_path=user_cfg, _project_path=tmp_path / "nonexistent")
        assert cfg.default == "claude"
        assert set(cfg.providers.keys()) == {"claude", "deepseek"}
        assert cfg.providers["claude"].api_key == "sk-ant-user"
        assert cfg.mcp_servers == {}

    def test_explicit_path_still_single_file(self, tmp_path):
        """显式 path= 时走单文件直载，mcp_servers 为空 dict，不触发两层。"""
        user_cfg, proj_dir = _write_two_layer(tmp_path, USER_YAML, PROJECT_YAML)
        # 显式 path 应该只读用户级文件，忽略 proj_dir 里的项目级
        cfg = load_config(path=user_cfg)
        assert cfg.default == "claude"
        assert "local" not in cfg.providers
        assert cfg.mcp_servers == {}


class TestTwoLayerMerge:
    """F50：两层深合并语义。"""

    def test_project_overrides_provider(self, tmp_path):
        """项目级同名 provider 覆盖用户级。"""
        user_cfg, proj_dir = _write_two_layer(tmp_path, USER_YAML, PROJECT_YAML)
        cfg = load_config(
            _user_path=user_cfg,
            _project_path=proj_dir / ".wentian" / "config.yaml",
        )
        # claude 被项目级覆盖
        assert cfg.providers["claude"].model == "claude-sonnet-proj"
        assert cfg.providers["claude"].api_key == "sk-ant-proj"

    def test_project_adds_new_provider(self, tmp_path):
        """项目级新增 provider 被并入。"""
        user_cfg, proj_dir = _write_two_layer(tmp_path, USER_YAML, PROJECT_YAML)
        cfg = load_config(
            _user_path=user_cfg,
            _project_path=proj_dir / ".wentian" / "config.yaml",
        )
        assert "local" in cfg.providers

    def test_user_only_provider_preserved(self, tmp_path):
        """用户级独有 provider 在合并后仍保留。"""
        user_cfg, proj_dir = _write_two_layer(tmp_path, USER_YAML, PROJECT_YAML)
        cfg = load_config(
            _user_path=user_cfg,
            _project_path=proj_dir / ".wentian" / "config.yaml",
        )
        assert "deepseek" in cfg.providers
        assert cfg.providers["deepseek"].api_key == "sk-ds-user"

    def test_project_default_overrides_user_default(self, tmp_path):
        """项目级 default 覆盖用户级。"""
        user_cfg, proj_dir = _write_two_layer(tmp_path, USER_YAML, PROJECT_YAML)
        cfg = load_config(
            _user_path=user_cfg,
            _project_path=proj_dir / ".wentian" / "config.yaml",
        )
        assert cfg.default == "local"

    def test_project_missing_skipped(self, tmp_path):
        """项目级文件不存在时跳过，不报错。"""
        user_cfg = tmp_path / "user.yaml"
        user_cfg.write_text(USER_YAML)
        cfg = load_config(
            _user_path=user_cfg,
            _project_path=tmp_path / "nonexistent" / ".wentian" / "config.yaml",
        )
        assert cfg.default == "claude"


class TestMCPServerParsing:
    """MCP Server 配置数据结构解析。"""

    def test_stdio_server_parsed(self, tmp_path):
        """带 command 的条目解析为 StdioServerConfig。"""
        yaml_content = """\
default: claude
providers:
  claude:
    protocol: anthropic
    model: claude-opus-4-8
    api_key: sk-ant-test
mcpServers:
  myfs:
    command: npx
    args: ["-y", "@mcp/server-fs", "/tmp"]
    env:
      FOO: bar
"""
        path = tmp_path / "cfg.yaml"
        path.write_text(yaml_content)
        cfg = load_config(path=path)
        assert "myfs" in cfg.mcp_servers
        srv = cfg.mcp_servers["myfs"]
        assert isinstance(srv, StdioServerConfig)
        assert srv.command == "npx"
        assert srv.args == ["-y", "@mcp/server-fs", "/tmp"]
        assert srv.env == {"FOO": "bar"}

    def test_stdio_server_defaults(self, tmp_path):
        """StdioServerConfig 的 args/env 默认为 []/{}。"""
        yaml_content = """\
default: claude
providers:
  claude:
    protocol: anthropic
    model: claude-opus-4-8
    api_key: sk-ant-test
mcpServers:
  bare:
    command: /usr/bin/mcp-tool
"""
        path = tmp_path / "cfg.yaml"
        path.write_text(yaml_content)
        cfg = load_config(path=path)
        srv = cfg.mcp_servers["bare"]
        assert isinstance(srv, StdioServerConfig)
        assert srv.args == []
        assert srv.env == {}

    def test_http_server_parsed(self, tmp_path):
        """带 url 的条目解析为 HttpServerConfig。"""
        yaml_content = """\
default: claude
providers:
  claude:
    protocol: anthropic
    model: claude-opus-4-8
    api_key: sk-ant-test
mcpServers:
  remote:
    url: https://example.com/mcp
    headers:
      Authorization: Bearer token123
"""
        path = tmp_path / "cfg.yaml"
        path.write_text(yaml_content)
        cfg = load_config(path=path)
        srv = cfg.mcp_servers["remote"]
        assert isinstance(srv, HttpServerConfig)
        assert srv.url == "https://example.com/mcp"
        assert srv.headers == {"Authorization": "Bearer token123"}

    def test_http_server_headers_default(self, tmp_path):
        """HttpServerConfig 的 headers 默认为 {}。"""
        yaml_content = """\
default: claude
providers:
  claude:
    protocol: anthropic
    model: claude-opus-4-8
    api_key: sk-ant-test
mcpServers:
  bare-http:
    url: https://bare.example.com/mcp
"""
        path = tmp_path / "cfg.yaml"
        path.write_text(yaml_content)
        cfg = load_config(path=path)
        srv = cfg.mcp_servers["bare-http"]
        assert isinstance(srv, HttpServerConfig)
        assert srv.headers == {}

    def test_mcp_two_layer_merge(self, tmp_path):
        """两层对 mcpServers 同样进行深合并。"""
        user_cfg, proj_dir = _write_two_layer(
            tmp_path, USER_YAML_WITH_MCP, PROJECT_YAML_WITH_MCP
        )
        cfg = load_config(
            _user_path=user_cfg,
            _project_path=proj_dir / ".wentian" / "config.yaml",
        )
        # user-fs 被项目级覆盖（args 变了）
        assert isinstance(cfg.mcp_servers["user-fs"], StdioServerConfig)
        assert "/proj" in cfg.mcp_servers["user-fs"].args
        # remote 是项目级新增
        assert "remote" in cfg.mcp_servers

    def test_invalid_server_no_command_no_url_raises(self, tmp_path):
        """既无 command 又无 url 的条目 → ConfigError，消息含字段名。"""
        yaml_content = """\
default: claude
providers:
  claude:
    protocol: anthropic
    model: claude-opus-4-8
    api_key: sk-ant-test
mcpServers:
  broken:
    env:
      FOO: bar
"""
        path = tmp_path / "cfg.yaml"
        path.write_text(yaml_content)
        with pytest.raises(ConfigError) as exc_info:
            load_config(path=path)
        err = str(exc_info.value)
        assert "command" in err or "url" in err


class TestVarExpansion:
    """${VAR} 展开：env/headers 值，缺失变量 → 空串 + 告警。"""

    def test_env_var_expanded_from_environ(self, tmp_path, monkeypatch):
        """stdio env 中的 ${VAR} 从 os.environ 展开。"""
        monkeypatch.setenv("MY_TOKEN", "secret-token")
        yaml_content = """\
default: claude
providers:
  claude:
    protocol: anthropic
    model: claude-opus-4-8
    api_key: sk-ant-test
mcpServers:
  myfs:
    command: npx
    env:
      TOKEN: ${MY_TOKEN}
"""
        path = tmp_path / "cfg.yaml"
        path.write_text(yaml_content)
        cfg = load_config(path=path)
        assert cfg.mcp_servers["myfs"].env["TOKEN"] == "secret-token"

    def test_headers_var_expanded_from_environ(self, tmp_path, monkeypatch):
        """http headers 中的 ${VAR} 从 os.environ 展开。"""
        monkeypatch.setenv("API_KEY", "my-api-key")
        yaml_content = """\
default: claude
providers:
  claude:
    protocol: anthropic
    model: claude-opus-4-8
    api_key: sk-ant-test
mcpServers:
  remote:
    url: https://example.com/mcp
    headers:
      Authorization: Bearer ${API_KEY}
"""
        path = tmp_path / "cfg.yaml"
        path.write_text(yaml_content)
        cfg = load_config(path=path)
        assert cfg.mcp_servers["remote"].headers["Authorization"] == "Bearer my-api-key"

    def test_missing_var_expands_to_empty_string_with_warning(
        self, tmp_path, monkeypatch
    ):
        """缺失的 ${VAR} 展开为空串，并发出告警。"""
        monkeypatch.delenv("MISSING_VAR", raising=False)
        yaml_content = """\
default: claude
providers:
  claude:
    protocol: anthropic
    model: claude-opus-4-8
    api_key: sk-ant-test
mcpServers:
  myfs:
    command: npx
    env:
      TOKEN: ${MISSING_VAR}
"""
        path = tmp_path / "cfg.yaml"
        path.write_text(yaml_content)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            cfg = load_config(path=path)
        assert cfg.mcp_servers["myfs"].env["TOKEN"] == ""
        assert len(w) >= 1
        assert any("MISSING_VAR" in str(warning.message) for warning in w)


# ---------------------------------------------------------------------------
# v0.8 · C51 · F56/F58（任务 T91）—— ContextConfig + ProviderConfig.context_window
# ---------------------------------------------------------------------------

# 顶层 context: 部分覆盖（用户级）；项目级再覆盖其中两键 → 验证逐键深合并
USER_YAML_WITH_CONTEXT = """\
default: claude
providers:
  claude:
    protocol: anthropic
    model: claude-opus-4-8
    api_key: sk-ant-user
context:
  default_window: 128000
  recent_keep_tokens: 5000
  char_per_token: 4.0
"""

PROJECT_YAML_WITH_CONTEXT = """\
context:
  recent_keep_tokens: 7000
  reserved_output: 32000
"""


class TestContextConfigDefaults:
    """RED 1：无 context: 块 → Config.context == ContextConfig()（全默认）。"""

    DEFAULTS = {
        "default_window": 200_000,
        "reserved_output": 64_000,
        "auto_margin": 13_000,
        "manual_margin": 3_000,
        "recent_keep_tokens": 10_000,
        "recent_keep_min_messages": 5,
        "offload_single_tokens": 2_000,
        "offload_round_sum_tokens": 8_000,
        "char_per_token": 3.5,
    }

    def test_dataclass_default_instance_field_values(self):
        """ContextConfig() 各字段默认值与 spec 一致。"""
        cc = ContextConfig()
        for field_name, expected in self.DEFAULTS.items():
            assert getattr(cc, field_name) == expected

    def test_no_context_block_uses_all_defaults(self, tmp_path):
        """配置无 context: 块 → cfg.context == ContextConfig()（全默认）。"""
        path = write_yaml(tmp_path, VALID_YAML)
        cfg = load_config(path)
        assert cfg.context == ContextConfig()

    def test_config_default_factory_when_constructed_directly(self):
        """直接构造 Config（不传 context）→ 缺省为 ContextConfig()。"""
        cfg = Config(
            providers={
                "claude": ProviderConfig(
                    name="claude",
                    protocol="anthropic",
                    model="m",
                    api_key="k",
                )
            },
            default="claude",
        )
        assert cfg.context == ContextConfig()

    def test_context_config_is_frozen(self):
        """ContextConfig 是 frozen dataclass（不可变）。"""
        cc = ContextConfig()
        with pytest.raises(Exception):
            cc.default_window = 1  # type: ignore[misc]


class TestContextConfigParsingAndMerge:
    """RED 2：context: 块部分字段覆盖 + 两层深合并对 context 块逐键生效。"""

    def test_partial_override_single_file(self, tmp_path):
        """单文件 context: 部分字段覆盖 → 仅覆盖项变，其余走默认。"""
        path = write_yaml(tmp_path, USER_YAML_WITH_CONTEXT)
        cfg = load_config(path)
        # 覆盖项
        assert cfg.context.default_window == 128_000
        assert cfg.context.recent_keep_tokens == 5_000
        assert cfg.context.char_per_token == 4.0
        # 未覆盖项保持默认
        assert cfg.context.reserved_output == 64_000
        assert cfg.context.auto_margin == 13_000
        assert cfg.context.manual_margin == 3_000
        assert cfg.context.recent_keep_min_messages == 5
        assert cfg.context.offload_single_tokens == 2_000
        assert cfg.context.offload_round_sum_tokens == 8_000

    def test_two_layer_merge_per_key(self, tmp_path):
        """两层深合并对 context 块逐键生效：项目级覆盖用户级同键，互不影响其余键。"""
        user_cfg, proj_dir = _write_two_layer(
            tmp_path, USER_YAML_WITH_CONTEXT, PROJECT_YAML_WITH_CONTEXT
        )
        cfg = load_config(
            _user_path=user_cfg,
            _project_path=proj_dir / ".wentian" / "config.yaml",
        )
        # 项目级覆盖：recent_keep_tokens 用户 5000 → 项目 7000
        assert cfg.context.recent_keep_tokens == 7_000
        # 项目级新增覆盖：reserved_output 默认 64000 → 项目 32000
        assert cfg.context.reserved_output == 32_000
        # 用户级独有键保留（项目级没动）
        assert cfg.context.default_window == 128_000
        assert cfg.context.char_per_token == 4.0
        # 两层都没动的键 → 默认
        assert cfg.context.auto_margin == 13_000

    def test_empty_context_block_is_defaults(self, tmp_path):
        """context: 为空映射 → 安全降级为全默认，不抛。"""
        yaml_content = """\
default: claude
providers:
  claude:
    protocol: anthropic
    model: claude-opus-4-8
    api_key: sk-ant-test
context: {}
"""
        path = tmp_path / "cfg.yaml"
        path.write_text(yaml_content)
        cfg = load_config(path=path)
        assert cfg.context == ContextConfig()


class TestProviderContextWindow:
    """RED 3：providers.X.context_window 解析；缺省为 None。"""

    def test_context_window_parsed(self, tmp_path):
        """providers.X.context_window 解析为 ProviderConfig.context_window。"""
        yaml_content = """\
default: claude
providers:
  claude:
    protocol: anthropic
    model: claude-opus-4-8
    api_key: sk-ant-test
    context_window: 1000000
"""
        path = tmp_path / "cfg.yaml"
        path.write_text(yaml_content)
        cfg = load_config(path=path)
        assert cfg.providers["claude"].context_window == 1_000_000

    def test_context_window_defaults_to_none(self, tmp_path):
        """provider 未配 context_window → None。"""
        path = write_yaml(tmp_path, VALID_YAML)
        cfg = load_config(path)
        assert cfg.providers["claude"].context_window is None
        assert cfg.providers["deepseek"].context_window is None


# ---------------------------------------------------------------------------
# v0.9 · C59 · F69（任务 T106）—— MemoryConfig + SessionsConfig
# ---------------------------------------------------------------------------

# 顶层 memory:/sessions: 部分覆盖（用户级）；项目级再覆盖部分键 → 逐键深合并
USER_YAML_WITH_MEM_SESS = """\
default: claude
providers:
  claude:
    protocol: anthropic
    model: claude-opus-4-8
    api_key: sk-ant-user
memory:
  enabled: false
  max_index_lines: 50
sessions:
  retention_days: 7
"""

PROJECT_YAML_WITH_MEM_SESS = """\
memory:
  max_index_lines: 99
sessions:
  resume_gap_reminder_hours: 12
"""


class TestMemoryConfigDefaults:
    """RED：无 memory: 块 → Config.memory == MemoryConfig()（全默认）。"""

    def test_no_memory_block_is_defaults(self, tmp_path):
        from wentian.config import MemoryConfig

        path = write_yaml(tmp_path, VALID_YAML)
        cfg = load_config(path)
        assert cfg.memory == MemoryConfig()

    def test_memory_config_default_field_values(self):
        from wentian.config import MemoryConfig

        mc = MemoryConfig()
        assert mc.enabled is True
        assert mc.provider is None
        assert mc.max_index_lines == 200
        assert mc.max_index_bytes == 25600

    def test_memory_config_is_frozen(self):
        from wentian.config import MemoryConfig

        mc = MemoryConfig()
        with pytest.raises(Exception):
            mc.enabled = False  # type: ignore[misc]

    def test_empty_memory_block_is_defaults(self, tmp_path):
        from wentian.config import MemoryConfig

        yaml_content = """\
default: claude
providers:
  claude:
    protocol: anthropic
    model: claude-opus-4-8
    api_key: sk-ant-test
memory: {}
"""
        path = tmp_path / "cfg.yaml"
        path.write_text(yaml_content)
        cfg = load_config(path=path)
        assert cfg.memory == MemoryConfig()


class TestSessionsConfigDefaults:
    """RED：无 sessions: 块 → Config.sessions == SessionsConfig()（全默认）。"""

    def test_no_sessions_block_is_defaults(self, tmp_path):
        from wentian.config import SessionsConfig

        path = write_yaml(tmp_path, VALID_YAML)
        cfg = load_config(path)
        assert cfg.sessions == SessionsConfig()

    def test_sessions_config_default_field_values(self):
        from wentian.config import SessionsConfig

        sc = SessionsConfig()
        assert sc.retention_days == 30
        assert sc.resume_gap_reminder_hours == 4

    def test_sessions_config_is_frozen(self):
        from wentian.config import SessionsConfig

        sc = SessionsConfig()
        with pytest.raises(Exception):
            sc.retention_days = 1  # type: ignore[misc]

    def test_empty_sessions_block_is_defaults(self, tmp_path):
        from wentian.config import SessionsConfig

        yaml_content = """\
default: claude
providers:
  claude:
    protocol: anthropic
    model: claude-opus-4-8
    api_key: sk-ant-test
sessions: {}
"""
        path = tmp_path / "cfg.yaml"
        path.write_text(yaml_content)
        cfg = load_config(path=path)
        assert cfg.sessions == SessionsConfig()


class TestMemorySessionsParsingAndMerge:
    """RED：部分字段覆盖仅覆盖项变；两层深合并逐键生效；缺块/缺字段安全降级不抛。"""

    def test_partial_override_single_file(self, tmp_path):
        from wentian.config import MemoryConfig, SessionsConfig

        path = write_yaml(tmp_path, USER_YAML_WITH_MEM_SESS)
        cfg = load_config(path)
        # memory 覆盖项
        assert cfg.memory.enabled is False
        assert cfg.memory.max_index_lines == 50
        # memory 未覆盖项保持默认
        assert cfg.memory.provider is None
        assert cfg.memory.max_index_bytes == 25600
        # sessions 覆盖项
        assert cfg.sessions.retention_days == 7
        # sessions 未覆盖项保持默认
        assert cfg.sessions.resume_gap_reminder_hours == 4
        # 类型确认
        assert isinstance(cfg.memory, MemoryConfig)
        assert isinstance(cfg.sessions, SessionsConfig)

    def test_two_layer_merge_per_key(self, tmp_path):
        user_cfg, proj_dir = _write_two_layer(
            tmp_path, USER_YAML_WITH_MEM_SESS, PROJECT_YAML_WITH_MEM_SESS
        )
        cfg = load_config(
            _user_path=user_cfg,
            _project_path=proj_dir / ".wentian" / "config.yaml",
        )
        # memory：项目级覆盖 max_index_lines 50 → 99；enabled 用户级 False 保留
        assert cfg.memory.max_index_lines == 99
        assert cfg.memory.enabled is False
        assert cfg.memory.max_index_bytes == 25600  # 两层都没动 → 默认
        # sessions：用户级 retention_days 7 保留；项目级新增 resume_gap_reminder_hours 12
        assert cfg.sessions.retention_days == 7
        assert cfg.sessions.resume_gap_reminder_hours == 12

    def test_missing_field_safe_default_no_raise(self, tmp_path):
        """memory: 只给一个字段，其余安全降级走默认，不抛。"""
        yaml_content = """\
default: claude
providers:
  claude:
    protocol: anthropic
    model: claude-opus-4-8
    api_key: sk-ant-test
memory:
  provider: deepseek
"""
        path = tmp_path / "cfg.yaml"
        path.write_text(yaml_content)
        cfg = load_config(path=path)
        assert cfg.memory.provider == "deepseek"
        assert cfg.memory.enabled is True  # 默认


class TestMemoryConfigSingleAuthority:
    """RED：config.py 的 MemoryConfig 是唯一权威，store/runner 复用同一类。"""

    def test_store_reexports_config_memoryconfig(self):
        from wentian.config import MemoryConfig as ConfigMC
        from wentian.memory.store import MemoryConfig as StoreMC

        assert StoreMC is ConfigMC

    def test_runner_reexports_config_memoryconfig(self):
        from wentian.config import MemoryConfig as ConfigMC
        from wentian.memory.runner import MemoryConfig as RunnerMC

        assert RunnerMC is ConfigMC


# ---------------------------------------------------------------------------
# v0.11 · C107a · F70（任务 T132）—— SkillsConfig（mirror MemoryConfig）
# ---------------------------------------------------------------------------


class TestSkillsConfigDefaults:
    """RED：无 skills: 块 → Config.skills == SkillsConfig()（全默认，enabled True）。"""

    def test_no_skills_block_is_defaults(self, tmp_path):
        from wentian.config import SkillsConfig

        path = write_yaml(tmp_path, VALID_YAML)
        cfg = load_config(path)
        assert cfg.skills == SkillsConfig()
        assert cfg.skills.enabled is True

    def test_skills_config_default_field_values(self):
        from wentian.config import SkillsConfig

        sc = SkillsConfig()
        assert sc.enabled is True

    def test_skills_config_is_frozen(self):
        from wentian.config import SkillsConfig

        sc = SkillsConfig()
        with pytest.raises(Exception):
            sc.enabled = False  # type: ignore[misc]

    def test_config_default_factory_when_constructed_directly(self):
        from wentian.config import SkillsConfig

        cfg = Config(
            providers={
                "claude": ProviderConfig(
                    name="claude",
                    protocol="anthropic",
                    model="m",
                    api_key="k",
                )
            },
            default="claude",
        )
        assert cfg.skills == SkillsConfig()


class TestSkillsConfigParsing:
    """RED：skills.enabled 解析；空块/非映射安全降级为全默认，不抛。"""

    def test_skills_enabled_false_parsed(self, tmp_path):
        yaml_content = """\
default: claude
providers:
  claude:
    protocol: anthropic
    model: claude-opus-4-8
    api_key: sk-ant-test
skills:
  enabled: false
"""
        path = tmp_path / "cfg.yaml"
        path.write_text(yaml_content)
        cfg = load_config(path=path)
        assert cfg.skills.enabled is False

    def test_empty_skills_block_is_defaults(self, tmp_path):
        from wentian.config import SkillsConfig

        yaml_content = """\
default: claude
providers:
  claude:
    protocol: anthropic
    model: claude-opus-4-8
    api_key: sk-ant-test
skills: {}
"""
        path = tmp_path / "cfg.yaml"
        path.write_text(yaml_content)
        cfg = load_config(path=path)
        assert cfg.skills == SkillsConfig()
        assert cfg.skills.enabled is True


# ---------------------------------------------------------------------------
# v0.12 · C99 · F77（任务 T123）—— Config.hooks + 两层叠加
# ---------------------------------------------------------------------------

# 最小有效 provider 块，供 hooks 测试复用
_PROVIDER_BLOCK = """\
default: claude
providers:
  claude:
    protocol: anthropic
    model: claude-opus-4-8
    api_key: sk-ant-test
"""

# 合法 hooks 列表（单条 PostToolUse shell 规则，background=false，不触发校验拒绝）
_HOOKS_BLOCK_A = """\
hooks:
  - event: PostToolUse
    action:
      type: shell
      command: echo user-hook
"""

_HOOKS_BLOCK_B = """\
hooks:
  - event: SessionStart
    action:
      type: shell
      command: echo project-hook
"""

# 非法规则：PreToolUse + background: true → HookConfigError
_HOOKS_BLOCK_ILLEGAL = """\
hooks:
  - event: PreToolUse
    action:
      type: shell
      command: echo should-fail
    background: true
"""


class TestConfigHooksSingleFile:
    """T123-RED-1: 单文件 hooks: 块解析到 Config.hooks。"""

    def test_with_hooks_block_returns_list_of_hook_rules(self, tmp_path):
        """单文件含 hooks: → Config.hooks 为 list[HookRule]，长度 1。"""
        from wentian.hooks.spec import HookRule

        path = write_yaml(tmp_path, _PROVIDER_BLOCK + _HOOKS_BLOCK_A)
        cfg = load_config(path=path)
        assert isinstance(cfg.hooks, list)
        assert len(cfg.hooks) == 1
        assert isinstance(cfg.hooks[0], HookRule)

    def test_with_hooks_block_event_is_correct(self, tmp_path):
        """解析后规则的 event 与 YAML 声明一致。"""
        from wentian.hooks.spec import HookEvent

        path = write_yaml(tmp_path, _PROVIDER_BLOCK + _HOOKS_BLOCK_A)
        cfg = load_config(path=path)
        assert cfg.hooks[0].event == HookEvent.POST_TOOL_USE

    def test_no_hooks_block_returns_empty_list(self, tmp_path):
        """无 hooks: 块 → Config.hooks == []（安全降级，N40/N41）。"""
        path = write_yaml(tmp_path, _PROVIDER_BLOCK)
        cfg = load_config(path=path)
        assert cfg.hooks == []

    def test_config_hooks_field_defaults_to_empty_list_when_constructed_directly(self):
        """直接构造 Config（不传 hooks）→ hooks 缺省为 []。"""
        from wentian.config import ProviderConfig

        cfg = Config(
            providers={
                "claude": ProviderConfig(
                    name="claude",
                    protocol="anthropic",
                    model="m",
                    api_key="k",
                )
            },
            default="claude",
        )
        assert cfg.hooks == []


class TestConfigHooksTwoLayerConcatenation:
    """T123-RED-2: 两层模式，用户级 + 项目级 hooks 拼接（非覆盖）。"""

    def _write_two_layer_hooks(
        self, tmp_path: Path, user_extra: str, project_extra: str
    ):
        """Helper: write user config with hooks and project config with hooks."""
        user_cfg = tmp_path / "user.yaml"
        user_cfg.write_text(_PROVIDER_BLOCK + user_extra)
        proj_dir = tmp_path / "project"
        proj_dir.mkdir()
        proj_wentian = proj_dir / ".wentian"
        proj_wentian.mkdir()
        (proj_wentian / "config.yaml").write_text(project_extra)
        return user_cfg, proj_dir / ".wentian" / "config.yaml"

    def test_both_layers_hooks_are_concatenated(self, tmp_path):
        """用户级 1 条 + 项目级 1 条 → Config.hooks 共 2 条（拼接）。"""
        user_cfg, proj_cfg = self._write_two_layer_hooks(
            tmp_path, _HOOKS_BLOCK_A, _HOOKS_BLOCK_B
        )
        cfg = load_config(_user_path=user_cfg, _project_path=proj_cfg)
        assert len(cfg.hooks) == 2

    def test_user_hook_comes_first(self, tmp_path):
        """用户级规则在前，项目级规则在后（顺序：user first, then project）。"""
        from wentian.hooks.spec import HookEvent

        user_cfg, proj_cfg = self._write_two_layer_hooks(
            tmp_path, _HOOKS_BLOCK_A, _HOOKS_BLOCK_B
        )
        cfg = load_config(_user_path=user_cfg, _project_path=proj_cfg)
        # user hook: PostToolUse; project hook: SessionStart
        assert cfg.hooks[0].event == HookEvent.POST_TOOL_USE
        assert cfg.hooks[1].event == HookEvent.SESSION_START

    def test_hooks_are_not_replaced_by_project(self, tmp_path):
        """项目级 hooks 不会替换用户级 hooks——两者都在（非覆盖）。"""
        from wentian.hooks.spec import HookEvent

        user_cfg, proj_cfg = self._write_two_layer_hooks(
            tmp_path, _HOOKS_BLOCK_A, _HOOKS_BLOCK_B
        )
        cfg = load_config(_user_path=user_cfg, _project_path=proj_cfg)
        events = {r.event for r in cfg.hooks}
        # Both events present — not just the project one
        assert HookEvent.POST_TOOL_USE in events
        assert HookEvent.SESSION_START in events

    def test_only_user_hooks_no_project_hooks(self, tmp_path):
        """项目级无 hooks: → Config.hooks 仅含用户级规则。"""
        user_cfg, proj_cfg = self._write_two_layer_hooks(
            tmp_path,
            _HOOKS_BLOCK_A,
            "",  # project has no hooks:
        )
        cfg = load_config(_user_path=user_cfg, _project_path=proj_cfg)
        assert len(cfg.hooks) == 1

    def test_only_project_hooks_no_user_hooks(self, tmp_path):
        """用户级无 hooks: → Config.hooks 仅含项目级规则。"""
        user_cfg, proj_cfg = self._write_two_layer_hooks(
            tmp_path,
            "",  # user has no hooks:
            _HOOKS_BLOCK_B,
        )
        cfg = load_config(_user_path=user_cfg, _project_path=proj_cfg)
        assert len(cfg.hooks) == 1


class TestConfigHooksInvalidPropagatesError:
    """T123-RED-3: 非法 hooks: → load_config 传播 HookConfigError。"""

    def test_illegal_hook_in_single_file_raises_hook_config_error(self, tmp_path):
        """单文件含 PreToolUse+background → HookConfigError（启动失败）。"""
        from wentian.hooks.config import HookConfigError

        path = write_yaml(tmp_path, _PROVIDER_BLOCK + _HOOKS_BLOCK_ILLEGAL)
        with pytest.raises(HookConfigError):
            load_config(path=path)

    def test_illegal_hook_in_two_layer_user_raises_hook_config_error(self, tmp_path):
        """两层模式：用户级非法 hook → HookConfigError。"""
        from wentian.hooks.config import HookConfigError

        user_cfg = tmp_path / "user.yaml"
        user_cfg.write_text(_PROVIDER_BLOCK + _HOOKS_BLOCK_ILLEGAL)
        proj_cfg = tmp_path / "proj.yaml"
        proj_cfg.write_text("")  # empty project
        with pytest.raises(HookConfigError):
            load_config(_user_path=user_cfg, _project_path=proj_cfg)

    def test_illegal_hook_in_two_layer_project_raises_hook_config_error(self, tmp_path):
        """两层模式：项目级非法 hook → HookConfigError。"""
        from wentian.hooks.config import HookConfigError

        user_cfg = tmp_path / "user.yaml"
        user_cfg.write_text(_PROVIDER_BLOCK)
        proj_dir = tmp_path / "project"
        proj_dir.mkdir()
        proj_wentian = proj_dir / ".wentian"
        proj_wentian.mkdir()
        (proj_wentian / "config.yaml").write_text(_HOOKS_BLOCK_ILLEGAL)
        with pytest.raises(HookConfigError):
            load_config(
                _user_path=user_cfg,
                _project_path=proj_dir / ".wentian" / "config.yaml",
            )


# ---------------------------------------------------------------------------
# v0.13 · C115 · F100（任务 T140）AgentsConfig + 模型别名映射
# ---------------------------------------------------------------------------

_AGENTS_PROVIDER_BLOCK = """\
default: claude
providers:
  claude:
    protocol: anthropic
    model: claude-opus-4-8
    api_key: sk-ant-test
"""


class TestAgentsConfig:
    """v0.13 · C115 · F100 — agents: 块解析与 AgentsConfig 默认值验证。"""

    # ------------------------------------------------------------------
    # Import helper — lazily imported so RED phase is detected cleanly
    # ------------------------------------------------------------------

    @staticmethod
    def _import_agents_config():
        from wentian.config import AgentsConfig  # noqa: PLC0415

        return AgentsConfig

    # ------------------------------------------------------------------
    # Default values (no agents: block)
    # ------------------------------------------------------------------

    def test_no_agents_block_enabled_is_true(self, tmp_path):
        """缺少 agents: 块时 config.agents.enabled 默认为 True。"""
        path = write_yaml(tmp_path, _AGENTS_PROVIDER_BLOCK)
        cfg = load_config(path)
        assert cfg.agents.enabled is True

    def test_no_agents_block_returns_agents_config_instance(self, tmp_path):
        """缺少 agents: 块时 config.agents 是 AgentsConfig 实例。"""
        AgentsConfig = self._import_agents_config()
        path = write_yaml(tmp_path, _AGENTS_PROVIDER_BLOCK)
        cfg = load_config(path)
        assert isinstance(cfg.agents, AgentsConfig)

    def test_default_max_turns_is_20(self, tmp_path):
        """default_max_turns 默认值为 20。"""
        path = write_yaml(tmp_path, _AGENTS_PROVIDER_BLOCK)
        cfg = load_config(path)
        assert cfg.agents.default_max_turns == 20

    def test_foreground_timeout_s_default_greater_than_zero(self, tmp_path):
        """foreground_timeout_s 默认值大于 0。"""
        path = write_yaml(tmp_path, _AGENTS_PROVIDER_BLOCK)
        cfg = load_config(path)
        assert cfg.agents.foreground_timeout_s > 0

    def test_background_allow_default_non_empty(self, tmp_path):
        """background_allow 默认值包含至少一个只读工具名。"""
        path = write_yaml(tmp_path, _AGENTS_PROVIDER_BLOCK)
        cfg = load_config(path)
        assert len(cfg.agents.background_allow) >= 1

    # ------------------------------------------------------------------
    # model_aliases defaults
    # ------------------------------------------------------------------

    def test_model_aliases_default_contains_haiku(self, tmp_path):
        """默认 model_aliases 包含 haiku → claude-haiku-4-5。"""
        path = write_yaml(tmp_path, _AGENTS_PROVIDER_BLOCK)
        cfg = load_config(path)
        assert cfg.agents.model_aliases["haiku"] == "claude-haiku-4-5"

    def test_model_aliases_default_contains_sonnet(self, tmp_path):
        """默认 model_aliases 包含 sonnet → claude-sonnet-4-6。"""
        path = write_yaml(tmp_path, _AGENTS_PROVIDER_BLOCK)
        cfg = load_config(path)
        assert cfg.agents.model_aliases["sonnet"] == "claude-sonnet-4-6"

    def test_model_aliases_default_contains_opus(self, tmp_path):
        """默认 model_aliases 包含 opus → claude-opus-4-8。"""
        path = write_yaml(tmp_path, _AGENTS_PROVIDER_BLOCK)
        cfg = load_config(path)
        assert cfg.agents.model_aliases["opus"] == "claude-opus-4-8"

    def test_model_aliases_default_contains_inherit_sentinel(self, tmp_path):
        """默认 model_aliases 包含 inherit → __inherit__（主对话模型哨兵）。"""
        path = write_yaml(tmp_path, _AGENTS_PROVIDER_BLOCK)
        cfg = load_config(path)
        assert cfg.agents.model_aliases["inherit"] == "__inherit__"

    # ------------------------------------------------------------------
    # enabled: false
    # ------------------------------------------------------------------

    def test_enabled_false_when_set(self, tmp_path):
        """agents:\\n  enabled: false → config.agents.enabled is False。"""
        yaml_content = _AGENTS_PROVIDER_BLOCK + "agents:\n  enabled: false\n"
        path = write_yaml(tmp_path, yaml_content)
        cfg = load_config(path)
        assert cfg.agents.enabled is False

    # ------------------------------------------------------------------
    # default_max_turns override
    # ------------------------------------------------------------------

    def test_default_max_turns_override(self, tmp_path):
        """agents:\\n  default_max_turns: 50 → default_max_turns == 50。"""
        yaml_content = _AGENTS_PROVIDER_BLOCK + "agents:\n  default_max_turns: 50\n"
        path = write_yaml(tmp_path, yaml_content)
        cfg = load_config(path)
        assert cfg.agents.default_max_turns == 50

    # ------------------------------------------------------------------
    # foreground_timeout_s override
    # ------------------------------------------------------------------

    def test_foreground_timeout_s_override(self, tmp_path):
        """agents:\\n  foreground_timeout_s: 60.0 → foreground_timeout_s == 60.0。"""
        yaml_content = (
            _AGENTS_PROVIDER_BLOCK + "agents:\n  foreground_timeout_s: 60.0\n"
        )
        path = write_yaml(tmp_path, yaml_content)
        cfg = load_config(path)
        assert cfg.agents.foreground_timeout_s == 60.0

    # ------------------------------------------------------------------
    # model_aliases partial override — dict-merge semantics
    # ------------------------------------------------------------------

    def test_model_aliases_partial_override_haiku(self, tmp_path):
        """用户只覆盖 haiku；其余键保留默认值。"""
        yaml_content = (
            _AGENTS_PROVIDER_BLOCK
            + "agents:\n  model_aliases:\n    haiku: my-haiku-model\n"
        )
        path = write_yaml(tmp_path, yaml_content)
        cfg = load_config(path)
        assert cfg.agents.model_aliases["haiku"] == "my-haiku-model"
        # Other keys must retain their defaults
        assert cfg.agents.model_aliases["sonnet"] == "claude-sonnet-4-6"
        assert cfg.agents.model_aliases["opus"] == "claude-opus-4-8"
        assert cfg.agents.model_aliases["inherit"] == "__inherit__"

    def test_model_aliases_custom_key_added(self, tmp_path):
        """用户新增自定义别名键；默认键仍存在。"""
        yaml_content = (
            _AGENTS_PROVIDER_BLOCK
            + "agents:\n  model_aliases:\n    fast: claude-haiku-4-5\n"
        )
        path = write_yaml(tmp_path, yaml_content)
        cfg = load_config(path)
        assert cfg.agents.model_aliases["fast"] == "claude-haiku-4-5"
        # Default keys still present
        assert "haiku" in cfg.agents.model_aliases
        assert "sonnet" in cfg.agents.model_aliases

    # ------------------------------------------------------------------
    # background_allow override
    # ------------------------------------------------------------------

    def test_background_allow_override(self, tmp_path):
        """agents:\\n  background_allow: [read_file] → background_allow == ('read_file',)。"""
        yaml_content = (
            _AGENTS_PROVIDER_BLOCK + "agents:\n  background_allow:\n    - read_file\n"
        )
        path = write_yaml(tmp_path, yaml_content)
        cfg = load_config(path)
        assert "read_file" in cfg.agents.background_allow

    # ------------------------------------------------------------------
    # Missing / non-mapping block → defaults, no throw
    # ------------------------------------------------------------------

    def test_none_agents_block_returns_defaults_no_throw(self):
        """AgentsConfig() 默认构建不抛；enabled=True。"""
        AgentsConfig = self._import_agents_config()
        ac = AgentsConfig()
        assert ac.enabled is True

    def test_non_mapping_agents_block_returns_defaults(self, tmp_path):
        """agents: 值为非映射字符串 → 安全降级到默认 AgentsConfig，不抛异常。"""
        yaml_content = _AGENTS_PROVIDER_BLOCK + "agents: not-a-mapping\n"
        path = write_yaml(tmp_path, yaml_content)
        cfg = load_config(path)  # must not raise
        assert cfg.agents.enabled is True
        assert cfg.agents.default_max_turns == 20

    def test_agents_config_is_frozen(self):
        """AgentsConfig 是冻结 dataclass（frozen=True）。"""
        AgentsConfig = self._import_agents_config()
        ac = AgentsConfig()
        with pytest.raises((AttributeError, TypeError)):
            ac.enabled = False  # type: ignore[misc]
