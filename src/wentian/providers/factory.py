"""Provider factory.

Dispatch on ProviderConfig.protocol to instantiate the correct Provider subclass.
Adding a new protocol requires a single registry entry (requirement F11).
"""

from __future__ import annotations

from wentian.config import ConfigError, ProviderConfig
from wentian.providers.anthropic import AnthropicProvider
from wentian.providers.base import Provider
from wentian.providers.openai_compat import OpenAICompatProvider

__all__ = ["create_provider"]

# Registry: protocol name → Provider subclass
_REGISTRY: dict[str, type[Provider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAICompatProvider,
}


def create_provider(cfg: ProviderConfig) -> Provider:
    """Instantiate and return the Provider for *cfg*.

    Parameters
    ----------
    cfg:
        A validated :class:`~wentian.config.ProviderConfig`.

    Returns
    -------
    Provider
        A concrete provider instance whose ``name`` matches ``cfg.name``.

    Raises
    ------
    ConfigError
        If ``cfg.protocol`` is not registered.
    """
    cls = _REGISTRY.get(cfg.protocol)
    if cls is None:
        raise ConfigError(
            f"Unknown protocol '{cfg.protocol}'. "
            f"Registered protocols: {sorted(_REGISTRY)}"
        )
    return cls(cfg)
