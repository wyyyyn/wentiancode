"""Memory note storage — scoped dirs + frontmatter notes + INDEX + caps + lock.

v0.9 · C56 · F68/N30/N31（任务 T103）

Leaf module: stdlib (``pathlib`` / ``threading`` / ``datetime`` indirectly) +
``pyyaml`` (already a project dependency) for frontmatter + reuse of v0.8
``context.estimator.char_estimate`` to budget the injected index. It imports NO
provider / agent / registry — zero reverse dependency on the orchestration layer
(N30).

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

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from wentian.context.estimator import char_estimate
from wentian.providers.base import Message

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
# Config (self-contained; T106 wires the real Config.memory, duck-compatible)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryConfig:
    """Auto-memory knobs (all optional, defaulted) — mirrors F69 ``memory:`` block.

    Defined here so the memory package is self-contained and offline-testable
    before the top-level ``config.py`` grows its own ``MemoryConfig`` (T106).
    The fields match F69 exactly so the later top-level dataclass is structurally
    (duck-) compatible.
    """

    enabled: bool = True
    provider: str | None = None
    max_index_lines: int = 200
    max_index_bytes: int = 25600


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
    """Serialize a note to ``---\\n<yaml>\\n---\\n<body>``."""
    front = {
        "category": note.category,
        "scope": note.scope,
        "title": note.title,
        "created_at": note.created_at,
        "source_session": note.source_session,
        "tags": list(note.tags),
    }
    yaml_text = yaml.safe_dump(front, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{yaml_text}\n---\n{note.content}\n"


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
    """Filesystem-safe slug from a title (keeps CJK + word chars)."""
    slug = _SLUG_STRIP_RE.sub("-", title).strip("-")
    return slug or "note"


def _index_line(title: str, summary: str) -> str:
    """One INDEX entry: ``- <title>: <summary>`` (one line, never the body)."""
    one_line = " ".join(summary.split())
    return f"- {title}: {one_line}"


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

    # -- scope routing ----------------------------------------------------

    def _scope_dir(self, scope: str) -> Path:
        """Root directory for a scope (``user``/``project``)."""
        return self._user_dir if scope == "user" else self._project_dir

    def _index_path(self, scope: str) -> Path:
        return self._scope_dir(scope) / "INDEX.md"

    # -- note writing -----------------------------------------------------

    def write_note(self, note: Note) -> Path:
        """Write *note* to ``<scope_dir>/<category>/<slug>.md`` (lock-serialized)."""
        category_dir = self._scope_dir(note.scope) / note.category
        path = category_dir / f"{_slug(note.title)}.md"
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

        ``action`` is ``add`` or ``update``; both upsert by title (the latest
        summary wins, the title never duplicates). The INDEX holds only
        one-line summaries, never note bodies.
        """
        scope = note.scope
        new_line = _index_line(note.title, summary)
        path = self._index_path(scope)
        with self._lock:
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            lines = [ln for ln in existing.splitlines() if ln.strip()]
            prefix = f"- {note.title}: "
            replaced = False
            for i, ln in enumerate(lines):
                if ln.startswith(prefix):
                    lines[i] = new_line
                    replaced = True
                    break
            if not replaced:
                lines.append(new_line)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # -- injection --------------------------------------------------------

    def read_indexes_for_injection(self) -> str:
        """User+project INDEX joined and capped to ``max_index_lines``/``_bytes``.

        Reads both scopes' INDEX, concatenates (user first), then truncates to at
        most ``cfg.max_index_lines`` lines AND ``cfg.max_index_bytes`` bytes.
        Returns ``""`` when no memory exists. ``char_estimate`` is reused to keep
        the index within the prompt budget.
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
    encoded length fits). ``char_estimate`` is consulted as the budget proxy but
    the hard byte cap governs the returned slice.
    """
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines]

    # Byte cap: drop trailing lines until it fits (mirrors the line cap's
    # head-keeping behaviour). char_estimate gives a cheap pre-check.
    while lines:
        candidate = "\n".join(lines)
        if len(candidate.encode("utf-8")) <= max_bytes:
            # estimator consulted as the documented budget proxy (N30 reuse)
            char_estimate(
                [Message(role="user", content=candidate)],
            )
            return candidate
        lines = lines[:-1]
    return ""
