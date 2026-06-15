"""Tests for OpenAICompatProvider (T12).

RED → GREEN cycle:
- chat.completions.create called with stream=True, correct model, messages
- base_url / api_key forwarded to OpenAI constructor
- system prompt injected as first message with role "system"
- delta.content → TextDelta
- delta.reasoning_content → ThinkingDelta
- chunks without reasoning_content don't raise
- stream ends with Done
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from wentian.config import ProviderConfig
from wentian.providers.openai_compat import OpenAICompatProvider
from wentian.providers.base import (
    Done,
    TextDelta,
    ThinkingDelta,
    ToolCallEvent,
    ToolSpec,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(
    *,
    name: str = "deepseek",
    model: str = "deepseek-chat",
    api_key: str = "sk-test",
    base_url: str | None = "https://api.deepseek.com",
) -> ProviderConfig:
    return ProviderConfig(
        name=name,
        protocol="openai",
        model=model,
        api_key=api_key,
        base_url=base_url,
    )


def _make_chunk(content: str | None = None, reasoning: str | None = None) -> SimpleNamespace:
    """Build a fake SSE chunk as returned by the openai SDK."""
    delta = SimpleNamespace(content=content)
    if reasoning is not None:
        delta.reasoning_content = reasoning
    choice = SimpleNamespace(delta=delta)
    return SimpleNamespace(choices=[choice], usage=None)


def _tc_fragment(
    index: int,
    *,
    id: str | None = None,
    name: str | None = None,
    arguments: str | None = None,
) -> SimpleNamespace:
    """Build a single streaming tool_call fragment as the openai SDK yields it.

    First fragment of a call carries id + function.name; subsequent fragments
    carry only function.arguments string pieces to be concatenated.
    """
    func = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(index=index, id=id, function=func)


def _make_tc_chunk(fragments: list[SimpleNamespace]) -> SimpleNamespace:
    """Build a chunk whose delta carries tool_call fragments (no text content)."""
    delta = SimpleNamespace(content=None, tool_calls=fragments)
    choice = SimpleNamespace(delta=delta)
    return SimpleNamespace(choices=[choice], usage=None)


class _FakeStream:
    """Mimics the openai Stream object: iterable AND a context manager."""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.closed = False

    def __iter__(self):
        return iter(self._chunks)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.closed = True
        return False


def _run_stream(provider: OpenAICompatProvider, messages, *, system=None) -> list:
    return list(provider.stream(messages, system=system))


# ---------------------------------------------------------------------------
# Constructor / client creation
# ---------------------------------------------------------------------------

class TestOpenAICompatProviderInit:
    def test_name_set_from_cfg(self):
        cfg = _make_cfg(name="my-openai")
        p = OpenAICompatProvider(cfg)
        assert p.name == "my-openai"

    def test_stream_is_generator(self):
        """stream() must return an iterator (lazy generator)."""
        cfg = _make_cfg()
        p = OpenAICompatProvider(cfg)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _FakeStream([])
        p._client = mock_client
        result = p.stream([{"role": "user", "content": "hi"}])
        assert hasattr(result, "__iter__") and hasattr(result, "__next__")


# ---------------------------------------------------------------------------
# OpenAI client construction
# ---------------------------------------------------------------------------

class TestClientConstruction:
    def test_api_key_forwarded(self):
        cfg = _make_cfg(api_key="sk-abc", base_url=None)
        p = OpenAICompatProvider(cfg)
        with patch("wentian.providers.openai_compat.OpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = _FakeStream([])
            _run_stream(p, [{"role": "user", "content": "hi"}])
        mock_cls.assert_called_once()
        _, kwargs = mock_cls.call_args
        assert kwargs["api_key"] == "sk-abc"

    def test_base_url_forwarded_when_set(self):
        cfg = _make_cfg(api_key="sk-abc", base_url="https://api.deepseek.com")
        p = OpenAICompatProvider(cfg)
        with patch("wentian.providers.openai_compat.OpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = _FakeStream([])
            _run_stream(p, [{"role": "user", "content": "hi"}])
        _, kwargs = mock_cls.call_args
        assert kwargs["base_url"] == "https://api.deepseek.com"

    def test_base_url_omitted_when_none(self):
        cfg = _make_cfg(api_key="sk-abc", base_url=None)
        p = OpenAICompatProvider(cfg)
        with patch("wentian.providers.openai_compat.OpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = _FakeStream([])
            _run_stream(p, [{"role": "user", "content": "hi"}])
        _, kwargs = mock_cls.call_args
        assert "base_url" not in kwargs

    def test_client_cached_across_calls(self):
        cfg = _make_cfg()
        p = OpenAICompatProvider(cfg)
        with patch("wentian.providers.openai_compat.OpenAI") as mock_cls:
            mock_cls.return_value.chat.completions.create.return_value = _FakeStream([])
            _run_stream(p, [{"role": "user", "content": "hi"}])
            mock_cls.return_value.chat.completions.create.return_value = _FakeStream([])
            _run_stream(p, [{"role": "user", "content": "hi2"}])
        # Constructor called only once despite two stream() calls
        assert mock_cls.call_count == 1


# ---------------------------------------------------------------------------
# API call parameters
# ---------------------------------------------------------------------------

class TestCreateCallParams:
    def _setup(self, cfg: ProviderConfig):
        p = OpenAICompatProvider(cfg)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _FakeStream([])
        p._client = mock_client
        return p, mock_client

    def test_stream_true_passed(self):
        p, mock_client = self._setup(_make_cfg())
        _run_stream(p, [{"role": "user", "content": "hi"}])
        _, kwargs = mock_client.chat.completions.create.call_args
        assert kwargs["stream"] is True

    def test_model_forwarded(self):
        p, mock_client = self._setup(_make_cfg(model="deepseek-reasoner"))
        _run_stream(p, [{"role": "user", "content": "hi"}])
        _, kwargs = mock_client.chat.completions.create.call_args
        assert kwargs["model"] == "deepseek-reasoner"

    def test_messages_forwarded(self):
        p, mock_client = self._setup(_make_cfg())
        msgs = [{"role": "user", "content": "hello"}]
        _run_stream(p, msgs)
        _, kwargs = mock_client.chat.completions.create.call_args
        assert {"role": "user", "content": "hello"} in kwargs["messages"]

    def test_system_injected_as_first_message(self):
        p, mock_client = self._setup(_make_cfg())
        msgs = [{"role": "user", "content": "hi"}]
        _run_stream(p, msgs, system="You are helpful.")
        _, kwargs = mock_client.chat.completions.create.call_args
        first = kwargs["messages"][0]
        assert first["role"] == "system"
        assert first["content"] == "You are helpful."

    def test_system_message_is_first_before_user(self):
        p, mock_client = self._setup(_make_cfg())
        msgs = [{"role": "user", "content": "hi"}]
        _run_stream(p, msgs, system="Be concise.")
        _, kwargs = mock_client.chat.completions.create.call_args
        roles = [m["role"] for m in kwargs["messages"]]
        assert roles[0] == "system"
        assert roles[1] == "user"

    def test_no_system_message_when_none(self):
        p, mock_client = self._setup(_make_cfg())
        msgs = [{"role": "user", "content": "hi"}]
        _run_stream(p, msgs, system=None)
        _, kwargs = mock_client.chat.completions.create.call_args
        roles = [m["role"] for m in kwargs["messages"]]
        assert "system" not in roles


# ---------------------------------------------------------------------------
# Event mapping
# ---------------------------------------------------------------------------

class TestEventMapping:
    def _provider_with_chunks(self, chunks) -> OpenAICompatProvider:
        cfg = _make_cfg()
        p = OpenAICompatProvider(cfg)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _FakeStream(chunks)
        p._client = mock_client
        return p

    def test_content_delta_yields_text_delta(self):
        chunk = _make_chunk(content="答")
        p = self._provider_with_chunks([chunk])
        events = _run_stream(p, [{"role": "user", "content": "hi"}])
        assert TextDelta(text="答") in events

    def test_reasoning_content_yields_thinking_delta(self):
        chunk = _make_chunk(reasoning="想")
        p = self._provider_with_chunks([chunk])
        events = _run_stream(p, [{"role": "user", "content": "hi"}])
        assert ThinkingDelta(text="想") in events

    def test_chunk_without_reasoning_content_does_not_raise(self):
        """Chunks that lack reasoning_content attribute entirely must not crash."""
        chunk = _make_chunk(content="hello")  # no reasoning_content attribute at all
        p = self._provider_with_chunks([chunk])
        # Should not raise
        events = _run_stream(p, [{"role": "user", "content": "hi"}])
        assert any(isinstance(e, TextDelta) for e in events)

    def test_stream_ends_with_done(self):
        chunks = [_make_chunk(content="a"), _make_chunk(content="b")]
        p = self._provider_with_chunks(chunks)
        events = _run_stream(p, [{"role": "user", "content": "hi"}])
        assert isinstance(events[-1], Done)

    def test_empty_stream_still_yields_done(self):
        p = self._provider_with_chunks([])
        events = _run_stream(p, [{"role": "user", "content": "hi"}])
        assert events == [Done(usage=None)]

    def test_none_content_not_yielded(self):
        """delta.content=None must not produce a TextDelta."""
        chunk = _make_chunk(content=None)
        p = self._provider_with_chunks([chunk])
        events = _run_stream(p, [{"role": "user", "content": "hi"}])
        assert not any(isinstance(e, TextDelta) for e in events)

    def test_both_content_and_reasoning_in_same_chunk(self):
        """A chunk with both fields yields ThinkingDelta then TextDelta."""
        chunk = _make_chunk(content="答", reasoning="想")
        p = self._provider_with_chunks([chunk])
        events = _run_stream(p, [{"role": "user", "content": "hi"}])
        types = [type(e) for e in events]
        assert ThinkingDelta in types
        assert TextDelta in types

    def test_empty_choices_chunk_skipped(self):
        """Chunk with empty choices list must be silently skipped."""
        empty_chunk = SimpleNamespace(choices=[], usage=None)
        content_chunk = _make_chunk(content="ok")
        p = self._provider_with_chunks([empty_chunk, content_chunk])
        events = _run_stream(p, [{"role": "user", "content": "hi"}])
        assert TextDelta(text="ok") in events

    def test_multiple_text_chunks_order_preserved(self):
        chunks = [_make_chunk(content=c) for c in ["a", "b", "c"]]
        p = self._provider_with_chunks(chunks)
        events = _run_stream(p, [{"role": "user", "content": "hi"}])
        text_events = [e for e in events if isinstance(e, TextDelta)]
        assert [e.text for e in text_events] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# Usage mapping
# ---------------------------------------------------------------------------

class TestUsageMapping:
    def _provider_with_chunks(self, chunks) -> OpenAICompatProvider:
        cfg = _make_cfg()
        p = OpenAICompatProvider(cfg)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _FakeStream(chunks)
        p._client = mock_client
        return p

    def test_usage_none_when_no_chunk_carries_usage(self):
        chunk = _make_chunk(content="hi")
        p = self._provider_with_chunks([chunk])
        events = _run_stream(p, [{"role": "user", "content": "hi"}])
        done = events[-1]
        assert isinstance(done, Done)
        assert done.usage is None

    def test_usage_mapped_from_last_chunk_with_usage(self):
        chunk = _make_chunk(content="hi")
        usage_ns = SimpleNamespace(prompt_tokens=10, completion_tokens=20)
        chunk.usage = usage_ns
        p = self._provider_with_chunks([chunk])
        events = _run_stream(p, [{"role": "user", "content": "hi"}])
        done = events[-1]
        assert isinstance(done, Done)
        assert done.usage is not None
        assert done.usage.input_tokens == 10
        assert done.usage.output_tokens == 20

    def test_usage_from_trailing_empty_choices_chunk(self):
        """Production pattern: real OpenAI/DeepSeek streams deliver final
        usage on a trailing chunk with an empty choices list."""
        content_chunk = _make_chunk(content="hi")
        usage_chunk = SimpleNamespace(
            choices=[],
            usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3),
        )
        p = self._provider_with_chunks([content_chunk, usage_chunk])
        events = _run_stream(p, [{"role": "user", "content": "hi"}])
        done = events[-1]
        assert isinstance(done, Done)
        assert done.usage is not None
        assert done.usage.input_tokens == 7
        assert done.usage.output_tokens == 3


# ---------------------------------------------------------------------------
# Stream lifecycle
# ---------------------------------------------------------------------------

class TestStreamLifecycle:
    def test_response_closed_after_consumption(self):
        """stream() must use the openai Stream as a context manager so the
        HTTP response is closed."""
        fake = _FakeStream([_make_chunk(content="hi")])
        cfg = _make_cfg()
        p = OpenAICompatProvider(cfg)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = fake
        p._client = mock_client
        _run_stream(p, [{"role": "user", "content": "hi"}])
        assert fake.closed is True

    def test_response_closed_on_early_abandonment(self):
        """Abandoning the generator mid-stream must still close the response."""
        fake = _FakeStream([_make_chunk(content=c) for c in ["a", "b", "c"]])
        cfg = _make_cfg()
        p = OpenAICompatProvider(cfg)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = fake
        p._client = mock_client
        gen = p.stream([{"role": "user", "content": "hi"}])
        next(gen)  # consume one event
        gen.close()  # abandon
        assert fake.closed is True


# ---------------------------------------------------------------------------
# T16: Provider.model attribute — OpenAICompatProvider stores cfg.model (F13/C1)
# ---------------------------------------------------------------------------

def test_provider_model_attribute_equals_cfg_model():
    """OpenAICompatProvider.model must equal the model string from ProviderConfig
    immediately after construction — no network call needed (client is lazy).
    v0.2 · C1 · F13（任务 T16）
    """
    cfg = _make_cfg(model="deepseek-chat")
    p = OpenAICompatProvider(cfg)
    assert p.model == "deepseek-chat", (
        "OpenAICompatProvider.model must reflect cfg.model after __init__"
    )


# ---------------------------------------------------------------------------
# T39: tool declaration + streaming fragment assembly (v0.3 · C11 · F22)
# ---------------------------------------------------------------------------

def _make_spec(
    *,
    name: str = "read_file",
    description: str = "Read a file.",
    parameters: dict | None = None,
) -> ToolSpec:
    if parameters is None:
        parameters = {"type": "object", "properties": {"path": {"type": "string"}}}
    return ToolSpec(name=name, description=description, parameters=parameters)


class TestToolDeclaration:
    def _setup(self, chunks=None):
        p = OpenAICompatProvider(_make_cfg())
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _FakeStream(chunks or [])
        p._client = mock_client
        return p, mock_client

    def test_tools_translated_to_function_wire_format(self):
        spec = _make_spec()
        p, mock_client = self._setup()
        list(p.stream([{"role": "user", "content": "hi"}], tools=[spec]))
        _, kwargs = mock_client.chat.completions.create.call_args
        assert kwargs["tools"] == [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file.",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                    },
                },
            }
        ]

    def test_tools_omitted_when_none(self):
        p, mock_client = self._setup()
        list(p.stream([{"role": "user", "content": "hi"}], tools=None))
        _, kwargs = mock_client.chat.completions.create.call_args
        assert "tools" not in kwargs


class TestToolCallAssembly:
    def _provider_with_chunks(self, chunks) -> OpenAICompatProvider:
        p = OpenAICompatProvider(_make_cfg())
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _FakeStream(chunks)
        p._client = mock_client
        return p

    def test_single_call_assembled_across_three_chunks(self):
        chunks = [
            _make_tc_chunk([_tc_fragment(0, id="call_1", name="read_file")]),
            _make_tc_chunk([_tc_fragment(0, arguments='{"path":')]),
            _make_tc_chunk([_tc_fragment(0, arguments=' "a.txt"}')]),
        ]
        p = self._provider_with_chunks(chunks)
        events = _run_stream(p, [{"role": "user", "content": "hi"}])
        tool_calls = [e for e in events if isinstance(e, ToolCallEvent)]
        assert len(tool_calls) == 1
        ev = tool_calls[0]
        assert ev.id == "call_1"
        assert ev.name == "read_file"
        assert ev.arguments == {"path": "a.txt"}

    def test_tool_call_event_emitted_before_done(self):
        chunks = [
            _make_tc_chunk([_tc_fragment(0, id="c1", name="f", arguments="{}")]),
        ]
        p = self._provider_with_chunks(chunks)
        events = _run_stream(p, [{"role": "user", "content": "hi"}])
        assert isinstance(events[-1], Done)
        assert isinstance(events[-2], ToolCallEvent)

    def test_two_interleaved_calls_emitted_in_index_order(self):
        chunks = [
            _make_tc_chunk([_tc_fragment(0, id="c0", name="first")]),
            _make_tc_chunk([_tc_fragment(1, id="c1", name="second")]),
            _make_tc_chunk([_tc_fragment(1, arguments='{"y": 2}')]),
            _make_tc_chunk([_tc_fragment(0, arguments='{"x": 1}')]),
        ]
        p = self._provider_with_chunks(chunks)
        events = _run_stream(p, [{"role": "user", "content": "hi"}])
        tool_calls = [e for e in events if isinstance(e, ToolCallEvent)]
        assert [tc.id for tc in tool_calls] == ["c0", "c1"]
        assert tool_calls[0].name == "first"
        assert tool_calls[0].arguments == {"x": 1}
        assert tool_calls[1].name == "second"
        assert tool_calls[1].arguments == {"y": 2}

    def test_invalid_json_arguments_yield_none(self):
        chunks = [
            _make_tc_chunk([_tc_fragment(0, id="c1", name="f", arguments="{not json")]),
        ]
        p = self._provider_with_chunks(chunks)
        events = _run_stream(p, [{"role": "user", "content": "hi"}])
        tool_calls = [e for e in events if isinstance(e, ToolCallEvent)]
        assert len(tool_calls) == 1
        assert tool_calls[0].arguments is None

    def test_text_then_tool_call_both_emitted(self):
        chunks = [
            _make_chunk(content="thinking..."),
            _make_tc_chunk([_tc_fragment(0, id="c1", name="f", arguments="{}")]),
        ]
        p = self._provider_with_chunks(chunks)
        events = _run_stream(p, [{"role": "user", "content": "hi"}])
        assert TextDelta(text="thinking...") in events
        assert any(isinstance(e, ToolCallEvent) for e in events)


# ---------------------------------------------------------------------------
# T40: neutral history -> wire format conversion (v0.3 · C11 · F22)
# ---------------------------------------------------------------------------

class TestHistoryConversion:
    def _build(self, messages, *, system=None) -> list[dict]:
        p = OpenAICompatProvider(_make_cfg())
        return p._build_messages(messages, system=system)

    def test_assistant_with_tool_calls_to_wire_format(self):
        msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "name": "read_file", "arguments": {"path": "a.txt"}},
            ],
        }
        out = self._build([msg])
        assert out == [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "a.txt"}',
                        },
                    }
                ],
            }
        ]

    def test_raw_content_field_ignored(self):
        msg = {
            "role": "assistant",
            "content": "hi",
            "raw_content": [{"type": "text", "text": "hi"}],
        }
        out = self._build([msg])
        assert "raw_content" not in out[0]
        assert out[0] == {"role": "assistant", "content": "hi"}

    def test_tool_role_message_to_wire_format(self):
        msg = {"role": "tool", "tool_call_id": "c1", "content": "file body"}
        out = self._build([msg])
        assert out == [
            {"role": "tool", "tool_call_id": "c1", "content": "file body"}
        ]

    def test_tool_error_prefixes_content(self):
        msg = {
            "role": "tool",
            "tool_call_id": "c1",
            "content": "boom",
            "is_error": True,
        }
        out = self._build([msg])
        assert out[0]["content"] == "[error] boom"

    def test_plaintext_history_passthrough_regression(self):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        out = self._build(msgs)
        assert out == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]

    def test_system_injection_first_regression(self):
        msgs = [{"role": "user", "content": "hi"}]
        out = self._build(msgs, system="Be brief.")
        assert out[0] == {"role": "system", "content": "Be brief."}
        assert out[1] == {"role": "user", "content": "hi"}


# ---------------------------------------------------------------------------
# T63: OpenAI-compat cache usage parsing (v0.5 · C24 · F38/F40)
# ---------------------------------------------------------------------------

class TestCacheUsageParsing:
    """OpenAI-compat servers signal server-side prefix-cache hits via
    usage.prompt_tokens_details.cached_tokens.  We map this to
    Usage.cache_read_input_tokens; cache_creation is always 0 for this
    protocol.

    Three sub-cases:
    1. cached_tokens present → mapped correctly, creation=0.
    2. prompt_tokens_details missing → cache_read=0, no error.
    3. cached_tokens missing inside prompt_tokens_details → cache_read=0, no error.
    """

    def _provider_with_chunks(self, chunks) -> OpenAICompatProvider:
        p = OpenAICompatProvider(_make_cfg())
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _FakeStream(chunks)
        p._client = mock_client
        return p

    # ── Regression: system is still a plain single system message ────────────

    def test_system_still_plain_single_message(self):
        """T63 regression: _build_messages must still emit a single plain
        system dict — no cache_control or extra fields."""
        p = OpenAICompatProvider(_make_cfg())
        out = p._build_messages(
            [{"role": "user", "content": "hi"}],
            system="Be concise.",
        )
        assert out[0] == {"role": "system", "content": "Be concise."}
        # Exactly one system message, no cache_control injected
        system_msgs = [m for m in out if m.get("role") == "system"]
        assert len(system_msgs) == 1
        assert "cache_control" not in out[0]

    # ── cached_tokens present → mapped to cache_read_input_tokens ───────────

    def test_cached_tokens_mapped_to_cache_read(self):
        """usage.prompt_tokens_details.cached_tokens=50 → cache_read_input_tokens=50."""
        details = SimpleNamespace(cached_tokens=50)
        usage_ns = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=20,
            prompt_tokens_details=details,
        )
        content_chunk = _make_chunk(content="hi")
        usage_chunk = SimpleNamespace(choices=[], usage=usage_ns)
        p = self._provider_with_chunks([content_chunk, usage_chunk])
        events = _run_stream(p, [{"role": "user", "content": "hi"}])
        done = events[-1]
        assert isinstance(done, Done)
        assert done.usage is not None
        assert done.usage.cache_read_input_tokens == 50
        assert done.usage.cache_creation_input_tokens == 0
        assert done.usage.input_tokens == 100
        assert done.usage.output_tokens == 20

    # ── prompt_tokens_details missing → cache_read stays 0, no error ────────

    def test_no_prompt_tokens_details_gives_zero_cache_read(self):
        """When prompt_tokens_details is absent, cache_read must be 0 (no crash)."""
        usage_ns = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
        # No prompt_tokens_details attribute at all
        usage_chunk = SimpleNamespace(choices=[], usage=usage_ns)
        p = self._provider_with_chunks([usage_chunk])
        events = _run_stream(p, [{"role": "user", "content": "hi"}])
        done = events[-1]
        assert isinstance(done, Done)
        assert done.usage is not None
        assert done.usage.cache_read_input_tokens == 0
        assert done.usage.cache_creation_input_tokens == 0

    # ── cached_tokens key missing inside details → cache_read stays 0 ───────

    def test_cached_tokens_key_missing_inside_details(self):
        """When prompt_tokens_details exists but lacks cached_tokens, cache_read=0."""
        details = SimpleNamespace()  # no cached_tokens attribute
        usage_ns = SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=5,
            prompt_tokens_details=details,
        )
        usage_chunk = SimpleNamespace(choices=[], usage=usage_ns)
        p = self._provider_with_chunks([usage_chunk])
        events = _run_stream(p, [{"role": "user", "content": "hi"}])
        done = events[-1]
        assert isinstance(done, Done)
        assert done.usage is not None
        assert done.usage.cache_read_input_tokens == 0
        assert done.usage.cache_creation_input_tokens == 0

    # ── dict-style usage (alternative form) → also handled gracefully ───────

    def test_dict_style_usage_with_cached_tokens(self):
        """Some compat servers may return usage as a plain dict; the extractor
        must handle that too (via .get())."""
        # Build a dict-like object: use a plain dict wrapped to also support
        # attribute access — we test the pure-dict path via the extractor
        # directly, as the SDK almost always returns objects, but we verify
        # the defensive branch doesn't crash when someone passes a Namespace
        # with prompt_tokens_details as a dict.
        details = {"cached_tokens": 30}
        usage_ns = SimpleNamespace(
            prompt_tokens=80,
            completion_tokens=15,
            prompt_tokens_details=details,
        )
        usage_chunk = SimpleNamespace(choices=[], usage=usage_ns)
        p = self._provider_with_chunks([usage_chunk])
        events = _run_stream(p, [{"role": "user", "content": "hi"}])
        done = events[-1]
        assert isinstance(done, Done)
        assert done.usage is not None
        assert done.usage.cache_read_input_tokens == 30
