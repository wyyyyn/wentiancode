"""Second-layer (heavy) compaction — clean cut boundary + eight-section summary.

v0.8 · C49 · F58/F59/F60（任务 T93）

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

context7 查证（Anthropic Messages API · 连续同角色容忍度）
-----------------------------------------------------------
查证结论：Anthropic 官方 SDK 文档对 ``messages`` 的说明是「模型被训练为在 *交替*
的 user/assistant 回合上工作（trained to operate on alternating turns）」——这是
*训练倾向* 的描述，**不是硬性拒绝**。实际 API 行为：连续同角色消息被**容忍**并在
服务端**合并为同一回合**；唯一的硬约束是「首条消息须为 user」以及「每个
tool_result 必须配前序 tool_use」。
采取策略：**默认实现**——snap 只跳过孤儿 ``tool`` 结果（``role == "tool"``），保留段
首条可以是 ``user`` 或 ``assistant``。因 F60 已将「摘要 + 边界」合并为**单条 user
消息**，即便其紧邻的保留段首条也是 user，两条连续 user 会被服务端合并、不报错，
故**无需** fallback 把 cut 继续 snap 到下一条 assistant。

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
    "你是对话压缩助手。你的唯一任务是把早期对话压成一段结构化摘要。"
    "纪律（不可违反）：\n"
    "1. 禁止调用任何工具——本次请求不携带任何工具声明，你也绝不能尝试调用工具。\n"
    "2. 先写分析草稿（梳理对话脉络、定位关键信息），再写正式摘要；草稿用完即弃，"
    "最终只采用正式摘要部分。\n"
    "3. 正式摘要必须完整包在 <final_summary>…</final_summary> 标签内。\n"
    "4. 正式摘要按八段固定结构组织，主要意图段尽量引用用户原话、不要改写。"
)

# Eight fixed sections (F58).
_EIGHT_SECTIONS = (
    "① 主要意图（尽量引用用户原话、不改写）\n"
    "② 关键概念\n"
    "③ 相关文件与代码\n"
    "④ 已修报错\n"
    "⑤ 问题求解\n"
    "⑥ 待办任务\n"
    "⑦ 当前工作\n"
    "⑧ 下一步"
)

SUMMARY_INSTRUCTION = (
    "请把以上早期对话压成结构化摘要。\n\n"
    "纪律：禁止调用任何工具；先写一段分析草稿梳理脉络、再写正式摘要，"
    "草稿用完即弃、最终只保留正式摘要。\n\n"
    "正式摘要必须完整包在 <final_summary>…</final_summary> 标签内，"
    "并按以下八段组织：\n" + _EIGHT_SECTIONS
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
    "[以上为早期对话的摘要。需要文件具体内容请重新用工具读取，"
    "切勿照摘要脑补或重建代码。]"
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
