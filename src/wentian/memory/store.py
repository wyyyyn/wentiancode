"""Memory note storage — scoped dirs + frontmatter notes + INDEX + caps + lock.

v0.9 · C56 · F68/N30/N31（任务 T103）

Leaf module: stdlib (``hashlib`` / ``os`` / ``re`` / ``pathlib`` / ``threading``)
+ ``pyyaml`` (already a project dependency) for frontmatter. The injected index
is budgeted by a hard line/byte cap. It imports NO provider / agent / registry /
estimator — zero reverse dependency on the orchestration layer (N30).

Notes land under one of two roots:

- **user scope** ``~/.config/wentian/memory/`` — cross-project preferences /
  corrections (``用户偏好`` / ``纠正反馈`` by default).
- **project scope** ``<cwd>/.wentian/memory/`` — project knowledge / references
  (``项目知识`` / ``参考资料`` by default).

The default category→scope attribution is just that — a *default*; the LLM may
re-attribute, so :func:`default_scope` is advisory and the store always obeys the
concrete ``Note.scope`` field when routing to disk.

Each note is one ``<scope_dir>/<category>/<slug>.md`` file: a YAML frontmatter
block (category / scope / title / created_at / source_session / tags) followed by
the note body. Each scope keeps one ``INDEX.md`` of one-line summaries (title +
a sentence, never the full body) for cheap startup injection into the
``长期记忆`` system-prompt slot.

All write operations (note + index) are serialized through a single
``threading.Lock`` so the background daemon thread can write the INDEX
concurrently with other writes without losing updates or tearing lines (N31).

Secrets discipline (N32): the store only ever writes the fields of a ``Note``
handed to it — it never reads or persists api_keys or any provider credentials.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# v0.9 · C59 · F69（任务 T106）—— MemoryConfig 的唯一权威在 config.py。
# memory→config 的类型 import（仿 context→providers.base 的叶子契约 import）：
# config.py 不反向依赖 memory，无环。本地不再定义重复的 MemoryConfig。
from wentian.config import MemoryConfig

__all__ = [
    "MemoryConfig",
    "Note",
    "MemoryStore",
    "read_note",
    "default_scope",
    "CATEGORIES",
]

# The four note categories (F67/F68).
CATEGORIES = ("用户偏好", "纠正反馈", "项目知识", "参考资料")

# Default category → scope attribution (LLM may override per-note).
_DEFAULT_SCOPE = {
    "用户偏好": "user",
    "纠正反馈": "user",
    "项目知识": "project",
    "参考资料": "project",
}


def default_scope(category: str) -> str:
    """Advisory default scope for a category (``user``/``project``).

    Unknown categories fall back to ``project`` (the more local, less global
    bucket) — a conservative default that keeps unfamiliar memories from
    leaking across every project.
    """
    return _DEFAULT_SCOPE.get(category, "project")


# ---------------------------------------------------------------------------
# Secret redaction (N32 defense in depth) — v0.9 review fix #4 / AC84
# ---------------------------------------------------------------------------

#: Patterns that look like leaked credentials. The extraction prompt already
#: tells the model never to emit keys (EXTRACT_SYSTEM rule 5); this regex pass
#: is a last-resort net so a slip never reaches disk in the memory/session files.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # OpenAI / Anthropic style keys: sk-..., sk-ant-...
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    # api_key / api-key / apikey : <value> or = <value>
    re.compile(r"(?i)api[_-]?key\s*[:=]\s*\S+"),
    # Authorization: Bearer <token>
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{8,}"),
)

_REDACTED = "[REDACTED]"


def _redact_secrets(text: str) -> str:
    """Replace anything matching a secret pattern with ``[REDACTED]``.

    N32 defense in depth: never persist api_keys / tokens to the memory or
    session files even if the model ignores the prompt discipline.
    """
    if not text:
        return text
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    return text


# ---------------------------------------------------------------------------
# Note
# ---------------------------------------------------------------------------


@dataclass
class Note:
    """A single extracted memory note.

    ``scope`` (``user``/``project``) decides which root the note lands under;
    ``category`` is one of :data:`CATEGORIES`. ``content`` is the note body
    (markdown); the rest become frontmatter.
    """

    category: str
    scope: str
    title: str
    content: str
    created_at: str
    source_session: str
    tags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Frontmatter encode / decode
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)


def _encode_note(note: Note) -> str:
    """Serialize a note to ``---\\n<yaml>\\n---\\n<body>``.

    Every persisted field is run through :func:`_redact_secrets` (N32 defense in
    depth) so a leaked api_key / token never reaches disk in title, tags or body.
    """
    front = {
        "category": note.category,
        "scope": note.scope,
        "title": _redact_secrets(note.title),
        "created_at": note.created_at,
        "source_session": note.source_session,
        "tags": [_redact_secrets(t) for t in note.tags],
    }
    yaml_text = yaml.safe_dump(front, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{yaml_text}\n---\n{_redact_secrets(note.content)}\n"


def read_note(path: Path) -> Note:
    """Parse a note ``.md`` file back into a :class:`Note` (frontmatter round-trip)."""
    text = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError(f"missing frontmatter in note file: {path}")
    front = yaml.safe_load(match.group(1)) or {}
    body = match.group(2)
    tags = front.get("tags") or []
    if not isinstance(tags, list):
        tags = [tags]
    return Note(
        category=str(front.get("category", "")),
        scope=str(front.get("scope", "")),
        title=str(front.get("title", "")),
        content=body,
        created_at=str(front.get("created_at", "")),
        source_session=str(front.get("source_session", "")),
        tags=[str(t) for t in tags],
    )


# ---------------------------------------------------------------------------
# Slug + INDEX line helpers
# ---------------------------------------------------------------------------

_SLUG_STRIP_RE = re.compile(r"[^\w一-鿿-]+")


def _slug(title: str) -> str:
    """Filesystem-safe slug from a title (keeps CJK + word chars).

    A short hash suffix of the **full title** is always appended so two distinct
    titles that strip/collapse to the same readable base (e.g. ``"a b"`` and
    ``"a/b"`` → ``a-b``) never share one file and clobber each other's data
    (review fix #9). The same title always maps to the same slug, so an
    ``update`` of an existing note still overwrites its own file.
    """
    base = _SLUG_STRIP_RE.sub("-", title).strip("-") or "note"
    digest = hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]
    return f"{base}-{digest}"


# ---------------------------------------------------------------------------
# MemoryStore
# ---------------------------------------------------------------------------


class MemoryStore:
    """Reads/writes scoped notes + per-scope INDEX, serialized by one lock."""

    def __init__(
        self,
        *,
        user_dir: Path,
        project_dir: Path,
        cfg: MemoryConfig | None = None,
    ) -> None:
        self._user_dir = Path(user_dir)
        self._project_dir = Path(project_dir)
        self._cfg = cfg or MemoryConfig()
        self._lock = threading.Lock()

    # -- 公有只读属性 ---------------------------------------------------------

    @property
    def user_dir(self) -> Path:
        """用户级记忆根目录（只读）。"""
        return self._user_dir

    @property
    def project_dir(self) -> Path:
        """项目级记忆根目录（只读）。"""
        return self._project_dir

    # -- scope routing ----------------------------------------------------

    def _scope_dir(self, scope: str) -> Path:
        """Root directory for a scope (``user``/``project``)."""
        return self._user_dir if scope == "user" else self._project_dir

    def _index_path(self, scope: str) -> Path:
        return self._scope_dir(scope) / "INDEX.md"

    # -- note writing -----------------------------------------------------

    def write_note(self, note: Note) -> Path:
        """Write *note* to ``<scope_dir>/<category>/<slug>.md`` (lock-serialized).

        The slug derives from the **redacted** title so a leaked secret never
        even lands in the filename (N32 defense in depth, review fix #4).
        """
        category_dir = self._scope_dir(note.scope) / note.category
        path = category_dir / f"{_slug(_redact_secrets(note.title))}.md"
        payload = _encode_note(note)
        with self._lock:
            category_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8")
        return path

    # -- INDEX read/write -------------------------------------------------

    def read_index(self, scope: str) -> str:
        """Read ``<scope_dir>/INDEX.md`` (returns ``""`` when absent)."""
        path = self._index_path(scope)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def upsert_index(self, note: Note, *, action: str, summary: str) -> None:
        """Add/update *note*'s one-line entry in its scope INDEX (lock-serialized).

        ``action`` is ``add`` or ``update``; both upsert by **(category, title)**
        (the latest summary wins) — keying on title alone made two same-titled
        notes in different categories overwrite each other's line (review fix #8).
        Summaries are redacted (N32) and written atomically via tmp + os.replace
        so a concurrent INDEX read never sees a torn file (review fix #10).
        The INDEX holds only one-line summaries, never note bodies.
        """
        scope = note.scope
        title = _redact_secrets(note.title)
        one_line = " ".join(_redact_secrets(summary).split())
        # Dedup key includes the category so cross-category same-title notes coexist.
        prefix = f"- [{note.category}] {title}: "
        new_line = f"{prefix}{one_line}"
        path = self._index_path(scope)
        with self._lock:
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            lines = [ln for ln in existing.splitlines() if ln.strip()]
            replaced = False
            for i, ln in enumerate(lines):
                if ln.startswith(prefix):
                    lines[i] = new_line
                    replaced = True
                    break
            if not replaced:
                lines.append(new_line)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._atomic_write(path, "\n".join(lines) + "\n")

    @staticmethod
    def _atomic_write(path: Path, data: str) -> None:
        """Write *data* to *path* atomically (tmp file + ``os.replace``).

        Mirrors ``session.save``: a concurrent reader sees either the old file or
        the fully-written new one, never a half-written line (review fix #10/N31).
        """
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp_path.write_text(data, encoding="utf-8")
            os.replace(tmp_path, path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    # -- injection --------------------------------------------------------

    def read_indexes_for_injection(self) -> str:
        """User+project INDEX joined and capped to ``max_index_lines``/``_bytes``.

        Reads both scopes' INDEX, concatenates (user first), then truncates to at
        most ``cfg.max_index_lines`` lines AND ``cfg.max_index_bytes`` bytes.
        Returns ``""`` when no memory exists.
        """
        parts = [self.read_index("user").strip(), self.read_index("project").strip()]
        joined = "\n".join(p for p in parts if p)
        if not joined:
            return ""
        return _cap_index(
            joined,
            max_lines=self._cfg.max_index_lines,
            max_bytes=self._cfg.max_index_bytes,
        )


def _cap_index(text: str, *, max_lines: int, max_bytes: int) -> str:
    """Truncate *text* to at most *max_lines* lines and *max_bytes* bytes.

    Line cap first (keep the head), then byte cap (drop trailing lines until the
    encoded length fits). The hard byte cap is the real budget (review fix #13 —
    the prior ``char_estimate`` call's return value was discarded, a misleading
    no-op; the byte/line caps below are the actual budget).
    """
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines]

    # Byte cap: drop trailing lines until it fits (mirrors the line cap's
    # head-keeping behaviour).
    while lines:
        candidate = "\n".join(lines)
        if len(candidate.encode("utf-8")) <= max_bytes:
            return candidate
        lines = lines[:-1]
    return ""
