"""Tests for memory/store.py: Note + frontmatter + INDEX + scoped dirs + caps + lock.

v0.9 · C56 · F68/N30/N31（任务 T103）

Pure offline tests — ``tmp_path`` stands in for the user-home and project-cwd
roots; no real ``~/.config`` / no network. Concurrency assertions use real
threads writing the same INDEX under the store's ``threading.Lock``.
"""

from __future__ import annotations

import threading

import pytest

from wentian.memory.store import (
    MemoryConfig,
    MemoryStore,
    Note,
    read_note,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path, cfg=None):
    user_dir = tmp_path / "user" / "memory"
    project_dir = tmp_path / "project" / ".wentian" / "memory"
    return MemoryStore(
        user_dir=user_dir,
        project_dir=project_dir,
        cfg=cfg or MemoryConfig(),
    )


def _note(
    *,
    category="用户偏好",
    scope="user",
    title="偏好缩进两格",
    content="用户希望代码用两格缩进。",
    created_at="2026-06-19T10:00:00",
    source_session="sess-1",
    tags=None,
):
    return Note(
        category=category,
        scope=scope,
        title=title,
        content=content,
        created_at=created_at,
        source_session=source_session,
        tags=tags if tags is not None else ["python", "style"],
    )


# ---------------------------------------------------------------------------
# write_note → scoped dir + frontmatter
# ---------------------------------------------------------------------------


def test_write_note_user_scope_lands_in_user_dir(tmp_path):
    store = _make_store(tmp_path)
    path = store.write_note(_note(scope="user", category="用户偏好"))
    assert path.exists()
    # user scope under user_dir/<category>/
    assert "user/memory" in path.as_posix()
    assert "用户偏好" in path.as_posix()
    assert path.suffix == ".md"


def test_write_note_project_scope_lands_in_project_dir(tmp_path):
    store = _make_store(tmp_path)
    path = store.write_note(_note(scope="project", category="项目知识"))
    assert path.exists()
    assert ".wentian/memory" in path.as_posix()
    assert "项目知识" in path.as_posix()


def test_write_note_creates_parent_dirs(tmp_path):
    store = _make_store(tmp_path)
    # dirs don't exist beforehand
    path = store.write_note(_note())
    assert path.parent.is_dir()


def test_write_note_has_frontmatter_and_content(tmp_path):
    store = _make_store(tmp_path)
    path = store.write_note(_note(content="正文一行。\n正文两行。"))
    text = path.read_text(encoding="utf-8")
    # YAML frontmatter delimited by ---
    assert text.startswith("---\n")
    assert text.count("---") >= 2
    assert "category:" in text
    assert "正文一行。" in text


# ---------------------------------------------------------------------------
# frontmatter round-trip
# ---------------------------------------------------------------------------


def test_read_note_roundtrip(tmp_path):
    store = _make_store(tmp_path)
    note = _note(
        category="纠正反馈",
        scope="user",
        title="不要用 emoji",
        content="用户纠正：回复里不要带 emoji。",
        created_at="2026-06-19T11:22:33",
        source_session="sess-42",
        tags=["tone", "correction"],
    )
    path = store.write_note(note)
    back = read_note(path)
    assert back.category == note.category
    assert back.title == note.title
    assert back.content.strip() == note.content.strip()
    assert back.created_at == note.created_at
    assert back.source_session == note.source_session
    assert back.tags == note.tags


# ---------------------------------------------------------------------------
# upsert_index / read_index
# ---------------------------------------------------------------------------


def test_upsert_index_add_then_read(tmp_path):
    store = _make_store(tmp_path)
    note = _note(scope="user", title="偏好缩进两格")
    store.upsert_index(note, action="add", summary="代码缩进用两格")
    text = store.read_index("user")
    assert "偏好缩进两格" in text
    assert "代码缩进用两格" in text
    # summary is a one-liner, not the full content
    assert "用户希望代码用两格缩进。" not in text


def test_upsert_index_update_does_not_duplicate(tmp_path):
    store = _make_store(tmp_path)
    note = _note(scope="user", title="偏好缩进两格")
    store.upsert_index(note, action="add", summary="代码缩进用两格")
    store.upsert_index(note, action="update", summary="代码缩进用两格（改用四格）")
    text = store.read_index("user")
    # title appears once, latest summary wins
    assert text.count("偏好缩进两格") == 1
    assert "改用四格" in text


def test_read_index_missing_returns_empty(tmp_path):
    store = _make_store(tmp_path)
    assert store.read_index("user") == ""
    assert store.read_index("project") == ""


# ---------------------------------------------------------------------------
# scoped dir routing by Note.scope (LLM may override default attribution)
# ---------------------------------------------------------------------------


def test_scope_routing_follows_note_scope_not_category(tmp_path):
    store = _make_store(tmp_path)
    # 项目知识 defaults to project, but LLM put it on user scope → store obeys field
    path = store.write_note(_note(category="项目知识", scope="user"))
    assert "user/memory" in path.as_posix()
    assert ".wentian/memory" not in path.as_posix()


# ---------------------------------------------------------------------------
# read_indexes_for_injection — caps (lines / bytes)
# ---------------------------------------------------------------------------


def test_read_indexes_for_injection_joins_both(tmp_path):
    store = _make_store(tmp_path)
    store.upsert_index(
        _note(scope="user", title="U1"), action="add", summary="用户条目"
    )
    store.upsert_index(
        _note(scope="project", title="P1", category="项目知识"),
        action="add",
        summary="项目条目",
    )
    text = store.read_indexes_for_injection()
    assert "U1" in text
    assert "P1" in text


def test_read_indexes_for_injection_empty_when_no_memory(tmp_path):
    store = _make_store(tmp_path)
    assert store.read_indexes_for_injection() == ""


def test_read_indexes_for_injection_caps_lines(tmp_path):
    cfg = MemoryConfig(max_index_lines=10, max_index_bytes=10**9)
    store = _make_store(tmp_path, cfg)
    for i in range(50):
        store.upsert_index(
            _note(scope="user", title=f"T{i}"), action="add", summary=f"摘要{i}"
        )
    text = store.read_indexes_for_injection()
    assert len(text.splitlines()) <= 10


def test_read_indexes_for_injection_caps_bytes(tmp_path):
    cfg = MemoryConfig(max_index_lines=10**9, max_index_bytes=200)
    store = _make_store(tmp_path, cfg)
    for i in range(50):
        store.upsert_index(
            _note(scope="user", title=f"标题{i}"),
            action="add",
            summary="一段相对较长的中文摘要内容用于撑爆字节预算" * 2,
        )
    text = store.read_indexes_for_injection()
    assert len(text.encode("utf-8")) <= 200


# ---------------------------------------------------------------------------
# write lock — concurrent INDEX writes serialized, no torn/interleaved content
# ---------------------------------------------------------------------------


def test_concurrent_index_writes_no_loss(tmp_path):
    store = _make_store(tmp_path)
    n = 40

    def worker(i):
        store.upsert_index(
            _note(scope="user", title=f"COND{i}"),
            action="add",
            summary=f"摘要内容编号{i}",
        )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    text = store.read_index("user")
    # every entry survived the concurrent writes (lock prevents lost updates)
    for i in range(n):
        assert f"COND{i}" in text, f"entry {i} lost under concurrency"


def test_concurrent_write_note_all_persist(tmp_path):
    store = _make_store(tmp_path)
    n = 30
    paths = [None] * n

    def worker(i):
        paths[i] = store.write_note(
            _note(scope="user", title=f"N{i}", content=f"内容{i}")
        )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for i in range(n):
        assert paths[i] is not None and paths[i].exists()


# ---------------------------------------------------------------------------
# MemoryConfig defaults
# ---------------------------------------------------------------------------


def test_memory_config_defaults():
    cfg = MemoryConfig()
    assert cfg.enabled is True
    assert cfg.provider is None
    assert cfg.max_index_lines == 200
    assert cfg.max_index_bytes == 25600


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
