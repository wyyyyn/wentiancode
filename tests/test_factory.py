"""Tests for create_provider factory (T4).

RED → GREEN cycle:
- factory dispatches on cfg.protocol
- returns correct concrete type
- unknown protocol raises ConfigError
"""

import pytest

from wentian.config import ConfigError, ProviderConfig
from wentian.providers.factory import create_provider
from wentian.providers.anthropic import AnthropicProvider
from wentian.providers.openai_compat import OpenAICompatProvider
from wentian.providers.base import Provider


def _make_cfg(protocol: str, name: str = "test") -> ProviderConfig:
    return ProviderConfig(
        name=name,
        protocol=protocol,  # type: ignore[arg-type]
        model="some-model",
        api_key="sk-test",
    )


class TestCreateProvider:
    def test_anthropic_protocol_returns_anthropic_provider(self):
        cfg = _make_cfg("anthropic", name="claude")
        provider = create_provider(cfg)
        assert isinstance(provider, AnthropicProvider)

    def test_openai_protocol_returns_openai_compat_provider(self):
        cfg = _make_cfg("openai", name="deepseek")
        provider = create_provider(cfg)
        assert isinstance(provider, OpenAICompatProvider)

    def test_returned_provider_is_provider_subclass(self):
        cfg = _make_cfg("anthropic")
        provider = create_provider(cfg)
        assert isinstance(provider, Provider)

    def test_provider_name_matches_cfg_name(self):
        cfg = _make_cfg("anthropic", name="my-claude")
        provider = create_provider(cfg)
        assert provider.name == "my-claude"

    def test_openai_provider_name_matches_cfg_name(self):
        cfg = _make_cfg("openai", name="my-deepseek")
        provider = create_provider(cfg)
        assert provider.name == "my-deepseek"

    def test_unknown_protocol_raises_config_error(self):
        cfg = _make_cfg("anthropic")
        cfg_bad = ProviderConfig(
            name="bad",
            protocol="anthropic",  # type: ignore[arg-type]
            model="x",
            api_key="sk-x",
        )
        # Monkey-patch protocol to an unregistered value
        object.__setattr__(cfg_bad, "protocol", "unknown_proto") if hasattr(
            cfg_bad, "__slots__"
        ) else setattr(cfg_bad, "protocol", "unknown_proto")
        with pytest.raises(ConfigError, match="unknown_proto"):
            create_provider(cfg_bad)

    def test_stream_raises_not_implemented_for_anthropic(self):
        cfg = _make_cfg("anthropic")
        provider = create_provider(cfg)
        with pytest.raises(NotImplementedError):
            list(provider.stream([{"role": "user", "content": "hi"}]))

    def test_stream_raises_not_implemented_for_openai(self):
        cfg = _make_cfg("openai")
        provider = create_provider(cfg)
        with pytest.raises(NotImplementedError):
            list(provider.stream([{"role": "user", "content": "hi"}]))
