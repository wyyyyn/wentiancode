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

``load_config(path)`` parses the file and returns a validated :class:`Config`.
Raises :class:`ConfigError` for any structural or semantic problem.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

__all__ = ["ProviderConfig", "Config", "ConfigError", "load_config"]

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


@dataclass
class Config:
    """Top-level application configuration."""

    providers: dict[str, ProviderConfig]
    default: str  # must be a key in providers

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


def load_config(path: Path | str | None = None) -> Config:
    """Load and validate a wentian config file.

    Parameters
    ----------
    path:
        Path to the YAML config file.  When *None* the XDG default location
        ``~/.config/wentian/config.yaml`` (or ``$XDG_CONFIG_HOME/wentian/config.yaml``)
        is used.

    Returns
    -------
    Config
        Fully validated configuration object.

    Raises
    ------
    ConfigError
        If the file is missing, malformed, or fails validation.
    """
    resolved = Path(path) if path is not None else _default_config_path()

    if not resolved.exists():
        raise ConfigError(
            f"Config file not found: {resolved}\n"
            "Create it or pass an explicit path to load_config()."
        )

    try:
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse YAML from {resolved}: {exc}") from exc

    _validate(raw)

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

    return Config(providers=providers, default=raw["default"])
