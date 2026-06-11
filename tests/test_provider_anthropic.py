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
from wentian.providers.base import (
    Done,
    TextDelta,
    ThinkingDelta,
    ToolCallEvent,
    ToolSpec,
)


# ---------------------------------------------------------------------------
# Helpers for building fake SDK objects
# ---------------------------------------------------------------------------

def _make_delta(delta_type: str, **kwargs):
    """Build a fake content_block_delta event."""
    delta = types.SimpleNamespace(type=delta_type, **kwargs)
    return types.SimpleNamespace(type="content_block_delta", delta=delta)


def _make_tool_use_block(block_id: str, name: str, input_dict: dict):
    """Build a fake tool_use content block exposing model_dump().

    Mirrors the anthropic SDK block: ``.type``/``.id``/``.name``/``.input`` plus
    a ``model_dump()`` returning the wire dict (incl. the ``type`` field).
    """
    payload = {
        "type": "tool_use",
        "id": block_id,
        "name": name,
        "input": input_dict,
    }

    class _ToolUseBlock(types.SimpleNamespace):
        def model_dump(self):
            return dict(payload)

    return _ToolUseBlock(**payload)


def _make_text_block(text: str):
    """Build a fake text content block exposing model_dump()."""
    payload = {"type": "text", "text": text}

    class _TextBlock(types.SimpleNamespace):
        def model_dump(self):
            return dict(payload)

    return _TextBlock(**payload)


def _make_stream_cm(
    events: list,
    input_tokens: int = 10,
    output_tokens: int = 20,
    *,
    with_usage: bool = True,
    content: list | None = None,
):
    """Return a fake context-manager stream that yields *events* and has get_final_message()."""
    usage = (
        types.SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
        if with_usage
        else None
    )
    final_message = types.SimpleNamespace(usage=usage, content=content or [])

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
# T3: exact kwargs allowlist — nothing beyond the expected key set is ever sent
# (in particular no temperature/top_p/top_k/budget_tokens — 400 on Opus 4.8)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("thinking", "expected_keys"),
    [
        (False, {"model", "max_tokens", "messages"}),
        (True, {"model", "max_tokens", "messages", "thinking"}),
    ],
    ids=["thinking_off", "thinking_on"],
)
def test_stream_kwargs_exact_allowlist(mock_anthropic_client, thinking, expected_keys):
    mock_class, _, mock_stream_method, _ = mock_anthropic_client

    cfg = _make_cfg(thinking=thinking)
    provider = AnthropicProvider(cfg)
    list(provider.stream([{"role": "user", "content": "hi"}]))

    call_kwargs = mock_stream_method.call_args.kwargs
    assert set(call_kwargs) == expected_keys, (
        f"stream() kwargs must be exactly {expected_keys}, got {set(call_kwargs)}"
    )


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
# T4b: final message with usage=None → Done(usage=None), no crash
# ---------------------------------------------------------------------------

def test_done_usage_none_when_final_usage_missing():
    cfg = _make_cfg()
    provider = AnthropicProvider(cfg)

    events = [_make_delta("text_delta", text="hi")]
    fake_stream = _make_stream_cm(events, with_usage=False)

    mock_stream_method = MagicMock(return_value=fake_stream)
    mock_messages = MagicMock()
    mock_messages.stream = mock_stream_method

    mock_client_instance = MagicMock()
    mock_client_instance.messages = mock_messages

    mock_class = MagicMock(return_value=mock_client_instance)

    with patch("wentian.providers.anthropic.anthropic") as mock_module:
        mock_module.Anthropic = mock_class
        result = list(provider.stream([{"role": "user", "content": "hi"}]))

    assert result[-1] == Done(usage=None), "last event must be Done(usage=None) when usage missing"


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


# ---------------------------------------------------------------------------
# T16: Provider.model attribute — AnthropicProvider stores cfg.model (F13/C1)
# ---------------------------------------------------------------------------

def test_provider_model_attribute_equals_cfg_model():
    """AnthropicProvider.model must equal the model string from ProviderConfig
    immediately after construction — no network call needed (client is lazy).
    v0.2 · C1 · F13（任务 T16）
    """
    cfg = _make_cfg(model="claude-opus-4-8")
    provider = AnthropicProvider(cfg)
    assert provider.model == "claude-opus-4-8", (
        "AnthropicProvider.model must reflect cfg.model after __init__"
    )


# ===========================================================================
# T37: tool declaration, tool_use parsing, raw_content
# v0.3 · C11 · F22（任务 T37）
# ===========================================================================


def _build_mock(fake_stream):
    """Build the patched-anthropic context manager + capture the stream method."""
    mock_stream_method = MagicMock(return_value=fake_stream)
    mock_messages = MagicMock()
    mock_messages.stream = mock_stream_method

    mock_client_instance = MagicMock()
    mock_client_instance.messages = mock_messages

    mock_class = MagicMock(return_value=mock_client_instance)
    return mock_class, mock_stream_method


# ---------------------------------------------------------------------------
# T37-1: tools=[spec] → wire format with name/description/input_schema;
#        tools=None → no `tools` key
# ---------------------------------------------------------------------------

def test_tools_translated_to_wire_format():
    cfg = _make_cfg()
    provider = AnthropicProvider(cfg)

    spec = ToolSpec(
        name="read_file",
        description="Read a file from disk.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )

    fake_stream = _make_stream_cm([_make_delta("text_delta", text="ok")])
    mock_class, mock_stream_method = _build_mock(fake_stream)

    with patch("wentian.providers.anthropic.anthropic") as mock_module:
        mock_module.Anthropic = mock_class
        list(provider.stream([{"role": "user", "content": "hi"}], tools=[spec]))

    call_kwargs = mock_stream_method.call_args.kwargs
    assert call_kwargs["tools"] == [
        {
            "name": "read_file",
            "description": "Read a file from disk.",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        }
    ]


def test_tools_key_absent_when_tools_none(mock_anthropic_client):
    _, _, mock_stream_method, _ = mock_anthropic_client

    cfg = _make_cfg()
    provider = AnthropicProvider(cfg)
    list(provider.stream([{"role": "user", "content": "hi"}], tools=None))

    call_kwargs = mock_stream_method.call_args.kwargs
    assert "tools" not in call_kwargs, "tools key must be absent when tools=None"


# ---------------------------------------------------------------------------
# T37-2: final message with text + two tool_use blocks →
#        TextDelta…, ToolCallEvent×2, then Done
# ---------------------------------------------------------------------------

def test_tool_use_blocks_yield_tool_call_events():
    cfg = _make_cfg()
    provider = AnthropicProvider(cfg)

    content = [
        _make_text_block("let me look"),
        _make_tool_use_block("toolu_1", "read_file", {"path": "a.txt"}),
        _make_tool_use_block("toolu_2", "list_dir", {"path": "."}),
    ]
    events = [_make_delta("text_delta", text="let me look")]
    fake_stream = _make_stream_cm(
        events, input_tokens=7, output_tokens=9, content=content
    )
    mock_class, _ = _build_mock(fake_stream)

    with patch("wentian.providers.anthropic.anthropic") as mock_module:
        mock_module.Anthropic = mock_class
        result = list(provider.stream([{"role": "user", "content": "q"}]))

    assert result[0] == TextDelta("let me look")
    assert result[1] == ToolCallEvent(
        id="toolu_1", name="read_file", arguments={"path": "a.txt"}
    )
    assert result[2] == ToolCallEvent(
        id="toolu_2", name="list_dir", arguments={"path": "."}
    )
    assert isinstance(result[3], Done)
    assert result[3] is result[-1]


# ---------------------------------------------------------------------------
# T37-3: Done.raw_content is each block dumped (incl. type) when tool_use
#        present; None for a pure-text reply
# ---------------------------------------------------------------------------

def test_done_raw_content_dumped_when_tool_use_present():
    cfg = _make_cfg()
    provider = AnthropicProvider(cfg)

    content = [
        _make_text_block("thinking out loud"),
        _make_tool_use_block("toolu_9", "do_it", {"k": 1}),
    ]
    fake_stream = _make_stream_cm(
        [_make_delta("text_delta", text="thinking out loud")], content=content
    )
    mock_class, _ = _build_mock(fake_stream)

    with patch("wentian.providers.anthropic.anthropic") as mock_module:
        mock_module.Anthropic = mock_class
        result = list(provider.stream([{"role": "user", "content": "q"}]))

    done = result[-1]
    assert isinstance(done, Done)
    assert done.raw_content == [
        {"type": "text", "text": "thinking out loud"},
        {"type": "tool_use", "id": "toolu_9", "name": "do_it", "input": {"k": 1}},
    ]


def test_done_raw_content_none_for_pure_text_reply():
    cfg = _make_cfg()
    provider = AnthropicProvider(cfg)

    content = [_make_text_block("just text")]
    fake_stream = _make_stream_cm(
        [_make_delta("text_delta", text="just text")], content=content
    )
    mock_class, _ = _build_mock(fake_stream)

    with patch("wentian.providers.anthropic.anthropic") as mock_module:
        mock_module.Anthropic = mock_class
        result = list(provider.stream([{"role": "user", "content": "q"}]))

    done = result[-1]
    assert isinstance(done, Done)
    assert done.raw_content is None
