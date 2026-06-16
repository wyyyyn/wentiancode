"""Session persistence layer.

Session: dataclass holding conversation history and metadata.
SessionStore: manages on-disk JSON files in a configurable directory.

Atomic write: write to a .tmp file first, then os.replace() to the final path.
Bad JSON files are skipped with a warning to stderr — never raised to callers.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from wentian.providers.base import Message

__all__ = ["Session", "SessionStore", "default_sessions_dir"]


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
    # Serialization (REFACTOR: centralised here so save/load share logic)
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

    Each session is stored as ``<dir>/<id>.json``.
    The directory is created on first use if it does not exist.

    Args:
        directory: Path to the sessions directory (injected, not hardcoded).
    """

    def __init__(self, directory: Path) -> None:
        self._dir = Path(directory)

    def _ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self._dir / f"{session_id}.json"

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

    def save(self, session: Session) -> None:
        """Persist a session to disk, refreshing updated_at first.

        Uses atomic write: write to a .tmp file, then os.replace() to the
        final path to avoid half-written files on crash.
        """
        self._ensure_dir()
        session.updated_at = _now_iso()
        data = json.dumps(session.to_dict(), ensure_ascii=False, indent=2)
        final_path = self._path(session.id)
        tmp_path = final_path.with_suffix(".tmp")
        try:
            tmp_path.write_text(data, encoding="utf-8")
            os.replace(tmp_path, final_path)
        finally:
            # Clean up tmp file if os.replace failed
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    def load(self, session_id: str) -> Session:
        """Load a session by id.

        Raises:
            FileNotFoundError: if no session file exists for that id.
        """
        path = self._path(session_id)
        if not path.exists():
            raise FileNotFoundError(f"No session file: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return Session.from_dict(data)

    def load_latest(self) -> Session | None:
        """Return the session with the most recent updated_at, or None if empty.

        Bad JSON files are skipped with a warning to stderr.
        Same-second ties resolve arbitrarily — accepted for single-user CLI.
        """
        sessions = self._iter_valid_sessions()
        if not sessions:
            return None
        return max(sessions, key=lambda s: s.updated_at)

    def list(self) -> list[tuple[str, str, str]]:
        """Return a list of (id, updated_at, summary) for all valid sessions.

        summary is the first ~40 chars of the first user message, or '' if
        the session has no user messages.

        Bad JSON files are skipped with a warning to stderr.
        """
        result = []
        for sess in self._iter_valid_sessions():
            summary = self._summary(sess)
            result.append((sess.id, sess.updated_at, summary))
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _iter_valid_sessions(self) -> list[Session]:
        """Load all valid session files, skipping bad ones with stderr warning."""
        if not self._dir.exists():
            return []
        sessions: list[Session] = []
        for path in self._dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                sessions.append(Session.from_dict(data))
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                print(
                    f"[wentian] Warning: skipping malformed session file "
                    f"{path.name}: {exc}",
                    file=sys.stderr,
                )
        return sessions

    @staticmethod
    def _summary(session: Session, max_chars: int = 40) -> str:
        """First user message truncated to max_chars, or '' if none."""
        for msg in session.messages:
            if msg.get("role") == "user":
                text = msg.get("content", "")
                if len(text) <= max_chars:
                    return text
                return text[:max_chars] + "…"
        return ""


# ---------------------------------------------------------------------------
# XDG helper
# ---------------------------------------------------------------------------


def default_sessions_dir() -> Path:
    """Return the default sessions directory, respecting $XDG_DATA_HOME.

    Falls back to ``~/.local/share/wentian/sessions/`` when the env var is
    not set.  The directory is NOT created here — SessionStore handles that.
    """
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        base = Path(xdg)
    else:
        base = Path.home() / ".local" / "share"
    return base / "wentian" / "sessions"
