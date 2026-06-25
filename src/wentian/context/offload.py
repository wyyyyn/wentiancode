"""First-layer oversized tool-result offload — lightweight prevention.

v0.8 · C48 · F57/N27 (task T92)

Scans the conversation in place for ``role=="tool"`` results whose estimated
token size crosses a threshold, writes each oversized result's full content to
the session artifacts directory, and replaces the inline content with a short
preview plus the absolute file path and a one-line hint. Two gates:

- **single gate** — any not-yet-offloaded tool result whose
  ``char_estimate([m]) > cfg.offload_single_tokens`` is offloaded.
- **round gate** — adjacent tool results form a *round* (a maximal run of
  consecutive ``tool`` messages, broken by any non-tool message). If a round's
  combined estimate exceeds ``cfg.offload_round_sum_tokens``, the largest
  results (by char_estimate, descending) are offloaded one by one until the
  round's remaining estimate falls back under the threshold.

The pass is **idempotent** (a message already marked ``offloaded`` is skipped,
not re-written to disk) and **never rewrites user/assistant messages** (N27 —
the token bulk lives in tool results; the user's original messages stay
verbatim). It does not import rich, any provider, or the agent layer — only the
stdlib plus ``char_estimate`` and the ``Message`` / ``ContextConfig`` types.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wentian.config import ContextConfig
from wentian.context.estimator import char_estimate
from wentian.providers.base import Message

__all__ = ["OffloadAction", "offload_oversized"]

#: Preview is capped at whichever of these limits is reached first.
_PREVIEW_MAX_LINES = 20
_PREVIEW_MAX_CHARS = 800


@dataclass(frozen=True)
class OffloadAction:
    """Record of one tool result that was offloaded to disk.

    ``tool_call_id`` identifies the result, ``path`` is the absolute path of the
    written artifact, and ``original_tokens`` is the estimated token size of the
    original content.
    """

    tool_call_id: str
    path: str
    original_tokens: int


def _build_preview(content: str) -> str:
    """First ~20 lines or ~800 chars of *content*, whichever comes first."""
    char_capped = content[:_PREVIEW_MAX_CHARS]
    lines = char_capped.split("\n")
    if len(lines) > _PREVIEW_MAX_LINES:
        return "\n".join(lines[:_PREVIEW_MAX_LINES])
    return char_capped


def _offload_message(
    message: Message,
    *,
    artifacts_dir: Path,
    tokens: int,
) -> OffloadAction:
    """Write *message*'s content to disk and replace it with preview + hint.

    Sets ``offloaded=True`` and preserves ``is_error``. Returns the action.
    """
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    call_id = message["tool_call_id"]
    original = message.get("content") or ""
    artifact = (artifacts_dir / f"tool-{call_id}.txt").resolve()
    artifact.write_text(original, encoding="utf-8")

    preview = _build_preview(original)
    message["content"] = (
        f"{preview}\n\n"
        f"[Full output saved to disk: {artifact} (approx. {tokens} tokens)."
        f" For details, use the file-reading tool to read that path; do not infer from the preview.]"
    )
    message["offloaded"] = True
    return OffloadAction(
        tool_call_id=call_id, path=str(artifact), original_tokens=tokens
    )


def _round_groups(messages: list[Message]) -> list[list[int]]:
    """Group indices of consecutive ``tool`` messages into rounds.

    A round is a maximal run of adjacent ``role=="tool"`` messages; any non-tool
    message breaks the run and starts a fresh group.
    """
    groups: list[list[int]] = []
    current: list[int] = []
    for idx, message in enumerate(messages):
        if message.get("role") == "tool":
            current.append(idx)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def offload_oversized(
    messages: list[Message],
    *,
    artifacts_dir: Path,
    cfg: ContextConfig,
) -> list[OffloadAction]:
    """Offload oversized tool results in place; return the actions taken.

    See the module docstring for the single / round gate semantics. Only
    ``role=="tool"`` messages that are not yet ``offloaded`` are considered;
    user and assistant messages are never inspected or rewritten (N27).
    """
    cpt = cfg.char_per_token
    actions: list[OffloadAction] = []

    for group in _round_groups(messages):
        # Live (not-yet-offloaded) members of this round, with their estimates.
        pending: list[tuple[int, int]] = []  # (index, estimated tokens)
        for idx in group:
            message = messages[idx]
            if message.get("offloaded"):
                continue
            tokens = char_estimate([message], cpt)
            pending.append((idx, tokens))

        # --- single gate ---
        survivors: list[tuple[int, int]] = []
        for idx, tokens in pending:
            if tokens > cfg.offload_single_tokens:
                actions.append(
                    _offload_message(
                        messages[idx], artifacts_dir=artifacts_dir, tokens=tokens
                    )
                )
            else:
                survivors.append((idx, tokens))

        # --- round sum gate: drop the largest survivors until under threshold ---
        remaining = sum(tokens for _, tokens in survivors)
        for idx, tokens in sorted(survivors, key=lambda it: it[1], reverse=True):
            if remaining <= cfg.offload_round_sum_tokens:
                break
            actions.append(
                _offload_message(
                    messages[idx], artifacts_dir=artifacts_dir, tokens=tokens
                )
            )
            remaining -= tokens

    return actions
