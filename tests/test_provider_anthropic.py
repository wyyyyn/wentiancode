"""Tests for AnthropicProvider (T11).

Mock strategy: patch `anthropic.Anthropic` so no real network calls are made.
Fake stream context manager yields SimpleNamespace event objects.
"""

from __future__ import annotations

import types
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from wentian.config import ProviderConfig
from wentian.providers.anthropic import AnthropicProvider
from wentian.providers.base import Done, TextDelta, ThinkingDelta, Usage


# ---------------------------------------------------------------------------
# Helpers for building fake SDK objects
# ---------------------------------------------------------------------------

def _make_delta(delta_type: str, **kwargs):
    """Build a fake content_block_delta event."""
    delta = types.SimpleNamespace(type=delta_type, **kwargs)
    return types.SimpleNamespace(type="content_block_delta", delta=delta)


def _make_stream_cm(events: list, input_tokens: int = 10, output_tokens: int = 20):
    """Return a fake context-manager stream that yields *events* and has get_final_message()."""
    usage = types.SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
    final_message = types.SimpleNamespace(usage=usage)

    class _FakeStream:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def __iter__(self) -> Iterator:
            yield from events

        def get_final_message(self):
            return final_message

    return _FakeStream()


def _make_cfg(
    *,
    model: str = "claude-opus-4-8",
    thinking: bool = False,
    base_url: str | None = None,
    api_key: str = "sk-ant-test",
) -> ProviderConfig:
    return ProviderConfig(
        name="claude",
        protocol="anthropic",
        model=model,
        api_key=api_key,
        base_url=base_url,
        thinking=thinking,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def captured_kwargs():
    """Dictionary to store kwargs captured by the fake messages.stream()."""
    return {}


@pytest.fixture
def mock_anthropic_client(captured_kwargs):
    """Patch anthropic.Anthropic; return (mock_class, fake_stream).

    The fake stream has a thinking_delta and text_delta event.
    """
    events = [
        _make_delta("thinking_delta", thinking="想"),
        _make_delta("text_delta", text="答"),
    ]
    fake_stream = _make_stream_cm(events, input_tokens=5, output_tokens=15)

    mock_stream_method = MagicMock(return_value=fake_stream)
    mock_messages = MagicMock()
    mock_messages.stream = mock_stream_method

    mock_client_instance = MagicMock()
    mock_client_instance.messages = mock_messages

    mock_class = MagicMock(return_value=mock_client_instance)

    with patch("wentian.providers.anthropic.anthropic") as mock_module:
        mock_module.Anthropic = mock_class
        yield mock_class, mock_client_instance, mock_stream_method, captured_kwargs


# ---------------------------------------------------------------------------
# T1: thinking=False → no `thinking` key in kwargs; model + max_tokens present
# ---------------------------------------------------------------------------

def test_no_thinking_key_when_thinking_disabled(mock_anthropic_client):
    mock_class, _, mock_stream_method, _ = mock_anthropic_client

    cfg = _make_cfg(thinking=False)
    provider = AnthropicProvider(cfg)
    messages = [{"role": "user", "content": "hello"}]

    list(provider.stream(messages))  # consume generator

    call_kwargs = mock_stream_method.call_args.kwargs
    assert "thinking" not in call_kwargs, "thinking key must be absent when cfg.thinking=False"
    assert call_kwargs["model"] == "claude-opus-4-8"
    assert call_kwargs["max_tokens"] == 64000


# ---------------------------------------------------------------------------
# T2: thinking=True → kwargs contains thinking={"type":"adaptive","display":"summarized"}
# ---------------------------------------------------------------------------

def test_thinking_key_present_when_thinking_enabled(mock_anthropic_client):
    mock_class, _, mock_stream_method, _ = mock_anthropic_client

    cfg = _make_cfg(thinking=True)
    provider = AnthropicProvider(cfg)
    messages = [{"role": "user", "content": "hello"}]

    list(provider.stream(messages))

    call_kwargs = mock_stream_method.call_args.kwargs
    assert "thinking" in call_kwargs, "thinking key must be present when cfg.thinking=True"
    assert call_kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}


# ---------------------------------------------------------------------------
# T3: kwargs must NOT contain temperature/top_p/top_k/budget_tokens
# ---------------------------------------------------------------------------

def test_forbidden_kwargs_absent(mock_anthropic_client):
    mock_class, _, mock_stream_method, _ = mock_anthropic_client

    cfg = _make_cfg()
    provider = AnthropicProvider(cfg)
    list(provider.stream([{"role": "user", "content": "hi"}]))

    call_kwargs = mock_stream_method.call_args.kwargs
    for forbidden in ("temperature", "top_p", "top_k", "budget_tokens"):
        assert forbidden not in call_kwargs, f"'{forbidden}' must not be sent to Anthropic API"


# ---------------------------------------------------------------------------
# T4: event mapping — thinking_delta → ThinkingDelta, text_delta → TextDelta, Done(usage)
# ---------------------------------------------------------------------------

def test_event_mapping_thinking_text_done(mock_anthropic_client):
    mock_class, _, mock_stream_method, _ = mock_anthropic_client

    cfg = _make_cfg(thinking=True)
    provider = AnthropicProvider(cfg)
    events = list(provider.stream([{"role": "user", "content": "q"}]))

    assert events[0] == ThinkingDelta("想")
    assert events[1] == TextDelta("答")
    assert isinstance(events[2], Done)
    usage = events[2].usage
    assert usage is not None
    assert usage.input_tokens == 5
    assert usage.output_tokens == 15


# ---------------------------------------------------------------------------
# T5a: base_url set → passed to client constructor
# ---------------------------------------------------------------------------

def test_base_url_passed_to_client():
    cfg = _make_cfg(base_url="https://custom.endpoint.example.com")
    provider = AnthropicProvider(cfg)

    events = [_make_delta("text_delta", text="hi")]
    fake_stream = _make_stream_cm(events)

    mock_stream_method = MagicMock(return_value=fake_stream)
    mock_messages = MagicMock()
    mock_messages.stream = mock_stream_method

    mock_client_instance = MagicMock()
    mock_client_instance.messages = mock_messages

    mock_class = MagicMock(return_value=mock_client_instance)

    with patch("wentian.providers.anthropic.anthropic") as mock_module:
        mock_module.Anthropic = mock_class
        list(provider.stream([{"role": "user", "content": "hi"}]))

    call_kwargs = mock_class.call_args.kwargs
    assert call_kwargs.get("base_url") == "https://custom.endpoint.example.com"


# ---------------------------------------------------------------------------
# T5b: base_url not set → NOT passed to client constructor at all
# ---------------------------------------------------------------------------

def test_base_url_omitted_when_not_set():
    cfg = _make_cfg(base_url=None)
    provider = AnthropicProvider(cfg)

    events = [_make_delta("text_delta", text="hi")]
    fake_stream = _make_stream_cm(events)

    mock_stream_method = MagicMock(return_value=fake_stream)
    mock_messages = MagicMock()
    mock_messages.stream = mock_stream_method

    mock_client_instance = MagicMock()
    mock_client_instance.messages = mock_messages

    mock_class = MagicMock(return_value=mock_client_instance)

    with patch("wentian.providers.anthropic.anthropic") as mock_module:
        mock_module.Anthropic = mock_class
        list(provider.stream([{"role": "user", "content": "hi"}]))

    call_kwargs = mock_class.call_args.kwargs
    assert "base_url" not in call_kwargs, "base_url must be omitted when cfg.base_url is None"


# ---------------------------------------------------------------------------
# T5c: system param → passed to messages.stream; absent when None
# ---------------------------------------------------------------------------

def test_system_passed_when_provided(mock_anthropic_client):
    _, _, mock_stream_method, _ = mock_anthropic_client

    cfg = _make_cfg()
    provider = AnthropicProvider(cfg)
    list(provider.stream([{"role": "user", "content": "hi"}], system="You are helpful."))

    call_kwargs = mock_stream_method.call_args.kwargs
    assert call_kwargs.get("system") == "You are helpful."


def test_system_omitted_when_none(mock_anthropic_client):
    _, _, mock_stream_method, _ = mock_anthropic_client

    cfg = _make_cfg()
    provider = AnthropicProvider(cfg)
    list(provider.stream([{"role": "user", "content": "hi"}], system=None))

    call_kwargs = mock_stream_method.call_args.kwargs
    assert "system" not in call_kwargs, "system must be omitted when system=None"


# ---------------------------------------------------------------------------
# T6: client is constructed lazily and cached (second call reuses same client)
# ---------------------------------------------------------------------------

def test_client_constructed_lazily_and_cached():
    cfg = _make_cfg()
    provider = AnthropicProvider(cfg)

    def make_fake_stream():
        events = [_make_delta("text_delta", text="x")]
        return _make_stream_cm(events)

    mock_stream_method = MagicMock(side_effect=lambda **kw: make_fake_stream())
    mock_messages = MagicMock()
    mock_messages.stream = mock_stream_method

    mock_client_instance = MagicMock()
    mock_client_instance.messages = mock_messages

    mock_class = MagicMock(return_value=mock_client_instance)

    with patch("wentian.providers.anthropic.anthropic") as mock_module:
        mock_module.Anthropic = mock_class
        list(provider.stream([{"role": "user", "content": "a"}]))
        list(provider.stream([{"role": "user", "content": "b"}]))

    assert mock_class.call_count == 1, "Anthropic() constructor must be called only once (cached)"
