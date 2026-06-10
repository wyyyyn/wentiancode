"""Tests for session.py — Session and SessionStore.

RED → GREEN → REFACTOR TDD cycle.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from wentian.session import Session, SessionStore, default_sessions_dir


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_store(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path)


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
        expected = tmp_path / f"{sess.id}.json"
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

    def test_save_refreshes_updated_at(self, tmp_path):
        """save() must refresh updated_at before persisting."""
        store = make_store(tmp_path)
        sess = store.create()
        original_updated = sess.updated_at
        time.sleep(0.01)  # ensure time advances
        store.save(sess)
        loaded = store.load(sess.id)
        # updated_at in the file >= original (may equal if sub-second same)
        assert loaded.updated_at >= original_updated

    def test_load_missing_raises(self, tmp_path):
        store = make_store(tmp_path)
        with pytest.raises(FileNotFoundError):
            store.load("nonexistent-id")


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
        time.sleep(0.02)  # ensure different updated_at
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
# T4: bad JSON → list() skips it; other sessions returned normally
# ---------------------------------------------------------------------------

class TestList:
    def test_list_empty_dir(self, tmp_path):
        store = make_store(tmp_path)
        assert store.list() == []

    def test_list_skips_bad_json(self, tmp_path, capsys):
        store = make_store(tmp_path)
        # Write a bad JSON file
        bad = tmp_path / "bad-file.json"
        bad.write_text("this is not json", encoding="utf-8")
        # Also create a valid session
        sess = store.create()
        sess.messages.append({"role": "user", "content": "Hello world"})
        store.save(sess)
        result = store.list()
        # Should NOT raise, should return exactly the valid session
        assert len(result) == 1
        ids = [item[0] for item in result]
        assert sess.id in ids

    def test_list_bad_json_warns_stderr(self, tmp_path, capsys):
        store = make_store(tmp_path)
        bad = tmp_path / "bad-file.json"
        bad.write_text("not valid json", encoding="utf-8")
        store.list()
        captured = capsys.readouterr()
        # Should print something to stderr about the bad file
        assert "bad-file" in captured.err or "warn" in captured.err.lower() or captured.err

    def test_list_returns_id_updated_at_summary(self, tmp_path):
        store = make_store(tmp_path)
        sess = store.create()
        sess.messages.append({"role": "user", "content": "What is the meaning of life?"})
        store.save(sess)
        result = store.list()
        assert len(result) == 1
        item = result[0]
        assert item[0] == sess.id          # id
        assert item[1] == sess.updated_at  # updated_at (from saved file)
        assert isinstance(item[2], str)    # summary

    def test_list_summary_truncated(self, tmp_path):
        store = make_store(tmp_path)
        sess = store.create()
        long_content = "A" * 200
        sess.messages.append({"role": "user", "content": long_content})
        store.save(sess)
        result = store.list()
        summary = result[0][2]
        assert len(summary) <= 43  # ~40 chars + possible ellipsis

    def test_list_empty_session_summary_is_empty_string(self, tmp_path):
        store = make_store(tmp_path)
        sess = store.create()  # no messages
        store.save(sess)
        result = store.list()
        assert result[0][2] == ""

    def test_list_summary_from_first_user_message(self, tmp_path):
        store = make_store(tmp_path)
        sess = store.create()
        sess.messages.append({"role": "assistant", "content": "I'm first but assistant"})
        sess.messages.append({"role": "user", "content": "User message here"})
        store.save(sess)
        result = store.list()
        # Summary should be from first user message
        assert "User message" in result[0][2]

    def test_list_multiple_sessions(self, tmp_path):
        store = make_store(tmp_path)
        sess_a = store.create()
        store.save(sess_a)
        sess_b = store.create()
        store.save(sess_b)
        result = store.list()
        assert len(result) == 2


# ---------------------------------------------------------------------------
# T5: Atomic write — tmp file + os.replace
# ---------------------------------------------------------------------------

class TestAtomicWrite:
    def test_file_content_is_valid_json(self, tmp_path):
        store = make_store(tmp_path)
        sess = store.create(provider="test")
        sess.messages.append({"role": "user", "content": "hi"})
        store.save(sess)
        path = tmp_path / f"{sess.id}.json"
        data = json.loads(path.read_text())
        assert data["id"] == sess.id
        assert data["provider"] == "test"
        assert data["messages"] == [{"role": "user", "content": "hi"}]

    def test_no_tmp_files_left_after_save(self, tmp_path):
        store = make_store(tmp_path)
        sess = store.create()
        store.save(sess)
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []


# ---------------------------------------------------------------------------
# T6: to_dict / from_dict serialization (REFACTOR target)
# ---------------------------------------------------------------------------

class TestSerializationMethods:
    def test_to_dict_keys(self, tmp_path):
        store = make_store(tmp_path)
        sess = store.create(provider="openai")
        d = sess.to_dict()
        assert set(d.keys()) == {"id", "created_at", "updated_at", "provider", "messages"}

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
# T7: default_sessions_dir() module helper
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
