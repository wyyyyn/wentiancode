"""Tests for context/summarizer.py: find_cut_index + summarize + build_compacted.

v0.8 · C49 · F58/F59/F60（任务 T93）

Pure offline tests. The provider is supplied via a *fake* (duck-typed) object
implementing ``stream(messages, *, system, tools)``; no real backend, no network.
"""

import pytest

from wentian.config import ContextConfig
from wentian.context.summarizer import (
    SUMMARY_SYSTEM,
    SummaryError,
    build_compacted,
    find_cut_index,
    summarize,
)
from wentian.providers.base import Done, TextDelta


# ---------------------------------------------------------------------------
# Fake provider
# ---------------------------------------------------------------------------


class FakeProvider:
    """Records the arguments of the last ``stream`` call and replays scripted deltas.

    ``script`` is the list of TextDelta texts the provider yields (followed by a
    final ``Done``). Defaults to a draft + a wrapped ``<final_summary>``.
    """

    def __init__(self, script=None):
        if script is None:
            script = ["草稿：梳理脉络…", "<final_summary>正式摘要内容</final_summary>"]
        self._script = script
        self.received_messages = None
        self.received_system = None
        self.received_tools = "UNSET"

    def stream(self, messages, *, system=None, tools=None):
        self.received_messages = messages
        self.received_system = system
        self.received_tools = tools
        for text in self._script:
            yield TextDelta(text)
        yield Done()


def _user(content):
    return {"role": "user", "content": content}


def _assistant(content="", tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    return msg


def _tool(content, tool_call_id="call_1"):
    return {"role": "tool", "content": content, "tool_call_id": tool_call_id}


# ---------------------------------------------------------------------------
# find_cut_index — tail accumulation, snap, clamp
# ---------------------------------------------------------------------------


def test_find_cut_index_keeps_tail_meeting_token_and_count_targets():
    """Cut leaves a tail that meets BOTH the token target and the min-count."""
    cfg = ContextConfig(recent_keep_tokens=10, recent_keep_min_messages=2)
    # Each message ~ 35 chars → ceil(35/3.5)=10 tokens apiece.
    msgs = [_user("a" * 35) for _ in range(6)]
    cut = find_cut_index(msgs, cfg=cfg)
    # One tail message already meets 10 tokens, but min-count forces >= 2 kept.
    assert cut == len(msgs) - 2
    assert len(msgs[cut:]) >= cfg.recent_keep_min_messages


def test_find_cut_index_token_target_extends_beyond_min_count():
    """When the token target needs more than min-count messages, keep more."""
    cfg = ContextConfig(recent_keep_tokens=30, recent_keep_min_messages=2)
    # Each ~ 35 chars → 10 tokens. Need 3 to reach 30 tokens (>= min 2).
    msgs = [_user("a" * 35) for _ in range(8)]
    cut = find_cut_index(msgs, cfg=cfg)
    assert cut == len(msgs) - 3


def test_find_cut_index_keep_head_is_never_tool():
    """Snap pushes orphan tool-result messages into the summary region."""
    cfg = ContextConfig(recent_keep_tokens=10, recent_keep_min_messages=2)
    # Construct so the raw cut lands on a tool message; snap must skip it.
    msgs = [
        _user("hello"),
        _assistant("", tool_calls=[{"id": "call_1", "name": "f", "arguments": {}}]),
        _tool("t" * 35, "call_1"),  # this would be the raw keep-head
        _user("x" * 35),
        _user("y" * 35),
    ]
    cut = find_cut_index(msgs, cfg=cfg)
    assert msgs[cut]["role"] != "tool"


def test_find_cut_index_does_not_split_assistant_tool_pair():
    """An assistant(tool_calls) + its tool result are never split across the cut."""
    cfg = ContextConfig(recent_keep_tokens=5, recent_keep_min_messages=1)
    msgs = [
        _user("intro " * 5),
        _assistant("", tool_calls=[{"id": "call_1", "name": "f", "arguments": {}}]),
        _tool("result " * 5, "call_1"),
        _user("z" * 35),
    ]
    cut = find_cut_index(msgs, cfg=cfg)
    kept = msgs[cut:]
    # No kept tool result may reference a tool_use that was summarized away.
    summarized_ids = {
        c["id"]
        for m in msgs[:cut]
        if m.get("role") == "assistant"
        for c in (m.get("tool_calls") or [])
    }
    kept_tool_ids = {m.get("tool_call_id") for m in kept if m.get("role") == "tool"}
    # Any kept tool result's id must NOT belong to a summarized assistant.
    assert kept_tool_ids.isdisjoint(summarized_ids)
    # And the keep-head is not a bare tool result.
    assert kept[0]["role"] != "tool"


def test_find_cut_index_clamp_short_history():
    """History shorter than the min-count → cut=0 (nothing summarized)."""
    cfg = ContextConfig(recent_keep_tokens=10, recent_keep_min_messages=5)
    msgs = [_user("a" * 35) for _ in range(3)]  # only 3 < min 5
    assert find_cut_index(msgs, cfg=cfg) == 0


def test_find_cut_index_clamp_keep_target_covers_all():
    """When the keep target covers the entire history → cut=0."""
    cfg = ContextConfig(recent_keep_tokens=1000, recent_keep_min_messages=2)
    msgs = [_user("a" * 35) for _ in range(4)]  # total 40 tokens < 1000
    assert find_cut_index(msgs, cfg=cfg) == 0


def test_find_cut_index_empty_history():
    """Empty history → cut=0."""
    cfg = ContextConfig()
    assert find_cut_index([], cfg=cfg) == 0


# ---------------------------------------------------------------------------
# summarize — request shape, prompt discipline, final extraction
# ---------------------------------------------------------------------------


def test_summarize_request_carries_no_tools():
    """The summary request must physically disable tools (tools is None)."""
    fake = FakeProvider()
    summarize(fake, [_user("earlier conversation")])
    assert fake.received_tools is None


def test_summarize_uses_summary_system_prompt():
    """The request is issued with SUMMARY_SYSTEM as the system prompt."""
    fake = FakeProvider()
    summarize(fake, [_user("earlier")])
    assert fake.received_system == SUMMARY_SYSTEM


def test_summarize_appends_user_instruction_with_discipline_semantics():
    """The last appended user message carries the prompt discipline semantics."""
    fake = FakeProvider()
    earlier = [_user("earlier")]
    summarize(fake, earlier)
    sent = fake.received_messages
    # earlier messages preserved, plus one trailing user instruction.
    assert sent[: len(earlier)] == earlier
    assert sent[-1]["role"] == "user"
    instruction = sent[-1]["content"]
    # Disallow tools + draft-then-final + eight sections + final_summary tag.
    assert "禁止" in instruction and "工具" in instruction
    assert "草稿" in instruction
    assert "<final_summary>" in instruction
    # eight section markers
    for marker in ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧"]:
        assert marker in instruction


def test_summary_system_constant_has_discipline_semantics():
    """SUMMARY_SYSTEM itself states the no-tool / draft-then-final discipline."""
    assert "禁止" in SUMMARY_SYSTEM and "工具" in SUMMARY_SYSTEM
    assert "草稿" in SUMMARY_SYSTEM


def test_summarize_extracts_final_summary_content():
    """The <final_summary> body is extracted; the draft is discarded."""
    fake = FakeProvider(
        script=["丢弃的草稿…", "<final_summary>这是正式摘要</final_summary>"]
    )
    result = summarize(fake, [_user("earlier")])
    assert result == "这是正式摘要"
    assert "草稿" not in result


def test_summarize_falls_back_to_full_text_when_no_tag():
    """Missing the tag → tolerate by taking the whole stripped text."""
    fake = FakeProvider(script=["  没有标签的全文摘要  "])
    result = summarize(fake, [_user("earlier")])
    assert result == "没有标签的全文摘要"


def test_summarize_empty_output_raises():
    """Empty / whitespace-only output → SummaryError."""
    fake = FakeProvider(script=["   "])
    with pytest.raises(SummaryError):
        summarize(fake, [_user("earlier")])


def test_summarize_empty_final_summary_raises():
    """A present-but-empty <final_summary> → SummaryError."""
    fake = FakeProvider(script=["草稿", "<final_summary>   </final_summary>"])
    with pytest.raises(SummaryError):
        summarize(fake, [_user("earlier")])


# ---------------------------------------------------------------------------
# build_compacted — shape
# ---------------------------------------------------------------------------


def test_build_compacted_shape():
    """Returns [one merged summary+boundary user message] + kept."""
    kept = [_user("recent"), _assistant("reply")]
    out = build_compacted("正式摘要文本", kept)
    assert out[0]["role"] == "user"
    head = out[0]["content"]
    assert "<conversation_summary>" in head
    assert "正式摘要文本" in head
    assert "</conversation_summary>" in head
    # boundary semantics: re-read files / do not hallucinate.
    assert "重新" in head and "脑补" in head
    # kept appended verbatim after the single merged message.
    assert out[1:] == kept
    assert len(out) == 1 + len(kept)


def test_build_compacted_empty_kept():
    """Empty kept → just the merged summary+boundary message."""
    out = build_compacted("摘要", [])
    assert len(out) == 1
    assert out[0]["role"] == "user"
