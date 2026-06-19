"""Session persistence layer.

v0.9 · C54/C55 · F64/F65/F66（任务 T99/T100/T101/T102）
  Storage format moved from single-file full JSON rewrite to **per-session
  JSONL with append writes**, **cwd-partitioned directories**, **recovery
  hygiene pure functions** and **lazy expired-session pruning**.

Session: dataclass holding conversation history and metadata.
SessionStore: manages on-disk JSONL files in a configurable directory.

JSONL line format (one JSON object per line):
  - optional first line meta: {"type":"meta","id":...,"created_at":...,"provider":...}
  - thereafter one Message per line (providers.base.Message, serialised as-is)

The store never maintains an independent meta file: the id comes from the
filename stem, the title from the first user message line, the message count
from counting non-meta lines, and updated_at from the file mtime.

``save(session)`` is an **atomic full rewrite** of the session's ``.jsonl``
(write to a ``.tmp`` file, then ``os.replace``) — kept so callers that repeatedly
call ``save`` stay correct.  ``append(session, new_messages)`` is the fast path
that appends incremental lines without rewriting the whole file.

Bad JSON lines are skipped with a warning to stderr — never raised to callers.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from wentian.providers.base import Message

__all__ = [
    "Session",
    "SessionStore",
    "default_sessions_dir",
    "project_sessions_dir",
    "prune_expired",
    "resume_gap_reminder",
    "truncate_unpaired",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Current UTC time as an ISO 8601 string (with timezone)."""
    return datetime.now(timezone.utc).isoformat()


def _make_id() -> str:
    """Generate a session id like '20260610-143052-a1b2'."""
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%d-%H%M%S")
    rand = secrets.token_hex(2)  # 4 hex chars
    return f"{ts}-{rand}"


def _encode_line(obj: dict) -> str:
    """Serialise one JSONL record to a single line (no embedded newlines)."""
    return json.dumps(obj, ensure_ascii=False)


def _meta_record(session: "Session") -> dict:
    """Build the optional meta first-line record for a session file."""
    return {
        "type": "meta",
        "id": session.id,
        "created_at": session.created_at,
        "provider": session.provider,
    }


# ---------------------------------------------------------------------------
# Session dataclass
# ---------------------------------------------------------------------------


@dataclass
class Session:
    """A single conversation session with its full message history.

    Attributes:
        id:         Unique identifier, e.g. '20260610-143052-a1b2'.
        created_at: ISO 8601 UTC timestamp when the session was first created.
        updated_at: ISO 8601 UTC timestamp; refreshed by SessionStore.save().
        provider:   Name of the most recently used provider.
        messages:   Full ordered list of conversation turns.
    """

    id: str
    created_at: str
    updated_at: str
    provider: str
    messages: list[Message] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialise to a plain dict suitable for json.dumps."""
        return {
            "id": self.id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "provider": self.provider,
            "messages": list(self.messages),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        """Deserialise from a plain dict (the inverse of to_dict)."""
        return cls(
            id=data["id"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            provider=data["provider"],
            messages=data.get("messages", []),
        )


# ---------------------------------------------------------------------------
# SessionStore
# ---------------------------------------------------------------------------


class SessionStore:
    """Manages Session files in a directory.

    Each session is stored as ``<dir>/<id>.jsonl``.
    The directory is created on first use if it does not exist.

    The directory is injected, not hardcoded.  The assembly layer (cli/repl)
    is responsible for choosing the cwd-partitioned directory via
    :func:`project_sessions_dir`; the store itself stays directory-agnostic.

    Args:
        directory: Path to the sessions directory.
    """

    def __init__(self, directory: Path) -> None:
        self._dir = Path(directory)

    def _ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self._dir / f"{session_id}.jsonl"

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(self, provider: str = "") -> Session:
        """Create and return a new empty Session (not yet persisted)."""
        now = _now_iso()
        return Session(
            id=_make_id(),
            created_at=now,
            updated_at=now,
            provider=provider,
            messages=[],
        )

    def append(self, session: Session, new_messages: list[Message]) -> None:
        """Append incremental messages to the session's JSONL file.

        Appends each message as one JSONL line without rewriting the whole
        file (fast; a crash only loses the last half-written line).  If the
        file does not yet exist, an optional ``meta`` first line is written
        before the messages.

        ``updated_at`` is refreshed on the in-memory session.
        """
        self._ensure_dir()
        path = self._path(session.id)
        new_file = not path.exists()
        lines: list[str] = []
        if new_file:
            lines.append(_encode_line(_meta_record(session)))
        for msg in new_messages:
            lines.append(_encode_line(dict(msg)))
        if lines:
            with path.open("a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        session.updated_at = _now_iso()

    def save(self, session: Session) -> None:
        """Atomically full-rewrite a session's JSONL file.

        Refreshes ``updated_at`` first, then writes the meta line followed by
        every message line to a ``.tmp`` file and ``os.replace``s it into
        place — avoiding half-written files on crash.  Kept so callers that
        repeatedly ``save`` (e.g. the REPL) stay correct.
        """
        self._ensure_dir()
        session.updated_at = _now_iso()
        lines = [_encode_line(_meta_record(session))]
        lines.extend(_encode_line(dict(msg)) for msg in session.messages)
        data = "\n".join(lines) + "\n"
        final_path = self._path(session.id)
        tmp_path = final_path.with_suffix(".tmp")
        try:
            tmp_path.write_text(data, encoding="utf-8")
            os.replace(tmp_path, final_path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    def load(self, session_id: str) -> Session:
        """Load a session by id, parsing its JSONL line by line.

        Bad (unparseable) lines are skipped with a warning to stderr and never
        raised to the caller.  The id is taken from the filename; created_at
        and provider are backfilled from the meta line if present.

        Raises:
            FileNotFoundError: if no session file exists for that id.
        """
        path = self._path(session_id)
        if not path.exists():
            raise FileNotFoundError(f"No session file: {path}")
        meta, messages = self._parse_lines(path)
        mtime = self._mtime_iso(path)
        return Session(
            id=session_id,
            created_at=meta.get("created_at") or mtime,
            updated_at=mtime,
            provider=meta.get("provider", ""),
            messages=messages,
        )

    def load_latest(self) -> Session | None:
        """Return the session with the most recent mtime, or None if empty."""
        files = self._session_files(all_projects=False)
        if not files:
            return None
        newest = max(files, key=lambda p: p.stat().st_mtime)
        return self.load(newest.stem)

    def list(self, *, all_projects: bool = False) -> list[tuple[str, str, str]]:
        """Return ``(id, updated_at, summary)`` tuples, newest first.

        ``all_projects=False`` (default) only scans the current partition
        directory; ``True`` scans every ``projects/*/sessions/`` partition.

        Metadata is derived purely from a lightweight scan: id from the
        filename, updated_at from the file mtime, summary from the first user
        message line.  Files whose lines are all unparseable are skipped.
        Legacy flat ``.json`` files are never listed.
        """
        files = self._session_files(all_projects=all_projects)
        rows: list[tuple[float, tuple[str, str, str]]] = []
        for path in files:
            try:
                mtime = path.stat().st_mtime
                summary = self._scan_summary(path)
            except OSError as exc:
                print(
                    f"[wentian] Warning: skipping unreadable session file "
                    f"{path.name}: {exc}",
                    file=sys.stderr,
                )
                continue
            updated_at = self._iso(mtime)
            rows.append((mtime, (path.stem, updated_at, summary)))
        rows.sort(key=lambda r: r[0], reverse=True)
        return [row for _, row in rows]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _session_files(self, *, all_projects: bool) -> list[Path]:
        """Return the ``.jsonl`` session files in scope (no legacy ``.json``)."""
        if all_projects:
            root = _projects_root(self._dir)
            if root is None or not root.exists():
                # Fall back to the current partition only.
                return self._current_partition_files()
            return sorted(root.glob("*/sessions/*.jsonl"))
        return self._current_partition_files()

    def _current_partition_files(self) -> list[Path]:
        if not self._dir.exists():
            return []
        return sorted(self._dir.glob("*.jsonl"))

    def _parse_lines(self, path: Path) -> tuple[dict, list[Message]]:
        """Parse a JSONL file → (meta_dict, messages), skipping bad lines."""
        meta: dict = {}
        messages: list[Message] = []
        with path.open("r", encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, start=1):
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as exc:
                    print(
                        f"[wentian] Warning: skipping malformed line "
                        f"{lineno} in {path.name}: {exc}",
                        file=sys.stderr,
                    )
                    continue
                if isinstance(obj, dict) and obj.get("type") == "meta":
                    meta = obj
                    continue
                messages.append(obj)
        return meta, messages

    def _scan_summary(self, path: Path, max_chars: int = 40) -> str:
        """First user message (first line, truncated), or '' if none."""
        _, messages = self._parse_lines(path)
        for msg in messages:
            if isinstance(msg, dict) and msg.get("role") == "user":
                text = msg.get("content", "")
                if not isinstance(text, str):
                    continue
                first_line = text.splitlines()[0] if text else ""
                if len(first_line) <= max_chars:
                    return first_line
                return first_line[:max_chars] + "…"
        return ""

    @staticmethod
    def _mtime_iso(path: Path) -> str:
        return SessionStore._iso(path.stat().st_mtime)

    @staticmethod
    def _iso(ts: float) -> str:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Recovery hygiene — pure functions (no provider import; N30 leaf boundary)
# ---------------------------------------------------------------------------


def truncate_unpaired(messages: list[Message]) -> list[Message]:
    """Trim the tail until it is cleanly paired for both provider conversions.

    Two kinds of dangling tail are removed, **looping until the tail is clean**
    (review fix #14 — the prior single-shot version left a second dangling turn):

    - **unpaired assistant tool_call** — an assistant message that requested tool
      calls but isn't followed by all the matching tool results (and any partial
      results after it);
    - **orphan tool result** — a trailing ``tool`` message whose ``tool_call_id``
      has no originating assistant ``tool_calls`` (e.g. a half-written recovery).

    Repeatedly trimming covers the «multiple consecutive unpaired assistant
    turns» and «unpaired turn followed by a mismatched orphan» cases. A
    properly-paired round before the dangling tail is preserved. Already-clean or
    plain-text histories are returned unchanged. The input list is never mutated.
    """
    out = list(messages)
    while True:
        idx = _trailing_dangling_index(out)
        if idx is None:
            return out
        out = out[:idx]


def _trailing_dangling_index(messages: list[Message]) -> int | None:
    """Index from which the tail is dangling (one trim step), or None if clean.

    Walks back from the tail over any tool-result messages, collecting the
    tool_call_ids they answer. The tail is dangling when either:

    - the assistant turn just before those results requested a tool_call id with
      no matching result (unpaired tool_use) → trim from that assistant turn; or
    - the trailing tool results have no originating assistant tool_calls at all
      (orphan tool_result) → trim from the first such tool message.
    """
    n = len(messages)
    i = n - 1
    answered: set[str] = set()
    first_tool_idx: int | None = None
    while i >= 0:
        msg = messages[i]
        role = msg.get("role") if isinstance(msg, dict) else None
        if role == "tool":
            tcid = msg.get("tool_call_id")
            if isinstance(tcid, str):
                answered.add(tcid)
            first_tool_idx = i
            i -= 1
            continue
        if role == "assistant":
            calls = msg.get("tool_calls") if isinstance(msg, dict) else None
            if calls:
                requested = {
                    c.get("id") for c in calls if isinstance(c, dict) and c.get("id")
                }
                if not requested.issubset(answered):
                    return i  # this assistant turn is unpaired → trim from here
                # Fully-answered tool round → tail is clean.
                return None
            # Assistant WITHOUT tool_calls can't originate a tool result: any
            # trailing tool results we walked over are orphans → trim them.
            if first_tool_idx is not None:
                return first_tool_idx
            return None
        # Any other role (e.g. user) sits before the trailing tool results: those
        # results are orphans (no originating assistant tool_calls) → trim them.
        if first_tool_idx is not None:
            return first_tool_idx
        return None
    # Reached the start with only tool results seen → all are orphans.
    if first_tool_idx is not None:
        return first_tool_idx
    return None


def resume_gap_reminder(updated_at: float, now: float, hours: int) -> str | None:
    """Return a one-shot time-gap reminder string, or None.

    If the elapsed time since ``updated_at`` strictly exceeds ``hours``, return
    a human-readable reminder describing how long ago the session was last
    active; otherwise return None.  The caller injects this via the v0.5
    ``<system-reminder>`` channel and never writes it back into persisted
    messages.
    """
    elapsed = now - updated_at
    if elapsed <= hours * 3600:
        return None
    return (
        f"距上次对话已过去约 {_humanize_gap(elapsed)}。"
        "如需文件的最新内容请重新用工具读取，不要照着旧上下文脑补。"
    )


def _humanize_gap(seconds: float) -> str:
    """Render an elapsed duration as a coarse human-readable string."""
    hours = seconds / 3600
    if hours < 24:
        return f"{int(hours)} 小时"
    days = hours / 24
    return f"{int(days)} 天"


# ---------------------------------------------------------------------------
# Expired-session pruning (F66)
# ---------------------------------------------------------------------------


def prune_expired(
    sessions_dir: Path,
    retention_days: int,
    *,
    now: float | None = None,
    exempt_ids: set[str] | None = None,
) -> list[Path]:
    """Delete sessions in ``sessions_dir`` older than ``retention_days``.

    Removes each ``<id>.jsonl`` whose mtime is older than the cutoff together
    with its sibling ``<id>.artifacts/`` directory (v0.8 offload products).
    Deletion failures are not fatal: they are warned to stderr and skipped.
    Only the given (current) partition directory is scanned.

    ``exempt_ids`` (review fix #11) — session ids that must never be pruned even
    when stale (e.g. the very session being resumed/continued this startup), so
    a critically-expired session can't be deleted out from under its own load.

    Returns the list of session file paths that were actually deleted.
    """
    directory = Path(sessions_dir)
    if not directory.exists():
        return []
    if now is None:
        now = datetime.now(timezone.utc).timestamp()
    exempt = exempt_ids or set()
    cutoff = now - retention_days * 86400
    removed: list[Path] = []
    for path in sorted(directory.glob("*.jsonl")):
        if path.stem in exempt:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError as exc:
            print(
                f"[wentian] Warning: cannot stat {path.name}: {exc}",
                file=sys.stderr,
            )
            continue
        if mtime >= cutoff:
            continue
        if _try_delete_session(path):
            removed.append(path)
    return removed


def _try_delete_session(path: Path) -> bool:
    """Delete a session file and its .artifacts/ dir; warn+skip on failure."""
    deleted = False
    try:
        path.unlink()
        deleted = True
    except OSError as exc:
        print(
            f"[wentian] Warning: failed to prune {path.name}: {exc}",
            file=sys.stderr,
        )
    artifacts = path.with_name(f"{path.stem}.artifacts")
    if artifacts.exists():
        try:
            shutil.rmtree(artifacts)
        except OSError as exc:
            print(
                f"[wentian] Warning: failed to prune {artifacts.name}: {exc}",
                file=sys.stderr,
            )
    return deleted


# ---------------------------------------------------------------------------
# Directory helpers (XDG + cwd partitioning)
# ---------------------------------------------------------------------------


def _data_home() -> Path:
    """Return the XDG data home base (``$XDG_DATA_HOME`` or ~/.local/share)."""
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg)
    return Path.home() / ".local" / "share"


def default_sessions_dir() -> Path:
    """Legacy flat sessions directory (kept for backward compatibility).

    Returns ``$XDG_DATA_HOME/wentian/sessions`` or
    ``~/.local/share/wentian/sessions``.  v0.9 partitions sessions per cwd via
    :func:`project_sessions_dir`; this flat path is treated as legacy and is
    never auto-scanned by the partitioned store.
    """
    return _data_home() / "wentian" / "sessions"


def _cwd_slug(cwd: Path) -> str:
    """Slugify an absolute cwd: path separators ``/`` → ``-`` (Claude Code style)."""
    abs_path = Path(cwd).resolve()
    text = abs_path.as_posix()
    return text.replace("/", "-")


def project_sessions_dir(cwd: Path, *, data_home: Path | None = None) -> Path:
    """Return ``<data_home>/wentian/projects/<cwd-slug>/sessions/``.

    ``<cwd-slug>`` is the absolute cwd path with separators replaced by ``-``
    (e.g. ``/Users/yn/proj`` → ``-Users-yn-proj``).  ``data_home`` defaults to
    the XDG data base used by :func:`default_sessions_dir`.
    """
    base = Path(data_home) if data_home is not None else _data_home()
    return base / "wentian" / "projects" / _cwd_slug(cwd) / "sessions"


def _projects_root(sessions_dir: Path) -> Path | None:
    """Given a ``projects/<slug>/sessions`` dir, return the ``projects`` root.

    Returns None if the directory is not laid out as a cwd partition.
    """
    sessions_dir = Path(sessions_dir)
    if sessions_dir.name != "sessions":
        return None
    slug_dir = sessions_dir.parent
    projects_root = slug_dir.parent
    if projects_root.name != "projects":
        return None
    return projects_root
