"""Tests for memory/runner.py: daemon fire-and-forget + swallow + lock + close.

v0.9 · C58 · F67/N31（任务 T105）

Offline tests with a fake provider + a real temp ``MemoryStore``. Thread
assertions cover: ``submit`` returns immediately (non-blocking), extraction
exceptions are swallowed (no crash, messages untouched), each thread builds a
fresh provider (no sharing the conversation provider object), concurrent INDEX
writes stay serialized, and ``close`` joins on a short timeout without leaking
threads or blocking process exit.
"""

from __future__ import annotations

import json
import threading
import time

from wentian.memory.runner import MemoryRunner
from wentian.memory.store import MemoryConfig, MemoryStore
from wentian.providers.base import Done, TextDelta


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _four_note_payload(tag="x"):
    notes = [
        {
            "decision": "add",
            "category": "用户偏好",
            "title": f"偏好-{tag}",
            "content": f"内容 {tag}",
            "summary": f"摘要 {tag}",
            "tags": [tag],
        },
        {
            "decision": "add",
            "category": "项目知识",
            "title": f"知识-{tag}",
            "content": f"项目内容 {tag}",
            "summary": f"项目摘要 {tag}",
        },
    ]
    return (
        "草稿...\n```json\n"
        + json.dumps({"notes": notes}, ensure_ascii=False)
        + "\n```"
    )


class FakeProvider:
    """Replays a fixed payload; can signal when its stream was entered."""

    def __init__(self, payload, *, started_event=None, delay=0.0):
        self._payload = payload
        self._started = started_event
        self._delay = delay

    def stream(self, messages, *, system=None, tools=None):
        if self._started is not None:
            self._started.set()
        if self._delay:
            time.sleep(self._delay)
        yield TextDelta(self._payload)
        yield Done()


class BoomProvider:
    def __init__(self, *, called=None):
        self._called = called

    def stream(self, messages, *, system=None, tools=None):
        if self._called is not None:
            self._called.set()
        raise RuntimeError("extraction boom")
        yield  # pragma: no cover


def _make_store(tmp_path, cfg=None):
    return MemoryStore(
        user_dir=tmp_path / "user" / "memory",
        project_dir=tmp_path / "proj" / ".wentian" / "memory",
        cfg=cfg or MemoryConfig(),
    )


def _recent():
    return [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好，有什么可以帮你？"},
    ]


# ---------------------------------------------------------------------------
# submit — daemon fire-and-forget, lands notes on disk
# ---------------------------------------------------------------------------


def test_submit_writes_notes_and_index_after_join(tmp_path):
    store = _make_store(tmp_path)
    runner = MemoryRunner(
        provider_factory=lambda: FakeProvider(_four_note_payload("a")),
        store=store,
        cfg=MemoryConfig(),
        session_id="sess-1",
    )
    runner.submit(_recent())
    runner.close(timeout=5.0)

    # notes landed by scope/category
    user_dir = tmp_path / "user" / "memory" / "用户偏好"
    proj_dir = tmp_path / "proj" / ".wentian" / "memory" / "项目知识"
    assert any(user_dir.glob("*.md")), "user note not written"
    assert any(proj_dir.glob("*.md")), "project note not written"
    # INDEX updated for both scopes
    assert "偏好-a" in store.read_index("user")
    assert "知识-a" in store.read_index("project")


def test_submit_returns_immediately(tmp_path):
    store = _make_store(tmp_path)
    started = threading.Event()
    # provider sleeps 1s once entered; submit must return well before that
    runner = MemoryRunner(
        provider_factory=lambda: FakeProvider(
            _four_note_payload(), started_event=started, delay=1.0
        ),
        store=store,
        cfg=MemoryConfig(),
        session_id="s",
    )
    t0 = time.monotonic()
    runner.submit(_recent())
    elapsed = time.monotonic() - t0
    assert elapsed < 0.3, f"submit blocked for {elapsed:.2f}s"
    # the worker thread did start running asynchronously
    assert started.wait(timeout=2.0)
    runner.close(timeout=2.0)


# ---------------------------------------------------------------------------
# exception swallowed — no crash, messages untouched
# ---------------------------------------------------------------------------


def test_submit_swallows_exception_and_keeps_messages(tmp_path, capsys):
    store = _make_store(tmp_path)
    called = threading.Event()
    runner = MemoryRunner(
        provider_factory=lambda: BoomProvider(called=called),
        store=store,
        cfg=MemoryConfig(),
        session_id="s",
    )
    recent = _recent()
    snapshot = json.dumps(recent, ensure_ascii=False)
    runner.submit(recent)  # must not raise
    assert called.wait(timeout=2.0)
    runner.close(timeout=2.0)

    # input messages were not mutated by the background extraction
    assert json.dumps(recent, ensure_ascii=False) == snapshot
    # error surfaced to stderr, not raised
    err = capsys.readouterr().err
    assert "memory" in err.lower() or "boom" in err.lower()


# ---------------------------------------------------------------------------
# disabled config — whole chain off
# ---------------------------------------------------------------------------


def test_submit_noop_when_disabled(tmp_path):
    store = _make_store(tmp_path)
    built = []
    runner = MemoryRunner(
        provider_factory=lambda: built.append(1) or FakeProvider(_four_note_payload()),
        store=store,
        cfg=MemoryConfig(enabled=False),
        session_id="s",
    )
    runner.submit(_recent())
    runner.close(timeout=1.0)
    assert built == []  # provider never built, no extraction attempted
    assert store.read_index("user") == ""


# ---------------------------------------------------------------------------
# self-built provider — no sharing the conversation provider object
# ---------------------------------------------------------------------------


def test_each_submit_builds_fresh_provider(tmp_path):
    store = _make_store(tmp_path)
    built_objs = []

    def factory():
        p = FakeProvider(_four_note_payload())
        built_objs.append(p)
        return p

    runner = MemoryRunner(
        provider_factory=factory,
        store=store,
        cfg=MemoryConfig(),
        session_id="s",
    )
    runner.submit(_recent())
    runner.submit(_recent())
    runner.close(timeout=5.0)
    # one fresh provider object per submit — none reused
    assert len(built_objs) == 2
    assert len({id(o) for o in built_objs}) == 2


# ---------------------------------------------------------------------------
# concurrent INDEX writes serialized (store lock), nothing lost
# ---------------------------------------------------------------------------


def test_concurrent_submits_index_intact(tmp_path):
    store = _make_store(tmp_path)
    n = 12

    def factory_for(i):
        return lambda: FakeProvider(_four_note_payload(f"{i:02d}"))

    runner = MemoryRunner(
        provider_factory=lambda: FakeProvider(_four_note_payload()),
        store=store,
        cfg=MemoryConfig(),
        session_id="s",
    )
    for i in range(n):
        runner._provider_factory = factory_for(i)  # vary payload per submit
        runner.submit(_recent())
    runner.close(timeout=10.0)

    user_index = store.read_index("user")
    proj_index = store.read_index("project")
    for i in range(n):
        assert f"偏好-{i:02d}" in user_index, f"user entry {i} lost"
        assert f"知识-{i:02d}" in proj_index, f"project entry {i} lost"


# ---------------------------------------------------------------------------
# close — short join, daemon threads, no leak / no block
# ---------------------------------------------------------------------------


def test_close_short_timeout_returns_fast_with_slow_extraction(tmp_path):
    store = _make_store(tmp_path)
    runner = MemoryRunner(
        provider_factory=lambda: FakeProvider(_four_note_payload(), delay=3.0),
        store=store,
        cfg=MemoryConfig(),
        session_id="s",
    )
    runner.submit(_recent())
    t0 = time.monotonic()
    runner.close(timeout=0.2)  # short join — must not wait the full 3s
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0, f"close blocked {elapsed:.2f}s past its timeout"


def test_worker_threads_are_daemon(tmp_path):
    store = _make_store(tmp_path)
    runner = MemoryRunner(
        provider_factory=lambda: FakeProvider(_four_note_payload(), delay=1.0),
        store=store,
        cfg=MemoryConfig(),
        session_id="s",
    )
    runner.submit(_recent())
    # at least one live worker, and it is a daemon (won't block interpreter exit)
    alive = [t for t in runner._threads if t.is_alive()]
    assert alive
    assert all(t.daemon for t in alive)
    runner.close(timeout=2.0)


def test_close_is_idempotent(tmp_path):
    store = _make_store(tmp_path)
    runner = MemoryRunner(
        provider_factory=lambda: FakeProvider(_four_note_payload()),
        store=store,
        cfg=MemoryConfig(),
        session_id="s",
    )
    runner.submit(_recent())
    runner.close(timeout=2.0)
    runner.close(timeout=2.0)  # second call must not raise


# ---------------------------------------------------------------------------
# v0.9 review fix — #5 AC80: LLM dedup end-to-end (skip decision → no re-append)
# ---------------------------------------------------------------------------


def _skip_payload(title="偏好-a"):
    """A provider payload whose single note is decided 'skip'."""
    notes = [
        {
            "decision": "skip",
            "category": "用户偏好",
            "title": title,
            "content": "等价信息，已被现有索引覆盖",
            "summary": "重复摘要",
        }
    ]
    return "```json\n" + json.dumps({"notes": notes}, ensure_ascii=False) + "\n```"


def test_skip_decision_does_not_append_to_index(tmp_path):
    """AC80: an existing INDEX entry + a 'skip' decision for an equivalent note
    → INDEX line count unchanged end-to-end through the runner (no dup append)."""
    store = _make_store(tmp_path)
    # First extraction adds the note → INDEX has it.
    runner = MemoryRunner(
        provider_factory=lambda: FakeProvider(_four_note_payload("a")),
        store=store,
        cfg=MemoryConfig(),
        session_id="s",
    )
    runner.submit(_recent())
    runner.close(timeout=5.0)
    before = store.read_index("user")
    before_lines = [ln for ln in before.splitlines() if ln.strip()]
    assert any("偏好-a" in ln for ln in before_lines)

    # Second extraction: the model judges the equivalent info 'skip'.
    runner2 = MemoryRunner(
        provider_factory=lambda: FakeProvider(_skip_payload("偏好-a")),
        store=store,
        cfg=MemoryConfig(),
        session_id="s",
    )
    runner2.submit(_recent())
    runner2.close(timeout=5.0)

    after = store.read_index("user")
    after_lines = [ln for ln in after.splitlines() if ln.strip()]
    # skip must not add a line (and the count is exactly preserved).
    assert len(after_lines) == len(before_lines)


# ---------------------------------------------------------------------------
# v0.9 review fix — #4 AC84: no plaintext secret reaches the memory files
# ---------------------------------------------------------------------------


def _secret_payload():
    notes = [
        {
            "decision": "add",
            "category": "参考资料",
            "title": "API 凭证 sk-LEAKED12345678",
            "content": "服务密钥 sk-ABCdef123456789，api_key=plaintexttoken9999",
            "summary": "凭证摘要 sk-SUMMARY9876543",
        }
    ]
    return "```json\n" + json.dumps({"notes": notes}, ensure_ascii=False) + "\n```"


def test_extraction_products_contain_no_plaintext_secret(tmp_path):
    """AC84: even if the model leaks a key, the persisted note file + INDEX hold
    no plaintext sk-/api_key= secret (defense-in-depth redaction in the store)."""
    store = _make_store(tmp_path)
    runner = MemoryRunner(
        provider_factory=lambda: FakeProvider(_secret_payload()),
        store=store,
        cfg=MemoryConfig(),
        session_id="s",
    )
    runner.submit(_recent())
    runner.close(timeout=5.0)

    # Scan every persisted file under both memory roots.
    roots = [tmp_path / "user" / "memory", tmp_path / "proj" / ".wentian" / "memory"]
    persisted = []
    for root in roots:
        for path in root.rglob("*.md"):
            persisted.append(path.read_text(encoding="utf-8"))
    blob = "\n".join(persisted)
    assert blob, "expected at least one persisted note/INDEX"
    assert "sk-LEAKED12345678" not in blob
    assert "sk-ABCdef123456789" not in blob
    assert "sk-SUMMARY9876543" not in blob
    assert "plaintexttoken9999" not in blob
    assert "[REDACTED]" in blob
