"""Configuration loading and validation for wentian.

YAML shape example::

    default: claude
    providers:
      claude:
        protocol: anthropic
        model: claude-opus-4-8
        api_key: sk-ant-...
        thinking: true
      deepseek:
        protocol: openai
        model: deepseek-chat
        base_url: https://api.deepseek.com
        api_key: sk-...
    mcpServers:
      filesystem:
        command: npx
        args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
        env:
          TOKEN: ${FS_TOKEN}
      remote:
        url: https://example.com/mcp
        headers:
          Authorization: Bearer ${API_KEY}

``load_config(path)`` parses the file and returns a validated :class:`Config`.
Raises :class:`ConfigError` for any structural or semantic problem.
"""

from __future__ import annotations

import os
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Union

import yaml

__all__ = [
    "ProviderConfig",
    "Config",
    "ConfigError",
    "load_config",
    # v0.7 · C44 · F50/N23（任务 T82）
    "StdioServerConfig",
    "HttpServerConfig",
    "MCPServerConfig",
]

VALID_PROTOCOLS = frozenset({"anthropic", "openai"})


class ConfigError(Exception):
    """Raised for any configuration loading or validation failure."""


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class ProviderConfig:
    """Configuration for a single LLM provider."""

    name: str
    protocol: Literal["anthropic", "openai"]
    model: str
    api_key: str
    base_url: str | None = None  # anthropic official endpoint may be omitted
    thinking: bool = False  # extended thinking; only meaningful for anthropic


# v0.7 · C44 · F50/N23（任务 T82）—— MCP Server 配置数据结构


@dataclass(frozen=True)
class StdioServerConfig:
    """Configuration for a stdio-based MCP server."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class HttpServerConfig:
    """Configuration for an HTTP-based MCP server."""

    name: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)


#: Union type alias for any MCP server config variant.
MCPServerConfig = Union[StdioServerConfig, HttpServerConfig]


@dataclass
class Config:
    """Top-level application configuration."""

    providers: dict[str, ProviderConfig]
    default: str  # must be a key in providers
    # v0.7 · C44 · F50/N23（任务 T82）
    mcp_servers: dict[str, MCPServerConfig] = field(default_factory=dict)

    def get(self, name: str | None = None) -> ProviderConfig:
        """Return a provider by name, or the default provider when *name* is None.

        Raises :class:`ConfigError` if *name* is not found in providers.
        """
        key = name if name is not None else self.default
        try:
            return self.providers[key]
        except KeyError:
            raise ConfigError(
                f"Provider '{key}' not found in config. "
                f"Available providers: {list(self.providers)}"
            ) from None


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _default_config_path() -> Path:
    """Return the XDG-aware default config file path."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "wentian" / "config.yaml"


def _validate(data: object) -> None:
    """Validate the raw YAML structure; raise ConfigError with a field-level message."""
    if not isinstance(data, dict):
        raise ConfigError(
            f"Config file must be a YAML mapping, got {type(data).__name__}"
        )

    # --- providers block ---
    if "providers" not in data:
        raise ConfigError("Config is missing required key 'providers'")

    providers_raw = data["providers"]
    if not isinstance(providers_raw, dict) or not providers_raw:
        raise ConfigError("'providers' must be a non-empty mapping")

    for pname, pdata in providers_raw.items():
        if not isinstance(pdata, dict):
            raise ConfigError(
                f"Provider '{pname}': expected a mapping, got {type(pdata).__name__}"
            )

        # required: protocol
        protocol = pdata.get("protocol")
        if not protocol:
            raise ConfigError(f"Provider '{pname}': missing required field 'protocol'")
        if protocol not in VALID_PROTOCOLS:
            raise ConfigError(
                f"Provider '{pname}': invalid protocol '{protocol}'. "
                f"Valid values: {sorted(VALID_PROTOCOLS)}"
            )

        # required: model
        if not pdata.get("model"):
            raise ConfigError(f"Provider '{pname}': missing required field 'model'")

        # required: api_key
        if not pdata.get("api_key"):
            raise ConfigError(f"Provider '{pname}': missing required field 'api_key'")

    # --- default ---
    if "default" not in data:
        raise ConfigError("Config is missing required key 'default'")

    default = data["default"]
    if default not in providers_raw:
        raise ConfigError(
            f"'default' refers to provider '{default}' which is not defined in 'providers'. "
            f"Available providers: {list(providers_raw)}"
        )


# v0.7 · C44 · F50/N23（任务 T82）—— 辅助函数


_VAR_RE = re.compile(r"\$\{(\w+)\}")


def _expand_vars(s: str) -> str:
    """Expand ``${VAR}`` placeholders using :data:`os.environ`.

    Missing variables expand to an empty string and emit a :mod:`warnings` warning.
    """

    def _replace(m: re.Match) -> str:
        var = m.group(1)
        val = os.environ.get(var)
        if val is None:
            warnings.warn(
                f"Config: environment variable '{var}' is not set; expanding to empty string.",
                stacklevel=4,
            )
            return ""
        return val

    return _VAR_RE.sub(_replace, s)


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge *override* into *base* with per-key deep merge for nested dicts.

    For each key in *override*:
    - If both values are dicts, recursively merge.
    - Otherwise the override value wins outright.
    Keys only in *base* are preserved unchanged.
    """
    result = dict(base)
    for key, oval in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(oval, dict):
            result[key] = _deep_merge(result[key], oval)
        else:
            result[key] = oval
    return result


def _parse_mcp_servers(raw_mcp: dict) -> dict[str, MCPServerConfig]:
    """Parse the ``mcpServers`` block from raw YAML into typed config objects."""
    servers: dict[str, MCPServerConfig] = {}
    for sname, sdata in raw_mcp.items():
        if not isinstance(sdata, dict):
            raise ConfigError(
                f"mcpServers.{sname}: expected a mapping, got {type(sdata).__name__}"
            )
        if "command" in sdata:
            raw_env = dict(sdata.get("env") or {})
            expanded_env = {k: _expand_vars(str(v)) for k, v in raw_env.items()}
            servers[sname] = StdioServerConfig(
                name=sname,
                command=sdata["command"],
                args=list(sdata.get("args") or []),
                env=expanded_env,
            )
        elif "url" in sdata:
            raw_headers = dict(sdata.get("headers") or {})
            expanded_headers = {k: _expand_vars(str(v)) for k, v in raw_headers.items()}
            servers[sname] = HttpServerConfig(
                name=sname,
                url=sdata["url"],
                headers=expanded_headers,
            )
        else:
            raise ConfigError(
                f"mcpServers.{sname}: missing required field 'command' (stdio) or 'url' (http)"
            )
    return servers


def _build_config_from_raw(raw: dict) -> Config:
    """Build a :class:`Config` from a validated merged raw-YAML dict."""
    providers: dict[str, ProviderConfig] = {}
    for pname, pdata in raw["providers"].items():
        providers[pname] = ProviderConfig(
            name=pname,
            protocol=pdata["protocol"],
            model=pdata["model"],
            api_key=pdata["api_key"],
            base_url=pdata.get("base_url"),
            thinking=bool(pdata.get("thinking", False)),
        )

    mcp_servers: dict[str, MCPServerConfig] = {}
    raw_mcp = raw.get("mcpServers") or {}
    if raw_mcp:
        mcp_servers = _parse_mcp_servers(raw_mcp)

    return Config(providers=providers, default=raw["default"], mcp_servers=mcp_servers)


def _load_yaml(path: Path) -> dict:
    """Read and parse a YAML file; raise :class:`ConfigError` on any problem."""
    if not path.exists():
        raise ConfigError(
            f"Config file not found: {path}\n"
            "Create it or pass an explicit path to load_config()."
        )
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse YAML from {path}: {exc}") from exc


def load_config(
    path: Path | str | None = None,
    *,
    # v0.7 · C44 · F50/N23（任务 T82）—— 仅供测试注入的关键字参数
    _user_path: Path | None = None,
    _project_path: Path | None = None,
) -> Config:
    """Load and validate a wentian config file.

    Parameters
    ----------
    path:
        Explicit path to a YAML config file.  When provided, the file is loaded
        directly (single-file mode) — two-layer merging is **not** triggered.
        This preserves the pre-v0.7 behaviour and is used by ``-c`` / tests that
        supply a self-contained fixture file.
    _user_path:
        Override the user-level config path (normally resolved via XDG).
        **Internal / test-only** — not part of the public API.
    _project_path:
        Override the project-level config path (normally ``<cwd>/.wentian/config.yaml``).
        **Internal / test-only** — not part of the public API.

    Returns
    -------
    Config
        Fully validated configuration object.

    Raises
    ------
    ConfigError
        If a required file is missing, malformed, or fails validation.
    """
    # ------------------------------------------------------------------ #
    # Single-file mode: explicit ``path`` bypasses two-layer merging       #
    # ------------------------------------------------------------------ #
    if path is not None:
        resolved = Path(path)
        raw = _load_yaml(resolved)
        _validate(raw)
        return _build_config_from_raw(raw)

    # ------------------------------------------------------------------ #
    # Two-layer mode: user-level + project-level deep merge               #
    # v0.7 · C44 · F50/N23（任务 T82）                                    #
    # ------------------------------------------------------------------ #
    user_path = _user_path if _user_path is not None else _default_config_path()
    project_path = (
        _project_path
        if _project_path is not None
        else Path.cwd() / ".wentian" / "config.yaml"
    )

    # User-level is required (mirrors old behaviour when no explicit path given)
    raw = _load_yaml(user_path)

    # Project-level is optional — silently skip when absent
    if project_path.exists():
        proj_raw = yaml.safe_load(project_path.read_text(encoding="utf-8")) or {}
        # Deep-merge providers and mcpServers; scalar 'default' wins if present
        raw = _deep_merge(raw, proj_raw)

    _validate(raw)
    return _build_config_from_raw(raw)
