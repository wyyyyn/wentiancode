"""Second-layer (heavy) compaction — clean cut boundary + eight-section summary.

v0.8 · C49 · F58/F59/F60 (task T93)

When the estimated conversation total approaches the window ceiling, the heavy
layer asks the current backend to compress the *earlier* messages into one
structured summary while keeping the recent tail verbatim. This module provides
the three pure building blocks the orchestrator (C50 Compactor) composes:

- :func:`find_cut_index` — pick a clean cut point so the kept tail meets a token
  budget AND a minimum message count, never starting on a (orphan) tool result,
  never splitting an ``assistant(tool_calls) ↔ its tool results`` pair.
- :func:`summarize` — issue the summary request with tools **physically
  disabled** (``tools=None``), collect the streamed text, extract the
  ``<final_summary>`` body (tolerating a missing tag), raise on empty.
- :func:`build_compacted` — fold the summary + a re-read boundary notice into a
  single ``user`` message and prepend it to the kept tail.

Pairing rule of thumb (verified against ``providers/anthropic.py``
``_convert_messages``, lines ~170-218): consecutive ``tool`` turns collapse into
one ``user`` message of ``tool_result`` blocks, and every ``tool_result`` carries
a ``tool_use_id`` that MUST pair with a ``tool_use`` in a preceding assistant
turn. Hence the keep segment must never begin with an orphan ``tool`` result, and
an assistant's tool calls must travel together with all their results — the snap
loop below enforces both.

context7 verification (Anthropic Messages API · consecutive same-role tolerance)
-----------------------------------------------------------
Verification conclusion: The Anthropic official SDK documentation describes ``messages`` as "the model is trained to operate on *alternating*
user/assistant turns" — this is a description of *training tendency*, **not a hard rejection**. Actual API behavior: consecutive same-role
messages are **tolerated** and **merged into the same turn** on the server side; the only hard constraints are "the first message must be
user" and "each tool_result must be paired with a preceding tool_use."
Strategy adopted: **default implementation** — snap only skips orphan ``tool`` results (``role == "tool"``), the first message of the kept
segment can be ``user`` or ``assistant``. Since F60 has already merged "summary + boundary" into a **single user message**, even if the
first message of the adjacent kept segment is also user, two consecutive user messages will be merged by the server without error, so
there is **no need** for a fallback to continue snapping the cut to the next assistant.

Leaf module: stdlib only (``re``) + ``wentian.config.ContextConfig`` +
``wentian.providers.base`` types. The provider is duck-typed (only ``stream`` is
used) — no concrete provider is imported. Never imports rich / agent.
"""

from __future__ import annotations

import re

from wentian.config import ContextConfig
from wentian.context.estimator import char_estimate
from wentian.providers.base import Message, TextDelta

__all__ = [
    "SummaryError",
    "SUMMARY_SYSTEM",
    "SUMMARY_INSTRUCTION",
    "find_cut_index",
    "summarize",
    "build_compacted",
]


class SummaryError(Exception):
    """Raised when the backend produces no usable summary text."""


# ---------------------------------------------------------------------------
# Cut-boundary selection
# ---------------------------------------------------------------------------


def find_cut_index(messages: list[Message], *, cfg: ContextConfig) -> int:
    """Pick the index where the kept tail begins (``messages[cut:]`` is kept).

    From the tail, accumulate each message's :func:`char_estimate` until the
    running total reaches ``cfg.recent_keep_tokens`` **and** the tail holds at
    least ``cfg.recent_keep_min_messages`` messages — that yields the initial
    cut. Then *snap* forward past any orphan ``tool`` result so the keep segment
    never begins on a bare ``tool_result`` (which would have no paired
    ``tool_use``). Finally *clamp* to 0 (summarize nothing) when the history is
    too short or the keep target already covers the whole history.
    """
    n = len(messages)
    # Clamp: not enough history to bother summarizing.
    if n <= cfg.recent_keep_min_messages:
        return 0

    # Walk back from the tail accumulating tokens until BOTH targets are met.
    cut = n
    accumulated = 0
    kept_count = 0
    for index in range(n - 1, -1, -1):
        accumulated += char_estimate(
            [messages[index]], char_per_token=cfg.char_per_token
        )
        kept_count += 1
        cut = index
        if (
            accumulated >= cfg.recent_keep_tokens
            and kept_count >= cfg.recent_keep_min_messages
        ):
            break

    # Clamp: keep target covers the whole history → nothing to summarize.
    if cut <= 0:
        return 0

    # Snap: push orphan tool results (no paired tool_use in the keep segment)
    # into the summary region so the keep head is never a bare tool_result.
    while cut < n and messages[cut].get("role") == "tool":
        cut += 1

    # Snap may have consumed the whole history → nothing left to keep.
    if cut >= n:
        return 0

    return cut


# ---------------------------------------------------------------------------
# Summary prompt discipline
# ---------------------------------------------------------------------------

SUMMARY_SYSTEM = (
    "You are a conversation compaction assistant. Your sole task is to compress earlier conversation into a structured summary."
    "Discipline (must not be violated):\n"
    "1. Do not call any tools — this request carries no tool declarations, and you must never attempt to call tools.\n"
    "2. First write an analysis draft (organize the conversation thread, locate key information), then write the formal summary;"
    " discard the draft after use, and only use the formal summary section in the end.\n"
    "3. The formal summary must be fully enclosed in <final_summary>…</final_summary> tags.\n"
    "4. The formal summary is organized into eight fixed sections; the main intent section should quote the user's original words"
    " as much as possible without paraphrasing."
)

# Eight fixed sections (F58).
_EIGHT_SECTIONS = (
    "① Main intent (quote user's original words as much as possible, no paraphrasing)\n"
    "② Key concepts\n"
    "③ Related files and code\n"
    "④ Fixed errors\n"
    "⑤ Problem solving\n"
    "⑥ Pending tasks\n"
    "⑦ Current work\n"
    "⑧ Next steps"
)

SUMMARY_INSTRUCTION = (
    "Please compress the earlier conversation above into a structured summary.\n\n"
    "Discipline: do not call any tools; first write an analysis draft to organize the thread, then write the formal summary,"
    " discard the draft after use, and only retain the formal summary in the end.\n\n"
    "The formal summary must be fully enclosed in <final_summary>…</final_summary> tags,"
    " organized into the following eight sections:\n" + _EIGHT_SECTIONS
)


# ---------------------------------------------------------------------------
# Summary extraction
# ---------------------------------------------------------------------------

_FINAL_SUMMARY_RE = re.compile(
    r"<final_summary>(.*?)</final_summary>", re.DOTALL | re.IGNORECASE
)


def _extract_final_summary(text: str) -> str:
    """Pull the ``<final_summary>`` body, tolerating a missing tag.

    With a tag present, return its stripped body. Without the tag, fall back to
    the whole stripped text. Raises :class:`SummaryError` if the result is empty.
    """
    match = _FINAL_SUMMARY_RE.search(text)
    body = match.group(1) if match else text
    body = body.strip()
    if not body:
        raise SummaryError("summary backend produced no usable text")
    return body


def summarize(provider, earlier: list[Message]) -> str:
    """Ask *provider* to summarize *earlier* messages into the final summary text.

    Issues ``provider.stream(earlier + [instruction], system=SUMMARY_SYSTEM,
    tools=None)`` — ``tools=None`` physically disables tools (F59). Collects all
    ``TextDelta`` text, extracts the ``<final_summary>`` body (tolerating a
    missing tag), and raises :class:`SummaryError` when empty.
    """
    request: list[Message] = list(earlier) + [
        {"role": "user", "content": SUMMARY_INSTRUCTION}
    ]
    chunks: list[str] = []
    for event in provider.stream(request, system=SUMMARY_SYSTEM, tools=None):
        if isinstance(event, TextDelta):
            chunks.append(event.text)
    return _extract_final_summary("".join(chunks))


# ---------------------------------------------------------------------------
# Compacted-history assembly
# ---------------------------------------------------------------------------

_BOUNDARY_NOTICE = (
    "[The above is a summary of the earlier conversation. To obtain the specific content of a file, re-read it with a tool;"
    " never hallucinate or reconstruct code from the summary.]"
)


def build_compacted(summary_text: str, kept: list[Message]) -> list[Message]:
    """Fold the summary + boundary into one user message, prepended to *kept*.

    The summary and the re-read boundary notice are merged into a single
    ``user`` message (F60 — backend-role-legal, avoids consecutive-same-role
    risk on stricter backends), then the kept tail is appended verbatim.
    """
    merged: Message = {
        "role": "user",
        "content": (
            f"<conversation_summary>\n{summary_text}\n</conversation_summary>\n\n"
            f"{_BOUNDARY_NOTICE}"
        ),
    }
    return [merged] + list(kept)
