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
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Literal, Union

import yaml

# v0.12 · C99 · F77 (task T123) — unidirectional config→hooks.config import (hooks do not import config, no cycles)
from wentian.hooks.config import HookConfigError, parse_hooks  # noqa: F401
from wentian.hooks.spec import HookRule

__all__ = [
    "ProviderConfig",
    "Config",
    "ConfigError",
    "load_config",
    # v0.7 · C44 · F50/N23 (task T82)
    "StdioServerConfig",
    "HttpServerConfig",
    "MCPServerConfig",
    # v0.8 · C51 · F56/F58 (task T91)
    "ContextConfig",
    # v0.9 · C59 · F69 (task T106)
    "MemoryConfig",
    "SessionsConfig",
    # v0.11 · C107a · F70 (task T132)
    "SkillsConfig",
    # v0.12 · C99 · F77 (task T123)
    "HookConfigError",
    "HookRule",
    # v0.13 · C115 · F100 (task T140)
    "AgentsConfig",
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
    # v0.8 · C51 · F56/F58 (task T91) — this backend's context window; None → ContextConfig.default_window
    context_window: int | None = None


# v0.7 · C44 · F50/N23 (task T82) — MCP Server config data structures


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


# v0.8 · C51 · F56/F58 (task T91) — context management / two-layer compaction config
#
# Optional top-level ``context:`` block. Entire block absent → ``ContextConfig()`` (all defaults);
# per-field absent → that field uses its default. Window semantics: window = input budget + output,
# so the second-layer heavy-summary trigger threshold = ``context_window - reserved_output - margin``
# (auto-trigger uses ``auto_margin``, ``/compact`` manual trigger uses ``manual_margin``).
# All fields have defaults; absent block/fields always degrade safely, never raise exceptions.


@dataclass(frozen=True)
class ContextConfig:
    """Context-management / two-layer compaction knobs (all optional, defaulted)."""

    default_window: int = 200_000  # fallback window when provider has no context_window configured
    reserved_output: int = 64_000  # output reservation (window = input budget + output)
    auto_margin: int = 13_000  # auto-trigger safety margin
    manual_margin: int = 3_000  # /compact manual-trigger margin
    recent_keep_tokens: int = 10_000  # target tokens to preserve verbatim at the tail
    recent_keep_min_messages: int = 5  # minimum number of messages to preserve at the tail
    offload_single_tokens: int = 2_000  # single tool-result offload threshold (first layer)
    offload_round_sum_tokens: int = 8_000  # per-round tool-result total offload threshold (first layer)
    char_per_token: float = 3.5  # incremental character-to-token ratio


# v0.9 · C59 · F69 (task T106) — auto-memory + session config
#
# Optional top-level ``memory:`` / ``sessions:`` blocks. Entire block absent → respective ``()`` all defaults;
# per-field absent → that field uses its default. Follows v0.7 two-layer deep-merge (per-key);
# absent blocks/fields always degrade safely, never raise ConfigError.
#
# De-duplication: :class:`MemoryConfig` here is the **single authority** — ``memory/store.py``
# and ``memory/runner.py`` import from here (a type import of memory→config, analogous to the
# context→providers.base leaf-contract import), rather than each defining their own copy.


@dataclass(frozen=True)
class MemoryConfig:
    """Auto-memory knobs (all optional, defaulted) — the F69 ``memory:`` block.

    Authoritative single definition: the ``memory`` package imports this class
    rather than defining its own, so there is exactly one ``MemoryConfig`` in the
    codebase.
    """

    enabled: bool = True
    provider: str | None = None
    max_index_lines: int = 200
    max_index_bytes: int = 25600


@dataclass(frozen=True)
class SessionsConfig:
    """Session lifecycle knobs (all optional, defaulted) — the F69 ``sessions:`` block."""

    retention_days: int = 30
    resume_gap_reminder_hours: int = 4


# v0.11 · C107a · F70 (task T132) — Skill system config
#
# Optional top-level ``skills:`` block. Entire block absent or non-mapping → ``SkillsConfig()`` (all defaults,
# ``enabled=True``); per-field absent → that field uses its default. Follows :func:`_parse_block` per-key
# safe degradation, never raises ConfigError (isomorphic to MemoryConfig/SessionsConfig).


@dataclass(frozen=True)
class SkillsConfig:
    """Skill-system knobs (all optional, defaulted) — the F70 ``skills:`` block."""

    enabled: bool = True


# v0.13 · C115 · F100 (task T140) — sub-agent system config
#
# Optional top-level ``agents:`` block. Entire block absent or non-mapping → ``AgentsConfig()`` (all defaults);
# per-field absent → that field uses its default. ``model_aliases`` uses dict-merge semantics (when the user
# specifies only some keys, unspecified keys retain their default values). ``background_allow`` lists
# read-only tool names (``name`` attribute, not ``friendly_name``) allowed to run silently in the background.
# The ``inherit`` alias maps to the sentinel ``"__inherit__"``; the runner layer resolves it to the main
# conversation's model.


def _default_model_aliases() -> dict[str, str]:
    """Return the default model-alias mapping for v0.13 AgentsConfig."""
    return {
        "haiku": "claude-haiku-4-5",
        "sonnet": "claude-sonnet-4-6",
        "opus": "claude-opus-4-8",
        "inherit": "__inherit__",
    }


@dataclass(frozen=True)
class AgentsConfig:
    """Sub-agent system knobs (all optional, defaulted) — the F100 ``agents:`` block.

    v0.13 · C115 · F100 (task T140)

    ``model_aliases`` maps short alias names to full model IDs.  The sentinel
    ``"__inherit__"`` (key ``"inherit"``) signals the runner to reuse the main
    conversation's model rather than selecting a new one.

    ``background_allow`` contains tool ``name`` values (not ``friendly_name``)
    that may run silently in background sub-agents without user confirmation.
    """

    enabled: bool = True
    model_aliases: dict[str, str] = field(default_factory=_default_model_aliases)
    default_max_turns: int = 20
    foreground_timeout_s: float = 30.0
    background_allow: tuple[str, ...] = ("read_file", "find_files", "search_text")


@dataclass
class Config:
    """Top-level application configuration."""

    providers: dict[str, ProviderConfig]
    default: str  # must be a key in providers
    # v0.7 · C44 · F50/N23 (task T82)
    mcp_servers: dict[str, MCPServerConfig] = field(default_factory=dict)
    # v0.8 · C51 · F56/F58 (task T91) — entire block absent → ContextConfig() (all defaults)
    context: ContextConfig = field(default_factory=ContextConfig)
    # v0.9 · C59 · F69 (task T106) — entire block absent → respective all defaults
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    sessions: SessionsConfig = field(default_factory=SessionsConfig)
    # v0.11 · C107a · F70 (task T132) — entire block absent → SkillsConfig() (all defaults)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    # v0.12 · C99 · F77 (task T123) — two-layer accumulation (concatenation not replacement); absent block → []
    hooks: list[HookRule] = field(default_factory=list)
    # v0.13 · C115 · F100 (task T140) — entire block absent → AgentsConfig() (all defaults)
    agents: AgentsConfig = field(default_factory=AgentsConfig)

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


# v0.7 · C44 · F50/N23 (task T82) — helper functions


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


# v0.8 · C51 · F56/F58 (task T91) — context: block per-field parsing (absent fields use defaults)


def _parse_context(raw_context: object) -> ContextConfig:
    """Parse the top-level ``context:`` block into :class:`ContextConfig`.

    Entire block absent or non-mapping → ``ContextConfig()`` (all defaults); per-field absent → that field uses its default.
    Degrades safely, never raises exceptions.
    """
    if not isinstance(raw_context, dict) or not raw_context:
        return ContextConfig()
    defaults = ContextConfig()
    return ContextConfig(
        **{
            f.name: raw_context.get(f.name, getattr(defaults, f.name))
            for f in fields(defaults)
        }
    )


# v0.9 · C59 · F69 (task T106) — memory:/sessions: block per-field parsing (absent fields use defaults)


def _parse_block(raw_block: object, dataclass_default):
    """Parse an optional top-level block into *dataclass_default*'s type.

    Entire block absent or non-mapping → fully-defaulted instance; per-field absent → that field uses its default. Degrades safely,
    never raises exceptions (isomorphic to :func:`_parse_context`).
    """
    cls = type(dataclass_default)
    if not isinstance(raw_block, dict) or not raw_block:
        return cls()
    return cls(
        **{
            f.name: raw_block.get(f.name, getattr(dataclass_default, f.name))
            for f in fields(dataclass_default)
        }
    )


def _parse_memory(raw_memory: object) -> MemoryConfig:
    """Parse the top-level ``memory:`` block into :class:`MemoryConfig`."""
    return _parse_block(raw_memory, MemoryConfig())


def _parse_sessions(raw_sessions: object) -> SessionsConfig:
    """Parse the top-level ``sessions:`` block into :class:`SessionsConfig`."""
    return _parse_block(raw_sessions, SessionsConfig())


# v0.11 · C107a · F70 (task T132) — skills: block per-field parsing (absent fields use defaults)


def _parse_skills(raw_skills: object) -> SkillsConfig:
    """Parse the top-level ``skills:`` block into :class:`SkillsConfig`."""
    return _parse_block(raw_skills, SkillsConfig())


# v0.13 · C115 · F100 (task T140) — agents: block parsing (absent fields use defaults; model_aliases dict-merge)


def _parse_agents(raw_agents: object) -> AgentsConfig:
    """Parse the top-level ``agents:`` block into :class:`AgentsConfig`.

    v0.13 · C115 · F100 (task T140)

    Entire block absent or non-mapping → ``AgentsConfig()`` (all defaults); per-field absent → that field uses its default.
    Degrades safely, never raises exceptions (isomorphic to :func:`_parse_skills`).

    ``model_aliases`` uses dict-merge semantics: starts from the default dict as a base, then overlays
    user-specified keys. Unspecified keys retain their default values (as opposed to :func:`_parse_block`'s
    whole-field replacement).
    """
    if not isinstance(raw_agents, dict) or not raw_agents:
        return AgentsConfig()

    defaults = AgentsConfig()

    # --- scalar fields: type-coerce with fallback to default on bad value ---
    enabled = _coerce_bool(raw_agents.get("enabled"), defaults.enabled)
    default_max_turns = _coerce_int(
        raw_agents.get("default_max_turns"), defaults.default_max_turns
    )
    foreground_timeout_s = _coerce_float(
        raw_agents.get("foreground_timeout_s"), defaults.foreground_timeout_s
    )
    background_allow = _coerce_str_tuple(
        raw_agents.get("background_allow"), defaults.background_allow
    )

    # --- model_aliases: dict-merge (user overlay onto defaults) ---
    model_aliases = dict(defaults.model_aliases)  # start from default copy
    raw_aliases = raw_agents.get("model_aliases")
    if isinstance(raw_aliases, dict):
        for k, v in raw_aliases.items():
            if isinstance(k, str) and isinstance(v, str):
                model_aliases[k] = v

    return AgentsConfig(
        enabled=enabled,
        model_aliases=model_aliases,
        default_max_turns=default_max_turns,
        foreground_timeout_s=foreground_timeout_s,
        background_allow=background_allow,
    )


def _coerce_bool(value: object, default: bool) -> bool:
    """Coerce *value* to bool; return *default* if type is unexpected."""
    if isinstance(value, bool):
        return value
    return default


def _coerce_int(value: object, default: int) -> int:
    """Coerce *value* to int; return *default* if type is unexpected."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return default


def _coerce_float(value: object, default: float) -> float:
    """Coerce *value* to float; return *default* if type is unexpected."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


def _coerce_str_tuple(value: object, default: tuple[str, ...]) -> tuple[str, ...]:
    """Coerce *value* (list/tuple of str) to tuple[str,...]; return *default* on bad value."""
    if isinstance(value, (list, tuple)):
        coerced = tuple(str(v) for v in value)
        return coerced
    return default


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
            # v0.8 · C51 · F56/F58 (task T91) — defaults to None
            context_window=pdata.get("context_window"),
        )

    mcp_servers: dict[str, MCPServerConfig] = {}
    raw_mcp = raw.get("mcpServers") or {}
    if raw_mcp:
        mcp_servers = _parse_mcp_servers(raw_mcp)

    # v0.8 · C51 · F56/F58 (task T91)
    context = _parse_context(raw.get("context"))

    # v0.9 · C59 · F69 (task T106)
    memory = _parse_memory(raw.get("memory"))
    sessions = _parse_sessions(raw.get("sessions"))

    # v0.11 · C107a · F70 (task T132)
    skills = _parse_skills(raw.get("skills"))
    # v0.12 · C99 · F77 (task T123) — single-file mode calls parse_hooks directly; HookConfigError propagates unchanged
    hooks = parse_hooks(raw.get("hooks"))
    # v0.13 · C115 · F100 (task T140)
    agents = _parse_agents(raw.get("agents"))

    return Config(
        providers=providers,
        default=raw["default"],
        mcp_servers=mcp_servers,
        context=context,
        memory=memory,
        sessions=sessions,
        skills=skills,
        hooks=hooks,
        agents=agents,
    )


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


def _concat_layer_hooks(
    user_raw: object,
    project_raw: object,
) -> list[HookRule]:
    """Parse and concatenate hooks from two config layers.

    v0.12 · C99 · F77 (task T123)

    ``_deep_merge`` performs list replacement (project wins), which is correct
    for providers but wrong for hooks — rules must *accumulate*.  This helper
    bypasses the merge for the ``hooks`` key and instead parses each layer
    independently, then concatenates user-first / project-second.

    Parameters
    ----------
    user_raw:
        Raw ``hooks`` value from the user-level config (``None`` or ``list``).
    project_raw:
        Raw ``hooks`` value from the project-level config (``None`` or
        ``list``; also ``None`` when no project file was found).

    Returns
    -------
    list[HookRule]
        User rules followed by project rules.  Either half may be empty.

    Raises
    ------
    HookConfigError
        Propagated unchanged from :func:`parse_hooks` if any rule is invalid.
    """
    return parse_hooks(user_raw) + parse_hooks(project_raw)


def load_config(
    path: Path | str | None = None,
    *,
    # v0.7 · C44 · F50/N23 (task T82) — keyword arguments for test injection only
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
    # v0.7 · C44 · F50/N23 (task T82)                                    #
    # ------------------------------------------------------------------ #
    user_path = _user_path if _user_path is not None else _default_config_path()
    project_path = (
        _project_path
        if _project_path is not None
        else Path.cwd() / ".wentian" / "config.yaml"
    )

    # User-level is required (mirrors old behaviour when no explicit path given)
    raw = _load_yaml(user_path)

    # v0.12 · C99 · F77 (task T123) — two-layer hooks concatenation: capture raw hooks lists from each
    # layer before deep_merge. _deep_merge performs list replacement (project overrides user), which is correct
    # for providers but semantically wrong for hooks (rules should accumulate). Solution: bypass _deep_merge
    # for the hooks key, parse each layer independently, concatenate (user-first, project-appended),
    # then overwrite cfg.hooks.
    user_raw_hooks = raw.get("hooks")  # may be None/list

    # Project-level is optional — silently skip when absent
    proj_raw_hooks = None
    if project_path.exists():
        proj_raw = yaml.safe_load(project_path.read_text(encoding="utf-8")) or {}
        proj_raw_hooks = proj_raw.get("hooks")  # may be None/list
        # Deep-merge providers and mcpServers; scalar 'default' wins if present
        raw = _deep_merge(raw, proj_raw)

    _validate(raw)
    cfg = _build_config_from_raw(raw)

    # v0.12 · C99 · F77 (task T123) — overwrite hooks: user-level + project-level concatenation (not replacement).
    # _build_config_from_raw already parsed the hooks key from the merged raw dict, but _deep_merge
    # performs list replacement, causing user-level rules to be lost. Here we re-parse from each original
    # layer and concatenate. HookConfigError from parse_hooks propagates unchanged (startup failure,
    # per spec N41/AC95).
    cfg.hooks = _concat_layer_hooks(user_raw_hooks, proj_raw_hooks)
    return cfg
