"""Tests for session.py — Session and SessionStore (JSONL, v0.9).

RED → GREEN → REFACTOR TDD cycle.

v0.9 · C54/C55 · F64/F65/F66（任务 T99/T100/T101/T102）
  Storage format moved from single-file full JSON rewrite to per-session JSONL
  with append writes, cwd-partitioned directories, recovery hygiene pure
  functions and lazy expired-session pruning.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from wentian.session import (
    Session,
    SessionStore,
    default_sessions_dir,
    project_sessions_dir,
    prune_expired,
    resume_gap_reminder,
    truncate_unpaired,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_store(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path)


def _read_lines(path: Path) -> list[dict]:
    """Parse every line of a JSONL file into dicts."""
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ---------------------------------------------------------------------------
# T1: create() returns a Session with id / timestamps; save → file on disk
# ---------------------------------------------------------------------------


class TestCreate:
    def test_create_has_id(self, tmp_path):
        store = make_store(tmp_path)
        sess = store.create()
        assert isinstance(sess.id, str) and sess.id

    def test_create_id_format(self, tmp_path):
        """id looks like YYYYMMDD-HHMMSS-xxxx (timestamp + 4-char random)."""
        store = make_store(tmp_path)
        sess = store.create()
        parts = sess.id.split("-")
        # At least 3 dash-separated parts: date, time, random
        assert len(parts) >= 3, f"Unexpected id format: {sess.id}"

    def test_create_has_timestamps(self, tmp_path):
        store = make_store(tmp_path)
        sess = store.create()
        assert sess.created_at
        assert sess.updated_at

    def test_create_empty_messages(self, tmp_path):
        store = make_store(tmp_path)
        sess = store.create()
        assert sess.messages == []

    def test_create_provider_default(self, tmp_path):
        store = make_store(tmp_path)
        sess = store.create()
        assert sess.provider == ""

    def test_create_provider_custom(self, tmp_path):
        store = make_store(tmp_path)
        sess = store.create(provider="anthropic")
        assert sess.provider == "anthropic"

    def test_save_creates_file(self, tmp_path):
        store = make_store(tmp_path)
        sess = store.create()
        store.save(sess)
        expected = tmp_path / f"{sess.id}.jsonl"
        assert expected.exists()

    def test_directory_created_if_missing(self, tmp_path):
        subdir = tmp_path / "sessions" / "nested"
        store = SessionStore(subdir)
        sess = store.create()
        store.save(sess)
        assert subdir.exists()


# ---------------------------------------------------------------------------
# T2: save → load round-trip equality
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def _session_with_messages(self, store: SessionStore) -> Session:
        sess = store.create(provider="openai")
        sess.messages.append({"role": "user", "content": "Hello"})
        sess.messages.append({"role": "assistant", "content": "Hi there!"})
        return sess

    def test_roundtrip_id(self, tmp_path):
        store = make_store(tmp_path)
        sess = self._session_with_messages(store)
        store.save(sess)
        loaded = store.load(sess.id)
        assert loaded.id == sess.id

    def test_roundtrip_provider(self, tmp_path):
        store = make_store(tmp_path)
        sess = self._session_with_messages(store)
        store.save(sess)
        loaded = store.load(sess.id)
        assert loaded.provider == sess.provider

    def test_roundtrip_messages(self, tmp_path):
        store = make_store(tmp_path)
        sess = self._session_with_messages(store)
        store.save(sess)
        loaded = store.load(sess.id)
        assert loaded.messages == sess.messages

    def test_roundtrip_created_at(self, tmp_path):
        store = make_store(tmp_path)
        sess = self._session_with_messages(store)
        original_created_at = sess.created_at
        store.save(sess)
        loaded = store.load(sess.id)
        assert loaded.created_at == original_created_at

    def test_load_missing_raises(self, tmp_path):
        store = make_store(tmp_path)
        with pytest.raises(FileNotFoundError):
            store.load("nonexistent-id")


# ---------------------------------------------------------------------------
# T99: JSONL append format + meta line + bad-line skip
# ---------------------------------------------------------------------------


class TestJsonlAppend:
    """v0.9 · C54 · F64（任务 T99）"""

    def test_append_writes_message_lines(self, tmp_path):
        """append() writes each new message as a JSONL line, not a full rewrite."""
        store = make_store(tmp_path)
        sess = store.create(provider="anthropic")
        store.append(sess, [{"role": "user", "content": "hi"}])
        path = tmp_path / f"{sess.id}.jsonl"
        assert path.exists()
        lines = _read_lines(path)
        # meta first line + one message line
        message_lines = [ln for ln in lines if ln.get("type") != "meta"]
        assert message_lines == [{"role": "user", "content": "hi"}]

    def test_append_meta_first_line(self, tmp_path):
        """The first line is an optional meta record with id/created_at/provider."""
        store = make_store(tmp_path)
        sess = store.create(provider="openai")
        store.append(sess, [{"role": "user", "content": "x"}])
        path = tmp_path / f"{sess.id}.jsonl"
        first = _read_lines(path)[0]
        assert first["type"] == "meta"
        assert first["id"] == sess.id
        assert first["created_at"] == sess.created_at
        assert first["provider"] == "openai"

    def test_append_is_incremental_not_rewrite(self, tmp_path):
        """Two appends grow the file; the byte prefix is unchanged."""
        store = make_store(tmp_path)
        sess = store.create(provider="anthropic")
        store.append(sess, [{"role": "user", "content": "first"}])
        path = tmp_path / f"{sess.id}.jsonl"
        prefix = path.read_bytes()
        store.append(sess, [{"role": "assistant", "content": "second"}])
        grown = path.read_bytes()
        # File grew, and the previously written bytes are an unchanged prefix.
        assert len(grown) > len(prefix)
        assert grown.startswith(prefix)

    def test_append_line_count_matches(self, tmp_path):
        """Line count = meta line + number of message lines appended."""
        store = make_store(tmp_path)
        sess = store.create(provider="anthropic")
        store.append(sess, [{"role": "user", "content": "a"}])
        store.append(sess, [{"role": "assistant", "content": "b"}])
        store.append(sess, [{"role": "user", "content": "c"}])
        path = tmp_path / f"{sess.id}.jsonl"
        lines = _read_lines(path)
        assert len(lines) == 1 + 3  # meta + 3 messages

    def test_load_parses_jsonl(self, tmp_path):
        """load() reconstructs the Session line by line from JSONL."""
        store = make_store(tmp_path)
        sess = store.create(provider="anthropic")
        store.append(sess, [{"role": "user", "content": "hello"}])
        store.append(sess, [{"role": "assistant", "content": "hi"}])
        loaded = store.load(sess.id)
        assert loaded.id == sess.id
        assert loaded.provider == "anthropic"
        assert loaded.created_at == sess.created_at
        assert loaded.messages == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]

    def test_load_skips_bad_line(self, tmp_path, capsys):
        """A malformed JSON line is skipped with stderr warning, others load."""
        store = make_store(tmp_path)
        sess = store.create(provider="anthropic")
        store.append(sess, [{"role": "user", "content": "good1"}])
        path = tmp_path / f"{sess.id}.jsonl"
        # Inject a broken line in the middle.
        with path.open("a", encoding="utf-8") as fh:
            fh.write("this is not json\n")
        store.append(sess, [{"role": "assistant", "content": "good2"}])
        loaded = store.load(sess.id)  # must not raise
        assert loaded.messages == [
            {"role": "user", "content": "good1"},
            {"role": "assistant", "content": "good2"},
        ]
        captured = capsys.readouterr()
        assert captured.err  # warned about the bad line

    def test_id_from_filename_not_meta(self, tmp_path):
        """ID is derived from the filename even with no meta line."""
        store = make_store(tmp_path)
        path = tmp_path / "20260101-000000-zzzz.jsonl"
        path.write_text(
            json.dumps({"role": "user", "content": "no meta here"}) + "\n",
            encoding="utf-8",
        )
        loaded = store.load("20260101-000000-zzzz")
        assert loaded.id == "20260101-000000-zzzz"
        assert loaded.messages == [{"role": "user", "content": "no meta here"}]

    def test_roundtrip_preserves_tool_pairing_and_offloaded(self, tmp_path):
        """append→load preserves tool messages and the v0.8 offloaded marker."""
        store = make_store(tmp_path)
        sess = store.create(provider="anthropic")
        msgs = [
            {"role": "user", "content": "do it"},
            {
                "role": "assistant",
                "content": "calling",
                "tool_calls": [
                    {"id": "c1", "name": "read", "arguments": {"path": "x"}}
                ],
                "raw_content": [{"type": "tool_use", "id": "c1", "name": "read"}],
            },
            {
                "role": "tool",
                "tool_call_id": "c1",
                "content": "preview…",
                "is_error": False,
                "offloaded": True,
            },
        ]
        store.append(sess, msgs)
        loaded = store.load(sess.id)
        assert loaded.messages == msgs
        assert loaded.messages[2]["offloaded"] is True


# ---------------------------------------------------------------------------
# T3: load_latest() returns newest by updated_at; empty dir → None
# ---------------------------------------------------------------------------


class TestLoadLatest:
    def test_empty_dir_returns_none(self, tmp_path):
        store = make_store(tmp_path)
        assert store.load_latest() is None

    def test_returns_newest(self, tmp_path):
        store = make_store(tmp_path)
        sess_a = store.create()
        store.save(sess_a)
        time.sleep(0.02)  # ensure different mtime
        sess_b = store.create()
        store.save(sess_b)
        latest = store.load_latest()
        assert latest is not None
        assert latest.id == sess_b.id

    def test_single_session_returned(self, tmp_path):
        store = make_store(tmp_path)
        sess = store.create()
        store.save(sess)
        latest = store.load_latest()
        assert latest is not None
        assert latest.id == sess.id


# ---------------------------------------------------------------------------
# T4: list() — scan-derived metadata, bad lines skipped, ordering
# ---------------------------------------------------------------------------


class TestList:
    def test_list_empty_dir(self, tmp_path):
        store = make_store(tmp_path)
        assert store.list() == []

    def test_list_skips_bad_file(self, tmp_path, capsys):
        store = make_store(tmp_path)
        # A file whose every line is broken still does not crash list().
        bad = tmp_path / "20260101-000000-bbbb.jsonl"
        bad.write_text("this is not json\n", encoding="utf-8")
        sess = store.create()
        sess.messages.append({"role": "user", "content": "Hello world"})
        store.save(sess)
        result = store.list()
        ids = [item[0] for item in result]
        assert sess.id in ids  # the valid one survives

    def test_list_returns_id_updated_at_summary(self, tmp_path):
        store = make_store(tmp_path)
        sess = store.create()
        sess.messages.append(
            {"role": "user", "content": "What is the meaning of life?"}
        )
        store.save(sess)
        result = store.list()
        assert len(result) == 1
        item = result[0]
        assert item[0] == sess.id  # id (from filename)
        assert isinstance(item[1], str)  # updated_at rendered as string
        assert isinstance(item[2], str)  # summary

    def test_list_summary_from_first_user_message(self, tmp_path):
        store = make_store(tmp_path)
        sess = store.create()
        sess.messages.append(
            {"role": "assistant", "content": "I'm first but assistant"}
        )
        sess.messages.append({"role": "user", "content": "User message here"})
        store.save(sess)
        result = store.list()
        assert "User message" in result[0][2]

    def test_list_summary_truncated(self, tmp_path):
        store = make_store(tmp_path)
        sess = store.create()
        sess.messages.append({"role": "user", "content": "A" * 200})
        store.save(sess)
        summary = store.list()[0][2]
        assert len(summary) <= 43

    def test_list_empty_session_summary_is_empty_string(self, tmp_path):
        store = make_store(tmp_path)
        sess = store.create()  # no messages
        store.save(sess)
        result = store.list()
        assert result[0][2] == ""

    def test_list_multiple_sessions(self, tmp_path):
        store = make_store(tmp_path)
        store.save(store.create())
        store.save(store.create())
        assert len(store.list()) == 2

    def test_list_ordered_newest_first(self, tmp_path):
        store = make_store(tmp_path)
        a = store.create()
        store.save(a)
        time.sleep(0.02)
        b = store.create()
        store.save(b)
        result = store.list()
        # newest (b) first
        assert result[0][0] == b.id
        assert result[1][0] == a.id


# ---------------------------------------------------------------------------
# T6: to_dict / from_dict serialization
# ---------------------------------------------------------------------------


class TestSerializationMethods:
    def test_to_dict_keys(self, tmp_path):
        store = make_store(tmp_path)
        sess = store.create(provider="openai")
        d = sess.to_dict()
        assert set(d.keys()) == {
            "id",
            "created_at",
            "updated_at",
            "provider",
            "messages",
        }

    def test_from_dict_roundtrip(self, tmp_path):
        store = make_store(tmp_path)
        sess = store.create(provider="openai")
        sess.messages.append({"role": "user", "content": "test"})
        d = sess.to_dict()
        reconstructed = Session.from_dict(d)
        assert reconstructed.id == sess.id
        assert reconstructed.provider == sess.provider
        assert reconstructed.messages == sess.messages
        assert reconstructed.created_at == sess.created_at
        assert reconstructed.updated_at == sess.updated_at


# ---------------------------------------------------------------------------
# T8: F28 tool-message persistence round-trip
# ---------------------------------------------------------------------------


class TestToolMessageRoundTrip:
    """v0.3 · F28（任务 T44）— now carried over JSONL storage."""

    def _build_session(self, store: SessionStore) -> Session:
        sess = store.create(provider="anthropic")
        sess.messages.append({"role": "user", "content": "What is 2+2?"})
        sess.messages.append(
            {
                "role": "assistant",
                "content": "Let me calculate that.",
                "tool_calls": [
                    {
                        "id": "call_abc123",
                        "name": "calculator",
                        "arguments": {"expression": "2+2", "mode": "exact"},
                    }
                ],
                "raw_content": [
                    {"type": "text", "text": "Let me calculate that."},
                    {
                        "type": "tool_use",
                        "id": "call_abc123",
                        "name": "calculator",
                        "input": {"expression": "2+2", "mode": "exact"},
                    },
                ],
            }
        )
        sess.messages.append(
            {
                "role": "tool",
                "tool_call_id": "call_abc123",
                "content": "4",
                "is_error": False,
            }
        )
        sess.messages.append(
            {
                "role": "tool",
                "tool_call_id": "call_abc123",
                "content": "Division by zero",
                "is_error": True,
            }
        )
        sess.messages.append({"role": "assistant", "content": "The answer is 4."})
        return sess

    def test_roundtrip_messages_deep_equal(self, tmp_path):
        store = make_store(tmp_path)
        sess = self._build_session(store)
        original_messages = [dict(m) for m in sess.messages]
        store.save(sess)
        loaded = store.load(sess.id)
        assert loaded.messages == original_messages

    def test_roundtrip_tool_calls_nested_dict(self, tmp_path):
        store = make_store(tmp_path)
        sess = self._build_session(store)
        store.save(sess)
        loaded = store.load(sess.id)
        asst_msg = loaded.messages[1]
        assert asst_msg["tool_calls"][0]["arguments"] == {
            "expression": "2+2",
            "mode": "exact",
        }

    def test_roundtrip_raw_content_nested_dict(self, tmp_path):
        store = make_store(tmp_path)
        sess = self._build_session(store)
        store.save(sess)
        loaded = store.load(sess.id)
        asst_msg = loaded.messages[1]
        assert asst_msg["raw_content"][1]["input"] == {
            "expression": "2+2",
            "mode": "exact",
        }

    def test_roundtrip_tool_message_is_error_true(self, tmp_path):
        store = make_store(tmp_path)
        sess = self._build_session(store)
        store.save(sess)
        loaded = store.load(sess.id)
        error_msg = loaded.messages[3]
        assert error_msg["role"] == "tool"
        assert error_msg["is_error"] is True
        assert error_msg["content"] == "Division by zero"

    def test_roundtrip_message_count(self, tmp_path):
        store = make_store(tmp_path)
        sess = self._build_session(store)
        store.save(sess)
        loaded = store.load(sess.id)
        assert len(loaded.messages) == 5


# ---------------------------------------------------------------------------
# T100: cwd-partitioned directories + list(all_projects=...)
# ---------------------------------------------------------------------------


class TestProjectPartition:
    """v0.9 · C54 · F64（任务 T100）"""

    def test_project_sessions_dir_slug(self, tmp_path):
        cwd = Path("/Users/yn/proj")
        d = project_sessions_dir(cwd, data_home=tmp_path)
        # cwd-slug = absolute cwd with '/' replaced by '-'
        assert d == tmp_path / "wentian" / "projects" / "-Users-yn-proj" / "sessions"

    def test_project_sessions_dir_default_data_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        cwd = Path("/a/b")
        d = project_sessions_dir(cwd)
        assert d == tmp_path / "wentian" / "projects" / "-a-b" / "sessions"

    def test_new_session_lands_in_partition(self, tmp_path):
        cwd = Path("/work/repo")
        sdir = project_sessions_dir(cwd, data_home=tmp_path)
        store = SessionStore(sdir)
        sess = store.create(provider="anthropic")
        store.append(sess, [{"role": "user", "content": "hi"}])
        expected = sdir / f"{sess.id}.jsonl"
        assert expected.exists()
        assert "projects" in str(expected)
        assert "-work-repo" in str(expected)

    def test_list_default_only_current_partition(self, tmp_path):
        cwd_a = Path("/proj/a")
        cwd_b = Path("/proj/b")
        store_a = SessionStore(project_sessions_dir(cwd_a, data_home=tmp_path))
        store_b = SessionStore(project_sessions_dir(cwd_b, data_home=tmp_path))
        sa = store_a.create()
        sa.messages.append({"role": "user", "content": "in A"})
        store_a.save(sa)
        sb = store_b.create()
        sb.messages.append({"role": "user", "content": "in B"})
        store_b.save(sb)
        # store_a default list sees only A's partition
        ids_a = [item[0] for item in store_a.list()]
        assert sa.id in ids_a
        assert sb.id not in ids_a

    def test_list_all_projects_scans_every_partition(self, tmp_path):
        cwd_a = Path("/proj/a")
        cwd_b = Path("/proj/b")
        store_a = SessionStore(project_sessions_dir(cwd_a, data_home=tmp_path))
        store_b = SessionStore(project_sessions_dir(cwd_b, data_home=tmp_path))
        sa = store_a.create()
        store_a.save(sa)
        sb = store_b.create()
        store_b.save(sb)
        ids_all = [item[0] for item in store_a.list(all_projects=True)]
        assert sa.id in ids_all
        assert sb.id in ids_all

    def test_legacy_flat_json_not_listed(self, tmp_path):
        """Old flat ~/.local/share/wentian/sessions/*.json is ignored."""
        cwd = Path("/proj/a")
        sdir = project_sessions_dir(cwd, data_home=tmp_path)
        store = SessionStore(sdir)
        # legacy flat dir alongside the partitioned projects root
        legacy = tmp_path / "wentian" / "sessions"
        legacy.mkdir(parents=True)
        (legacy / "20200101-000000-old0.json").write_text(
            json.dumps(
                {
                    "id": "20200101-000000-old0",
                    "created_at": "2020-01-01T00:00:00+00:00",
                    "updated_at": "2020-01-01T00:00:00+00:00",
                    "provider": "x",
                    "messages": [],
                }
            ),
            encoding="utf-8",
        )
        sess = store.create()
        store.save(sess)
        default_ids = [item[0] for item in store.list()]
        all_ids = [item[0] for item in store.list(all_projects=True)]
        assert "20200101-000000-old0" not in default_ids
        assert "20200101-000000-old0" not in all_ids
        # legacy file untouched
        assert (legacy / "20200101-000000-old0.json").exists()


# ---------------------------------------------------------------------------
# T101: recovery hygiene pure functions — truncate_unpaired / resume_gap_reminder
# ---------------------------------------------------------------------------


class TestTruncateUnpaired:
    """v0.9 · C55 · F65（任务 T101）"""

    def test_trailing_unpaired_assistant_toolcall_is_truncated(self):
        messages = [
            {"role": "user", "content": "do it"},
            {
                "role": "assistant",
                "content": "calling",
                "tool_calls": [{"id": "c1", "name": "read", "arguments": {}}],
            },
        ]
        out = truncate_unpaired(messages)
        # the dangling assistant tool_call turn is removed
        assert out == [{"role": "user", "content": "do it"}]

    def test_paired_history_returned_unchanged(self):
        messages = [
            {"role": "user", "content": "do it"},
            {
                "role": "assistant",
                "content": "calling",
                "tool_calls": [{"id": "c1", "name": "read", "arguments": {}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "ok", "is_error": False},
            {"role": "assistant", "content": "done"},
        ]
        out = truncate_unpaired(messages)
        assert out == messages

    def test_empty_history_unchanged(self):
        assert truncate_unpaired([]) == []

    def test_plain_text_tail_unchanged(self):
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        assert truncate_unpaired(messages) == messages

    def test_does_not_mutate_input(self):
        messages = [
            {"role": "user", "content": "do it"},
            {
                "role": "assistant",
                "content": "calling",
                "tool_calls": [{"id": "c1", "name": "read", "arguments": {}}],
            },
        ]
        original = [dict(m) for m in messages]
        truncate_unpaired(messages)
        assert messages == original  # input list untouched

    def test_partially_paired_tail_truncated(self):
        """Assistant requests two tools, only one result present → truncate turn."""
        messages = [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c1", "name": "read", "arguments": {}},
                    {"id": "c2", "name": "read", "arguments": {}},
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "r1", "is_error": False},
        ]
        out = truncate_unpaired(messages)
        # unpaired assistant turn + its partial results removed
        assert out == [{"role": "user", "content": "go"}]


class TestResumeGapReminder:
    """v0.9 · C55 · F65（任务 T101）"""

    def test_over_threshold_returns_text(self):
        now = 1_000_000.0
        # 5 hours earlier, threshold 4h → reminder
        updated_at = now - 5 * 3600
        msg = resume_gap_reminder(updated_at, now, hours=4)
        assert msg is not None
        assert isinstance(msg, str)
        assert msg  # non-empty

    def test_under_threshold_returns_none(self):
        now = 1_000_000.0
        updated_at = now - 1 * 3600  # 1 hour ago, threshold 4h
        assert resume_gap_reminder(updated_at, now, hours=4) is None

    def test_exactly_at_threshold_returns_none(self):
        now = 1_000_000.0
        updated_at = now - 4 * 3600  # exactly 4h
        # at the boundary: not strictly over → None
        assert resume_gap_reminder(updated_at, now, hours=4) is None

    def test_threshold_configurable(self):
        now = 1_000_000.0
        updated_at = now - 2 * 3600  # 2h ago
        assert resume_gap_reminder(updated_at, now, hours=1) is not None
        assert resume_gap_reminder(updated_at, now, hours=4) is None


# ---------------------------------------------------------------------------
# T102: prune_expired — delete stale sessions and their .artifacts/
# ---------------------------------------------------------------------------


class TestPruneExpired:
    """v0.9 · C55 · F66（任务 T102）"""

    def _aged_session(self, store: SessionStore, tmp_path: Path, age_days: float):
        sess = store.create(provider="anthropic")
        sess.messages.append({"role": "user", "content": "x"})
        store.save(sess)
        path = store._path(sess.id)
        old = time.time() - age_days * 86400
        os.utime(path, (old, old))
        return sess, path

    def test_deletes_expired_keeps_fresh(self, tmp_path):
        store = make_store(tmp_path)
        old_sess, old_path = self._aged_session(store, tmp_path, age_days=40)
        fresh = store.create()
        fresh.messages.append({"role": "user", "content": "y"})
        store.save(fresh)
        removed = prune_expired(tmp_path, retention_days=30)
        assert old_path in removed
        assert not old_path.exists()
        assert store._path(fresh.id).exists()

    def test_deletes_artifacts_dir(self, tmp_path):
        store = make_store(tmp_path)
        old_sess, old_path = self._aged_session(store, tmp_path, age_days=40)
        artifacts = tmp_path / f"{old_sess.id}.artifacts"
        artifacts.mkdir()
        (artifacts / "blob.txt").write_text("big output", encoding="utf-8")
        prune_expired(tmp_path, retention_days=30)
        assert not old_path.exists()
        assert not artifacts.exists()

    def test_retention_days_configurable(self, tmp_path):
        store = make_store(tmp_path)
        _, path = self._aged_session(store, tmp_path, age_days=10)
        # 10-day-old session not expired under 30, expired under 5
        assert prune_expired(tmp_path, retention_days=30) == []
        assert path.exists()
        removed = prune_expired(tmp_path, retention_days=5)
        assert path in removed
        assert not path.exists()

    def test_now_injectable(self, tmp_path):
        store = make_store(tmp_path)
        sess = store.create()
        store.save(sess)
        path = store._path(sess.id)
        mtime = path.stat().st_mtime
        # now far in the future → session counts as expired
        future = mtime + 100 * 86400
        removed = prune_expired(tmp_path, retention_days=30, now=future)
        assert path in removed

    def test_failure_is_not_fatal(self, tmp_path, capsys, monkeypatch):
        store = make_store(tmp_path)
        _, old_path = self._aged_session(store, tmp_path, age_days=40)
        fresh = store.create()
        store.save(fresh)
        os.utime(store._path(fresh.id), None)  # touch → fresh mtime

        import wentian.session as session_mod

        real_unlink = Path.unlink

        def boom(self, *a, **k):
            if self == old_path:
                raise PermissionError("cannot delete")
            return real_unlink(self, *a, **k)

        monkeypatch.setattr(Path, "unlink", boom)
        # must not raise even though deleting old_path fails
        prune_expired(tmp_path, retention_days=30)
        captured = capsys.readouterr()
        assert captured.err  # warned
        # The error did not abort the program.
        assert session_mod  # sanity

    def test_empty_dir_returns_empty_list(self, tmp_path):
        assert prune_expired(tmp_path, retention_days=30) == []


# ---------------------------------------------------------------------------
# T7: default_sessions_dir() module helper (legacy flat path; kept for compat)
# ---------------------------------------------------------------------------


class TestDefaultSessionsDir:
    def test_returns_path(self):
        d = default_sessions_dir()
        assert isinstance(d, Path)

    def test_uses_xdg_data_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        d = default_sessions_dir()
        assert d == tmp_path / "wentian" / "sessions"

    def test_fallback_to_home_local_share(self, monkeypatch):
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        d = default_sessions_dir()
        home = Path.home()
        assert d == home / ".local" / "share" / "wentian" / "sessions"
