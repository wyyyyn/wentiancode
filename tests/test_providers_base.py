"""Tests for providers/base.py: StreamEvent dataclasses, Provider ABC."""
import pytest
from wentian.providers.base import ThinkingDelta, TextDelta, Done, Usage, Provider, StreamEvent


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
