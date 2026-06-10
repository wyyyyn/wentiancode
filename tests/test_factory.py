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
        cfg_bad = ProviderConfig(
            name="bad",
            protocol="anthropic",  # type: ignore[arg-type]
            model="x",
            api_key="sk-x",
        )
        # Monkey-patch protocol to an unregistered value
        setattr(cfg_bad, "protocol", "unknown_proto")
        with pytest.raises(ConfigError, match="unknown_proto"):
            create_provider(cfg_bad)

    def test_stream_is_implemented_for_anthropic(self):
        # AnthropicProvider.stream() is now fully implemented (T11).
        # Verify it no longer raises NotImplementedError — it should attempt
        # an API call (which we catch as a non-NotImplementedError exception
        # when using a dummy key, or succeed if mocked).
        cfg = _make_cfg("anthropic")
        provider = create_provider(cfg)
        with pytest.raises(Exception) as exc_info:
            list(provider.stream([{"role": "user", "content": "hi"}]))
        assert not isinstance(exc_info.value, NotImplementedError), (
            "AnthropicProvider.stream() must be implemented (T11)"
        )

    def test_stream_is_implemented_for_openai(self):
        # OpenAICompatProvider.stream() is now fully implemented (T12).
        # Verify it no longer raises NotImplementedError — it should attempt
        # an API call (which we catch as a non-NotImplementedError exception
        # when using a dummy key, or succeed if mocked).
        cfg = _make_cfg("openai")
        provider = create_provider(cfg)
        with pytest.raises(Exception) as exc_info:
            list(provider.stream([{"role": "user", "content": "hi"}]))
        assert not isinstance(exc_info.value, NotImplementedError), (
            "OpenAICompatProvider.stream() must be implemented (T12)"
        )
