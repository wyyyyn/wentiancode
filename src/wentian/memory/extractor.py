"""Memory extraction — four-category notes + LLM dedup decision, sync stream.

v0.9 · C57 · F67/N28/N30（任务 T104）

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
kinds of notes — **用户偏好 / 纠正反馈 / 项目知识 / 参考资料** — each carrying a
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
    "你是记忆抽取助手。你的唯一任务是从最近一轮对话里提炼值得长期记住的笔记。\n"
    "纪律（不可违反）：\n"
    "1. 禁止调用任何工具——本次请求不携带任何工具声明，你也绝不能尝试调用工具。\n"
    "2. 只归纳真正值得跨会话记住的信息；没有则返回空列表。\n"
    "3. 按四类归纳，每条用 category 字段标明所属类（取值必须是其一）：\n"
    "   - 用户偏好：用户长期的口味/习惯/约定（跨项目）。\n"
    "   - 纠正反馈：用户对助手的纠正、明确不要的做法（跨项目）。\n"
    "   - 项目知识：当前项目的事实/约定/结构（项目内）。\n"
    "   - 参考资料：有用的链接/文件位置/外部资料（项目内）。\n"
    "4. 去重：我会把现有索引（INDEX）喂给你；对每条笔记判定 decision——\n"
    "   add（全新信息，新增）/ update（已有同主题、本轮有更新）/ skip（已被现有索引覆盖，跳过）。\n"
    "5. 绝不把 api_key、密码、令牌等任何密钥写进笔记。\n"
    "6. 输出必须是一个 JSON 对象，形如：\n"
    '   {"notes": [{"decision": "add", "category": "用户偏好", "scope": "user", '
    '"title": "…", "content": "…", "summary": "一句话索引摘要", "tags": ["…"]}]}\n'
    "   可以先写一段分析草稿，但正式结果必须是上面那个 JSON 对象（可包在 ```json 代码块里）。"
)

_INSTRUCTION = (
    "请从最近一轮对话里抽取值得长期记住的笔记，按四类归纳，"
    "并结合下面的现有索引判定每条的 decision（add/update/skip）。\n\n"
    "现有索引（INDEX）：\n{index}\n\n"
    "只输出约定的 JSON 对象（可含分析草稿，但正式结果是 JSON）。"
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
    turns inside that window are folded to a short activity summary (not the full
    payload) so a huge tool result never bloats the extraction request. An empty
    history → ``[]``.
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
            content = msg.get("content") or ""
            if len(content) > _TOOL_SUMMARY_CHARS:
                content = content[:_TOOL_SUMMARY_CHARS] + "…（工具结果已截断）"
            summarized: Message = {
                "role": "tool",
                "content": f"[工具活动摘要] {content}",
            }
            if msg.get("tool_call_id"):
                summarized["tool_call_id"] = msg["tool_call_id"]
            window.append(summarized)
        else:
            window.append({"role": role, "content": msg.get("content") or ""})
    return window


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
    instruction = _INSTRUCTION.format(index=existing_index or "（暂无）")
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
