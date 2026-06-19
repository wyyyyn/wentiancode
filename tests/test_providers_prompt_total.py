"""Tests for Provider.prompt_token_total: real prompt token total.

v0.8 · C47 · F56（任务 T90）

Two backends report input_tokens with different semantics:
- Anthropic: input_tokens excludes cache read/write → real total = input +
  cache_read + cache_creation.
- OpenAI-compatible: prompt_tokens already INCLUDES cached_tokens → must not be
  re-added; the base default (return input_tokens) is therefore correct.
"""

from collections.abc import Iterator

from wentian.providers.anthropic import AnthropicProvider
from wentian.providers.base import Done, Message, Provider, StreamEvent, ToolSpec, Usage


class _MinimalProvider(Provider):
    """Concrete Provider that adds no override — exercises the base default."""

    name = "minimal"

    def stream(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
    ) -> Iterator[StreamEvent]:
        yield Done()


# --- base / OpenAI-compatible default: input_tokens already includes cache ---


def test_base_default_returns_input_tokens_only():
    """base default: prompt_token_total == input_tokens (cache already folded in)."""
    p = _MinimalProvider()
    usage = Usage(input_tokens=1000, output_tokens=10, cache_read_input_tokens=900)
    assert p.prompt_token_total(usage) == 1000


def test_base_default_ignores_cache_creation_too():
    """base default does not add cache_creation either (OpenAI has no such concept)."""
    p = _MinimalProvider()
    usage = Usage(
        input_tokens=500,
        output_tokens=10,
        cache_creation_input_tokens=42,
        cache_read_input_tokens=100,
    )
    assert p.prompt_token_total(usage) == 500


# --- AnthropicProvider override: input excludes cache, so add both ---


def _anthropic_provider() -> AnthropicProvider:
    from wentian.config import ProviderConfig

    cfg = ProviderConfig(
        name="anthropic",
        protocol="anthropic",
        model="claude-x",
        api_key="sk-test",
    )
    return AnthropicProvider(cfg)


def test_anthropic_adds_cache_read_and_creation():
    """Anthropic: input + cache_read + cache_creation == real prompt total."""
    p = _anthropic_provider()
    usage = Usage(
        input_tokens=100,
        output_tokens=10,
        cache_creation_input_tokens=50,
        cache_read_input_tokens=900,
    )
    assert p.prompt_token_total(usage) == 1050


def test_anthropic_no_cache_equals_input():
    """With zero cache fields, Anthropic total degrades to input_tokens."""
    p = _anthropic_provider()
    usage = Usage(input_tokens=300, output_tokens=10)
    assert p.prompt_token_total(usage) == 300
