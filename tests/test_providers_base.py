"""Tests for providers/base.py: StreamEvent dataclasses, Provider ABC."""
import dataclasses

import pytest
from wentian.providers.base import (
    ThinkingDelta,
    TextDelta,
    Done,
    Usage,
    Provider,
    StreamEvent,
    ToolSpec,
    ToolCallEvent,
)


# --- Dataclass construction tests ---

def test_thinking_delta_text():
    td = ThinkingDelta("x")
    assert td.text == "x"


def test_text_delta_text():
    td = TextDelta("hello")
    assert td.text == "hello"


def test_done_usage_none():
    d = Done(usage=None)
    assert d.usage is None


def test_done_usage_defaults_to_none():
    assert Done().usage is None


def test_done_usage_with_values():
    u = Usage(input_tokens=10, output_tokens=20)
    d = Done(usage=u)
    assert d.usage.input_tokens == 10
    assert d.usage.output_tokens == 20


# --- v0.3 tool contract: ToolSpec ---

def test_tool_spec_construct():
    """ToolSpec(name, description, parameters) constructs with the given fields."""
    params = {"type": "object", "properties": {"path": {"type": "string"}}}
    ts = ToolSpec(name="read", description="Read a file", parameters=params)
    assert ts.name == "read"
    assert ts.description == "Read a file"
    assert ts.parameters == params


def test_tool_spec_is_frozen():
    """ToolSpec is frozen: assigning to a field raises FrozenInstanceError."""
    ts = ToolSpec(name="read", description="Read a file", parameters={})
    with pytest.raises(dataclasses.FrozenInstanceError):
        ts.name = "write"


# --- v0.3 tool contract: ToolCallEvent ---

def test_tool_call_event_construct_with_dict():
    """ToolCallEvent accepts a dict for arguments."""
    args = {"path": "/tmp/x"}
    ev = ToolCallEvent(id="call_1", name="read", arguments=args)
    assert ev.id == "call_1"
    assert ev.name == "read"
    assert ev.arguments == args


def test_tool_call_event_arguments_accepts_none():
    """ToolCallEvent accepts None for arguments (unparseable JSON)."""
    ev = ToolCallEvent(id="call_2", name="read", arguments=None)
    assert ev.arguments is None


def test_tool_call_event_is_frozen():
    """ToolCallEvent is frozen: assignment raises FrozenInstanceError."""
    ev = ToolCallEvent(id="call_3", name="read", arguments=None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ev.name = "write"


# --- v0.3 tool contract: Done.raw_content ---

def test_done_raw_content_defaults_to_none():
    """Done().raw_content defaults to None."""
    assert Done().raw_content is None


def test_done_carries_usage_and_raw_content():
    """Done can carry both usage and raw_content blocks."""
    u = Usage(input_tokens=1, output_tokens=2)
    blocks = [{"type": "text", "text": "hi"}]
    d = Done(usage=u, raw_content=blocks)
    assert d.usage is u
    assert d.raw_content == blocks


# --- v0.3 tool contract: FakeProvider stream accepts tools= ---

def test_fake_provider_stream_accepts_tools_kw(fake_provider):
    """FakeProvider.stream accepts a tools= keyword (None default) without
    altering the existing event sequence."""
    events = list(fake_provider.stream([], tools=None))
    assert len(events) == 3
    assert isinstance(events[0], ThinkingDelta)
    assert isinstance(events[1], TextDelta)
    assert isinstance(events[2], Done)


# --- Provider ABC test ---

def test_provider_is_abstract():
    """Directly instantiating Provider must raise TypeError (it's an ABC)."""
    with pytest.raises(TypeError):
        Provider()


def test_provider_subclass_without_stream_is_abstract():
    """A subclass that doesn't implement stream() is still abstract."""
    class IncompleteProvider(Provider):
        name = "incomplete"

    with pytest.raises(TypeError):
        IncompleteProvider()


# --- FakeProvider (from conftest) tests ---

def test_fake_provider_yields_events_in_order(fake_provider):
    """FakeProvider yields preset events in order and ends with Done."""
    events = list(fake_provider.stream([]))
    assert len(events) == 3
    assert isinstance(events[0], ThinkingDelta)
    assert events[0].text == "a"
    assert isinstance(events[1], TextDelta)
    assert events[1].text == "b"
    assert isinstance(events[2], Done)
    assert events[2].usage is None


def test_fake_provider_last_event_is_done(fake_provider):
    """The last event yielded must always be Done."""
    events = list(fake_provider.stream([]))
    assert isinstance(events[-1], Done)


def test_fake_provider_records_calls(fake_provider):
    """FakeProvider records messages passed to stream() in self.calls."""
    messages = [{"role": "user", "content": "hi"}]
    list(fake_provider.stream(messages))
    assert len(fake_provider.calls) == 1
    assert fake_provider.calls[0] == messages


def test_fake_provider_records_multiple_calls(fake_provider_factory):
    """FakeProvider accumulates multiple call records."""
    fp = fake_provider_factory([TextDelta("x"), Done(None)])
    msgs1 = [{"role": "user", "content": "first"}]
    msgs2 = [{"role": "user", "content": "first"}, {"role": "assistant", "content": "reply"}, {"role": "user", "content": "second"}]
    list(fp.stream(msgs1))
    list(fp.stream(msgs2))
    assert len(fp.calls) == 2
    assert fp.calls[0] == msgs1
    assert fp.calls[1] == msgs2


def test_fake_provider_is_provider_subclass(fake_provider):
    """FakeProvider must be a subclass of Provider (satisfies AC8 contract)."""
    assert isinstance(fake_provider, Provider)


# --- T61: Usage cache fields (v0.5 / F40 / C25) ---

def test_usage_cache_fields_default_to_zero():
    """Usage(input_tokens=1, output_tokens=2) → cache fields default to 0."""
    u = Usage(input_tokens=1, output_tokens=2)
    assert u.cache_creation_input_tokens == 0
    assert u.cache_read_input_tokens == 0


def test_usage_positional_cache_fields():
    """Usage(1, 2, 3, 4) positional → cache_creation==3, cache_read==4."""
    u = Usage(1, 2, 3, 4)
    assert u.cache_creation_input_tokens == 3
    assert u.cache_read_input_tokens == 4


def test_usage_keyword_cache_read_only():
    """Usage(1, 2, cache_read_input_tokens=5) → cache_read==5, cache_creation==0."""
    u = Usage(1, 2, cache_read_input_tokens=5)
    assert u.cache_read_input_tokens == 5
    assert u.cache_creation_input_tokens == 0
