"""Tests for memory/extractor.py: prompt + four-category parse + LLM dedup.

v0.9 · C57 · F67/N28/N30（任务 T104）

Pure offline tests. The provider is duck-typed via a *fake* implementing
``stream(messages, *, system, tools)`` — no real backend, no network. The
parser ``parse_notes`` is exercised standalone (offline pure function); the
end-to-end ``extract`` uses the fake provider returning fixed note JSON.
"""

from __future__ import annotations

import json

from wentian.memory.extractor import (
    EXTRACT_SYSTEM,
    NoteDecision,
    build_recent_window,
    extract,
    parse_notes,
)
from wentian.providers.base import Done, TextDelta


# ---------------------------------------------------------------------------
# Fake provider
# ---------------------------------------------------------------------------


class FakeProvider:
    """Records the last ``stream`` call args and replays a scripted text payload."""

    def __init__(self, payload: str):
        self._payload = payload
        self.received_messages = None
        self.received_system = "UNSET"
        self.received_tools = "UNSET"
        self.call_count = 0

    def stream(self, messages, *, system=None, tools=None):
        self.call_count += 1
        self.received_messages = messages
        self.received_system = system
        self.received_tools = tools
        yield TextDelta(self._payload)
        yield Done()


class BoomProvider:
    def stream(self, messages, *, system=None, tools=None):
        raise RuntimeError("provider exploded")
        yield  # pragma: no cover - unreachable, makes this a generator


# ---------------------------------------------------------------------------
# Fixtures of fixed model output
# ---------------------------------------------------------------------------


def _four_note_payload():
    notes = [
        {
            "decision": "add",
            "category": "用户偏好",
            "title": "偏好两格缩进",
            "content": "用户希望代码用两格缩进。",
            "tags": ["python", "style"],
        },
        {
            "decision": "add",
            "category": "纠正反馈",
            "title": "不要 emoji",
            "content": "用户纠正：回复不要带 emoji。",
            "tags": ["tone"],
        },
        {
            "decision": "add",
            "category": "项目知识",
            "title": "测试用 uv run pytest",
            "content": "本项目用 uv run pytest 跑测试。",
            "tags": ["test"],
        },
        {
            "decision": "add",
            "category": "参考资料",
            "title": "spec 在 spec 目录",
            "content": "需求事实来源在 spec/ 目录。",
            "tags": ["doc"],
        },
    ]
    return (
        "这是模型的分析草稿……\n```json\n"
        + json.dumps({"notes": notes}, ensure_ascii=False)
        + "\n```\n收工。"
    )


def _u(content):
    return {"role": "user", "content": content}


def _a(content):
    return {"role": "assistant", "content": content}


def _t(content, tool_call_id="c1"):
    return {"role": "tool", "content": content, "tool_call_id": tool_call_id}


# ---------------------------------------------------------------------------
# parse_notes — pure offline function
# ---------------------------------------------------------------------------


def test_parse_notes_four_categories():
    decisions = parse_notes(
        _four_note_payload(), source_session="sess-1", now="2026-06-19T10:00:00"
    )
    assert len(decisions) == 4
    cats = [d.note.category for d in decisions]
    assert cats == ["用户偏好", "纠正反馈", "项目知识", "参考资料"]
    # scope filled by default attribution
    by_cat = {d.note.category: d for d in decisions}
    assert by_cat["用户偏好"].note.scope == "user"
    assert by_cat["纠正反馈"].note.scope == "user"
    assert by_cat["项目知识"].note.scope == "project"
    assert by_cat["参考资料"].note.scope == "project"
    # metadata stamped onto every note
    for d in decisions:
        assert d.note.source_session == "sess-1"
        assert d.note.created_at == "2026-06-19T10:00:00"


def test_parse_notes_carries_decision_action():
    payload = json.dumps(
        {
            "notes": [
                {
                    "decision": "skip",
                    "category": "用户偏好",
                    "title": "已知偏好",
                    "content": "x",
                },
                {
                    "decision": "update",
                    "category": "项目知识",
                    "title": "改过的知识",
                    "content": "y",
                },
            ]
        },
        ensure_ascii=False,
    )
    decisions = parse_notes(payload, source_session="s", now="t")
    actions = [d.action for d in decisions]
    assert actions == ["skip", "update"]
    assert all(isinstance(d, NoteDecision) for d in decisions)


def test_parse_notes_explicit_scope_overrides_default():
    payload = json.dumps(
        {
            "notes": [
                {
                    "decision": "add",
                    "category": "项目知识",
                    "scope": "user",
                    "title": "改判到 user",
                    "content": "z",
                }
            ]
        },
        ensure_ascii=False,
    )
    decisions = parse_notes(payload, source_session="s", now="t")
    assert decisions[0].note.scope == "user"


def test_parse_notes_empty_or_garbage_returns_empty():
    assert parse_notes("", source_session="s", now="t") == []
    assert parse_notes("no json here at all", source_session="s", now="t") == []
    assert parse_notes("{not valid json}", source_session="s", now="t") == []


def test_parse_notes_tolerates_surrounding_noise():
    decisions = parse_notes(_four_note_payload(), source_session="s", now="t")
    assert len(decisions) == 4


def test_parse_notes_skips_summary_field_as_index_line():
    payload = json.dumps(
        {
            "notes": [
                {
                    "decision": "add",
                    "category": "用户偏好",
                    "title": "带摘要",
                    "content": "完整正文内容很长很长。",
                    "summary": "一句话摘要",
                }
            ]
        },
        ensure_ascii=False,
    )
    decisions = parse_notes(payload, source_session="s", now="t")
    assert decisions[0].summary == "一句话摘要"
    # summary defaults to title when absent
    payload2 = json.dumps(
        {
            "notes": [
                {
                    "decision": "add",
                    "category": "用户偏好",
                    "title": "T",
                    "content": "C",
                }
            ]
        },
        ensure_ascii=False,
    )
    d2 = parse_notes(payload2, source_session="s", now="t")
    assert d2[0].summary  # non-empty fallback


# ---------------------------------------------------------------------------
# EXTRACT_SYSTEM prompt content
# ---------------------------------------------------------------------------


def test_extract_system_mentions_categories_and_dedup():
    for cat in ("用户偏好", "纠正反馈", "项目知识", "参考资料"):
        assert cat in EXTRACT_SYSTEM
    # dedup semantics + structured output + no-tools discipline
    assert "新增" in EXTRACT_SYSTEM or "add" in EXTRACT_SYSTEM
    assert "更新" in EXTRACT_SYSTEM or "update" in EXTRACT_SYSTEM
    assert "跳过" in EXTRACT_SYSTEM or "skip" in EXTRACT_SYSTEM
    assert "JSON" in EXTRACT_SYSTEM or "json" in EXTRACT_SYSTEM
    assert "工具" in EXTRACT_SYSTEM


# ---------------------------------------------------------------------------
# extract — end-to-end with fake provider
# ---------------------------------------------------------------------------


def test_extract_disables_tools_and_passes_system():
    provider = FakeProvider(_four_note_payload())
    recent = [_u("你好"), _a("你好，有什么可以帮你？")]
    decisions = extract(
        provider, recent, existing_index="", source_session="s", now="t"
    )
    assert provider.received_tools is None  # tools physically disabled
    assert provider.received_system == EXTRACT_SYSTEM
    assert len(decisions) == 4


def test_extract_feeds_existing_index_to_model():
    provider = FakeProvider(_four_note_payload())
    recent = [_u("hi"), _a("hello")]
    existing = "- 偏好两格缩进: 代码缩进两格"
    extract(provider, recent, existing_index=existing, source_session="s", now="t")
    # the existing index text must appear somewhere in what we sent the model
    sent = json.dumps(provider.received_messages, ensure_ascii=False)
    assert "偏好两格缩进" in sent


def test_extract_dedup_skip_not_appended():
    # model judges the only note as already-covered → no notes to write
    payload = json.dumps(
        {
            "notes": [
                {
                    "decision": "skip",
                    "category": "用户偏好",
                    "title": "已覆盖项",
                    "content": "重复信息",
                }
            ]
        },
        ensure_ascii=False,
    )
    provider = FakeProvider(payload)
    recent = [_u("hi"), _a("hello")]
    decisions = extract(
        provider,
        recent,
        existing_index="- 已覆盖项: 之前记过",
        source_session="s",
        now="t",
    )
    # skip decisions are returned but marked skip — caller (runner) won't write them
    assert all(d.action == "skip" for d in decisions)


def test_extract_returns_empty_on_provider_garbage():
    provider = FakeProvider("完全不是 JSON 的废话回复")
    decisions = extract(
        provider, [_u("hi"), _a("ok")], existing_index="", source_session="s", now="t"
    )
    assert decisions == []


def test_extract_swallows_provider_exception():
    # extract must not raise — parsing/streaming errors → [] (runner also guards)
    decisions = extract(
        BoomProvider(),
        [_u("hi"), _a("ok")],
        existing_index="",
        source_session="s",
        now="t",
    )
    assert decisions == []


# ---------------------------------------------------------------------------
# build_recent_window — only the latest round
# ---------------------------------------------------------------------------


def test_build_recent_window_takes_last_round_only():
    history = [
        _u("第一轮问题"),
        _a("第一轮回答"),
        _u("第二轮问题"),
        _a("第二轮回答"),
    ]
    window = build_recent_window(history)
    blob = json.dumps(window, ensure_ascii=False)
    assert "第二轮问题" in blob
    assert "第二轮回答" in blob
    assert "第一轮问题" not in blob


def test_build_recent_window_includes_tool_activity_summary():
    history = [
        _u("读一下文件"),
        _a("好的，我来读"),
        _t("文件的全部内容，可能非常长……" * 50),
        _a("文件读完了，内容是 X"),
    ]
    window = build_recent_window(history)
    blob = json.dumps(window, ensure_ascii=False)
    # last user message present
    assert "读一下文件" in blob
    # final assistant body present
    assert "文件读完了" in blob


def test_build_recent_window_empty_history():
    assert build_recent_window([]) == []


# ---------------------------------------------------------------------------
# v0.9 review fix — #12: orphan tool messages + non-str content
# ---------------------------------------------------------------------------


def test_build_recent_window_drops_orphan_tool_result():
    """#12: a window that begins with an orphan tool result (no preceding
    assistant tool_calls in the slice) must NOT carry a dangling tool message —
    otherwise the extraction provider may 400 on the unmatched tool_result."""
    # The last user message is followed by a tool result whose originating
    # assistant tool_call is *before* the window cut → orphan inside the slice.
    history = [
        _a("我去读文件"),  # has tool_calls conceptually, but BEFORE the window
        {
            "role": "tool",
            "content": "结果",
            "tool_call_id": "before-window",
        },
        _u("最后的问题"),
        {
            "role": "tool",
            "content": "孤儿结果",
            "tool_call_id": "orphan-1",
        },
        _a("最终回答"),
    ]
    window = build_recent_window(history)
    # No message in the window is a bare orphan tool result.
    assert all(m.get("role") != "tool" for m in window), (
        "orphan tool result must be dropped/folded, not passed through verbatim"
    )
    # The latest user + final assistant body still survive.
    blob = json.dumps(window, ensure_ascii=False)
    assert "最后的问题" in blob
    assert "最终回答" in blob


def test_build_recent_window_handles_non_str_content():
    """#12: a tool/assistant message whose content is a non-str (e.g. a list of
    content blocks) must not crash len()/slicing; it is coerced to str first."""
    history = [
        _u("做点事"),
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "块内容"}],  # non-str content
        },
    ]
    # Must not raise (the old len()/slice on a list would have).
    window = build_recent_window(history)
    blob = json.dumps(window, ensure_ascii=False)
    assert "做点事" in blob
    # every emitted content is a string (safe for both provider conversions)
    assert all(isinstance(m.get("content"), str) for m in window)


def test_build_recent_window_long_non_str_tool_content():
    """#12: a huge non-str tool content is coerced + truncated, not crashed."""
    history = [
        _u("读文件"),
        _a(""),
        {
            "role": "tool",
            "content": ["x"] * 5000,  # non-str, would break len()/slice
            "tool_call_id": "c1",
        },
        _a("读完了"),
    ]
    # The orphan-ness: there is no assistant tool_calls pairing in this slice,
    # so the tool message is an orphan and should be dropped — but crucially it
    # must not crash on the non-str content along the way.
    window = build_recent_window(history)
    assert all(isinstance(m.get("content"), str) for m in window)
    blob = json.dumps(window, ensure_ascii=False)
    assert "读文件" in blob
    assert "读完了" in blob
