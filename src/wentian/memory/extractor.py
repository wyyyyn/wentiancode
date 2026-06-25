"""Memory extraction — four-category notes + LLM dedup decision, sync stream.

v0.9 · C57 · F67/N28/N30 (task T104)

Mirrors the v0.8 ``context.summarizer`` discipline exactly:

- the provider is **duck-typed** (only ``stream`` is used) — no concrete
  provider / registry / loop / agent is imported (zero reverse dependency, N30);
- the request is issued **synchronously** with ``tools=None`` (tools physically
  disabled) and the streamed ``TextDelta`` text is collected in a plain
  ``for`` loop — **no asyncio**;
- the structured parse (:func:`parse_notes`) is a pure, offline-testable
  function; ``extract`` is the thin streaming wrapper around it and never raises
  (parse/stream errors → ``[]`` + a stderr warning; the runner layer also
  guards).

The model is asked to emit, for the *latest round* of conversation, up to four
kinds of notes — **user preferences / corrective feedback / project knowledge / references** — each carrying a
dedup *decision* (``add`` / ``update`` / ``skip``) judged against the existing
INDEX we feed it.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass

from wentian.memory.store import CATEGORIES, Note, default_scope
from wentian.providers.base import Message, TextDelta

__all__ = [
    "EXTRACT_SYSTEM",
    "NoteDecision",
    "parse_notes",
    "extract",
    "build_recent_window",
]


# ---------------------------------------------------------------------------
# Decision wrapper
# ---------------------------------------------------------------------------


@dataclass
class NoteDecision:
    """A parsed note plus the LLM's dedup action and one-line index summary.

    ``action`` is ``add`` / ``update`` / ``skip``; ``summary`` is the one-line
    text destined for the INDEX (never the full note body). The runner writes
    the note + upserts the index for ``add``/``update`` and ignores ``skip``.
    """

    note: Note
    action: str
    summary: str


# ---------------------------------------------------------------------------
# Prompt discipline
# ---------------------------------------------------------------------------

EXTRACT_SYSTEM = (
    "You are a memory extraction assistant. Your sole task is to distill notes worth remembering long-term from the most recent round of conversation.\n"
    "Rules (must not be violated):\n"
    "1. Do not call any tools — this request carries no tool declarations, and you must never attempt to call tools.\n"
    "2. Only summarize information that is truly worth remembering across sessions; if there is none, return an empty list.\n"
    "3. Summarize into four categories, marking each with a category field (value must be one of):\n"
    "   - user_preferences: the user's long-term preferences/habits/conventions (cross-project).\n"
    "   - correction_feedback: corrections the user has made to the assistant, behaviors explicitly not wanted (cross-project).\n"
    "   - project_knowledge: facts/conventions/structure of the current project (project-scoped).\n"
    "   - references: useful links/file locations/external resources (project-scoped).\n"
    "4. Deduplication: I will feed you the existing index (INDEX); for each note determine the decision —\n"
    "   add (brand-new information, add it) / update (same topic already exists, this round has updates) / skip (already covered by existing index, skip it).\n"
    "5. Never write api_key, passwords, tokens, or any other secrets into notes.\n"
    "6. Output must be a JSON object of the form:\n"
    '   {"notes": [{"decision": "add", "category": "user_preferences", "scope": "user", '
    '"title": "…", "content": "…", "summary": "one-line index summary", "tags": ["…"]}]}\n'
    "   You may write an analysis draft first, but the formal result must be the JSON object above (may be wrapped in a ```json code block)."
)

_INSTRUCTION = (
    "Please extract notes worth remembering long-term from the most recent round of conversation, summarize them into four categories, "
    "and determine the decision (add/update/skip) for each note based on the existing index below.\n\n"
    "Existing index (INDEX):\n{index}\n\n"
    "Output only the agreed-upon JSON object (may include an analysis draft, but the formal result is JSON)."
)


# ---------------------------------------------------------------------------
# JSON block extraction + parse
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


def _extract_json_object(text: str) -> dict | None:
    """Pull the notes JSON object out of *text*, tolerating surrounding noise.

    Tries (1) a fenced ```json``` block, then (2) a brace-balanced scan for the
    first ``{…}`` that parses and contains a ``notes`` key. Returns ``None`` when
    nothing usable is found.
    """
    if not text or not text.strip():
        return None

    # 1) fenced code block
    for match in _FENCE_RE.finditer(text):
        obj = _try_load(match.group(1))
        if obj is not None and "notes" in obj:
            return obj

    # 2) brace-balanced scan from each '{'
    for start in (i for i, ch in enumerate(text) if ch == "{"):
        depth = 0
        for end in range(start, len(text)):
            ch = text[end]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    obj = _try_load(text[start : end + 1])
                    if obj is not None and "notes" in obj:
                        return obj
                    break
    return None


def _try_load(blob: str) -> dict | None:
    try:
        obj = json.loads(blob)
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def parse_notes(text: str, *, source_session: str, now: str) -> list[NoteDecision]:
    """Parse model output into :class:`NoteDecision` list (pure, never raises).

    Each note dict must carry ``category`` / ``title`` / ``content``; ``scope``
    defaults from the category attribution, ``decision`` defaults to ``add``,
    ``summary`` defaults to the title, ``tags`` to ``[]``. Anything unparseable
    or schema-invalid is dropped; total failure → ``[]`` with a stderr warning.
    """
    obj = _extract_json_object(text)
    if obj is None:
        return []
    raw_notes = obj.get("notes")
    if not isinstance(raw_notes, list):
        return []

    decisions: list[NoteDecision] = []
    for entry in raw_notes:
        if not isinstance(entry, dict):
            continue
        category = str(entry.get("category", "")).strip()
        title = str(entry.get("title", "")).strip()
        content = str(entry.get("content", "")).strip()
        if category not in CATEGORIES or not title or not content:
            continue
        scope = str(entry.get("scope") or default_scope(category)).strip()
        if scope not in ("user", "project"):
            scope = default_scope(category)
        action = str(entry.get("decision") or "add").strip().lower()
        if action not in ("add", "update", "skip"):
            action = "add"
        tags_raw = entry.get("tags") or []
        tags = [str(t) for t in tags_raw] if isinstance(tags_raw, list) else []
        summary = str(entry.get("summary") or title).strip() or title
        note = Note(
            category=category,
            scope=scope,
            title=title,
            content=content,
            created_at=now,
            source_session=source_session,
            tags=tags,
        )
        decisions.append(NoteDecision(note=note, action=action, summary=summary))
    return decisions


# ---------------------------------------------------------------------------
# Recent-round window
# ---------------------------------------------------------------------------

_TOOL_SUMMARY_CHARS = 200


def build_recent_window(messages: list[Message]) -> list[Message]:
    """Slice the latest round (last user + the assistant body that followed it).

    Walks back to the last ``user`` message and keeps it through the tail. Tool
    activity inside that window is folded into the **preceding assistant** turn's
    text as a short summary (not the full payload) so a huge tool result never
    bloats the extraction request — and so the window carries **no bare ``tool``
    messages**. Dropping the ``tool`` role entirely sidesteps orphan
    tool_result / unmatched tool_use 400s when the slice begins after the
    originating assistant tool_call (review fix #12); both provider conversions
    then see only plain user/assistant text. Non-str content (e.g. content-block
    lists) is coerced to ``str`` before any length/slice (review fix #12). An
    empty history → ``[]``.
    """
    if not messages:
        return []

    # find the last user message index
    last_user = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            last_user = i
            break
    if last_user is None:
        last_user = 0

    window: list[Message] = []
    for msg in messages[last_user:]:
        role = msg.get("role")
        if role == "tool":
            # Fold the tool activity into the previous assistant turn's text
            # rather than emitting a bare (possibly orphan) tool message.
            content = _coerce_str(msg.get("content"))
            if len(content) > _TOOL_SUMMARY_CHARS:
                content = content[:_TOOL_SUMMARY_CHARS] + "…(tool result truncated)"
            note = f"[tool activity summary] {content}"
            if window and window[-1].get("role") == "assistant":
                prev = window[-1]
                prev_text = _coerce_str(prev.get("content"))
                prev["content"] = f"{prev_text}\n{note}".strip()
            else:
                # No assistant to attach to → keep the activity as user-side text
                # (never as a tool message, to avoid orphan tool_result errors).
                window.append({"role": "user", "content": note})
        else:
            window.append({"role": role, "content": _coerce_str(msg.get("content"))})
    return window


def _coerce_str(content: object) -> str:
    """Coerce a message ``content`` to ``str`` (handles None / content-block lists).

    Guards every ``len()`` / slice in :func:`build_recent_window` against non-str
    content (review fix #12). ``None`` → ``""``; everything else → ``str(...)``.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return str(content)


# ---------------------------------------------------------------------------
# extract — sync streaming wrapper (mirrors summarizer.summarize)
# ---------------------------------------------------------------------------


def extract(
    provider,
    recent_messages: list[Message],
    existing_index: str,
    *,
    source_session: str,
    now: str,
) -> list[NoteDecision]:
    """Ask *provider* to extract notes from *recent_messages*; never raises.

    Issues ``provider.stream(recent + [instruction], system=EXTRACT_SYSTEM,
    tools=None)`` synchronously (tools physically disabled, no asyncio), collects
    the ``TextDelta`` text, then parses it via :func:`parse_notes`. Any
    streaming/parse error is swallowed (warned to stderr) and yields ``[]`` — the
    background runner adds a second guard on top.
    """
    instruction = _INSTRUCTION.format(index=existing_index or "(none)")
    request: list[Message] = list(recent_messages) + [
        {"role": "user", "content": instruction}
    ]
    try:
        chunks: list[str] = []
        for event in provider.stream(request, system=EXTRACT_SYSTEM, tools=None):
            if isinstance(event, TextDelta):
                chunks.append(event.text)
        return parse_notes("".join(chunks), source_session=source_session, now=now)
    except Exception as exc:  # noqa: BLE001 - extraction must never escape
        print(f"[memory] extraction failed, skipped: {exc!r}", file=sys.stderr)
        return []
