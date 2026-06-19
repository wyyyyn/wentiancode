"""Two-layer compaction orchestration + failure circuit breaker.

v0.8 · C50 · F61/F62（任务 T94）

The :class:`Compactor` ties the first layer (oversized tool-result offload) and
the second layer (heavy LLM summary) together, decides when each fires, and owns
the stateful circuit breaker that protects against a "summary fails → history
never shrinks → instantly re-triggers and re-fails" death loop.

One :meth:`Compactor.compact` call, in order:

1. **L1 (always)** — :func:`offload_oversized` rewrites oversized tool results in
   place. Runs unconditionally, even when the breaker is tripped.
2. **Estimate** — anchor the size at the provider's real prompt-token total of
   ``last_usage`` (0 when ``None``) plus the char estimate of the messages
   appended since the last anchor (``messages[_last_seen_len:]``). With
   ``last_usage=None`` the anchor is 0 and (because ``_last_seen_len`` is usually
   0) the estimate degrades to a full-history char folding.
3. **Threshold** — ``context_window - reserved_output - margin``, where ``margin``
   is ``manual_margin`` for a manual ``/compact`` (narrower, more aggressive) and
   ``auto_margin`` for an automatic per-round call (wider safety buffer).
4. **L2 (conditional)** — fires when ``est > threshold`` *and* (the call is
   ``manual`` *or* the breaker is not tripped). On a clean cut it summarizes the
   earlier segment and rewrites the history to ``summary + boundary + kept tail``.
   Three consecutive :class:`SummaryError` trip the breaker; any success resets
   the fail counter, and a successful *manual* run additionally clears the trip.
5. **Anchor update** — ``_last_seen_len = len(messages)`` taken *after* any
   rewrite, so the next call's ``messages[_last_seen_len:]`` is exactly the
   messages appended past this anchor.

Circuit breaker semantics:

- ``_fail`` counts *consecutive* :class:`SummaryError`; ``>= 3`` sets
  ``_tripped``. Any successful summary zeroes ``_fail``.
- A ``manual`` call ignores ``_tripped`` and force-retries; on success it also
  sets ``_tripped = False`` (the breaker is released).
- An automatic call (``manual=False``) skips L2 while ``_tripped`` is set — but
  L1 offload still runs every call.

Leaf module: stdlib only + ``wentian.context.*`` (estimator / offload /
summarizer) + ``wentian.config.ContextConfig`` + ``wentian.providers.base``
types. The provider is duck-typed — only ``.stream`` and ``.prompt_token_total``
are used; no rich, no agent, no concrete provider is imported.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wentian.config import ContextConfig
from wentian.context.estimator import estimate_total
from wentian.context.offload import OffloadAction, offload_oversized
from wentian.context.summarizer import (
    SummaryError,
    build_compacted,
    find_cut_index,
    summarize,
)
from wentian.providers.base import Message, Usage

__all__ = ["CompactionResult", "Compactor"]

#: Consecutive summary failures that trip the breaker.
_FAIL_TRIP_THRESHOLD = 3


@dataclass(frozen=True)
class CompactionResult:
    """Outcome of one :meth:`Compactor.compact` call.

    - ``offloaded`` — the L1 actions taken this call (may be empty).
    - ``summarized`` — whether L2 rewrote the history this call.
    - ``estimated_tokens`` — the size estimate used for the threshold decision.
    - ``tripped`` — the breaker state *after* this call.
    - ``failed_this_call`` — whether an L2 attempt raised :class:`SummaryError`.
    """

    offloaded: list[OffloadAction]
    summarized: bool
    estimated_tokens: int
    tripped: bool
    failed_this_call: bool


class Compactor:
    """Stateful orchestrator for the two-layer compaction strategy."""

    def __init__(
        self,
        provider,
        *,
        artifacts_dir: Path,
        context_window: int,
        cfg: ContextConfig,
    ) -> None:
        self.provider = provider
        self._artifacts_dir = artifacts_dir
        self._context_window = context_window
        self._cfg = cfg
        # Estimate anchor: len(messages) at the end of the previous call, so
        # ``messages[_last_seen_len:]`` is exactly what was appended since.
        self._last_seen_len = 0
        # Circuit breaker state.
        self._fail = 0
        self._tripped = False

    @property
    def context_window(self) -> int:
        """v0.8 · C52 · F61/F62（任务 T96）— current context window (read-only)."""
        return self._context_window

    def set_provider(self, provider, context_window: int) -> None:
        """v0.8 · C52 · F61/F62（任务 T96）— swap the active backend + window.

        Called by the REPL after ``/provider`` so estimation anchors and the L2
        threshold track the newly selected backend (windows can differ by an
        order of magnitude, e.g. opus 1M vs deepseek 128K). Pure state swap; the
        circuit breaker and the size anchor are intentionally left untouched —
        switching backend does not reset accumulated history.
        """
        self.provider = provider
        self._context_window = context_window

    def set_artifacts_dir(self, artifacts_dir: Path) -> None:
        """v0.8 · C52 · F61/F62（任务 T96）— retarget the offload artifacts dir.

        Called by the REPL after ``/new`` / ``/resume`` so offloaded tool
        results land under the *current* session's ``<id>.artifacts/`` directory.
        Also resets the size anchor: a fresh session starts a new estimate.
        """
        self._artifacts_dir = artifacts_dir
        self._last_seen_len = 0

    def _threshold(self, *, manual: bool) -> int:
        """Trigger threshold for L2 given the call mode."""
        margin = self._cfg.manual_margin if manual else self._cfg.auto_margin
        return self._context_window - self._cfg.reserved_output - margin

    def _estimate(self, messages: list[Message], last_usage: Usage | None) -> int:
        """Size estimate = prompt anchor + char estimate of new messages."""
        prompt_total = (
            self.provider.prompt_token_total(last_usage)
            if last_usage is not None
            else 0
        )
        new_messages = messages[self._last_seen_len :]
        return estimate_total(
            prompt_total, new_messages, char_per_token=self._cfg.char_per_token
        )

    def compact(
        self,
        messages: list[Message],
        last_usage: Usage | None,
        *,
        manual: bool = False,
    ) -> CompactionResult:
        """Run L1 → estimate → threshold → L2 in place; return the outcome."""
        # --- L1: oversized tool-result offload (always, even when tripped) ---
        actions = offload_oversized(
            messages, artifacts_dir=self._artifacts_dir, cfg=self._cfg
        )

        # --- Estimate + threshold ---
        est = self._estimate(messages, last_usage)
        threshold = self._threshold(manual=manual)

        # --- L2: heavy summary (conditional) ---
        summarized = False
        failed_this_call = False
        if est > threshold and (manual or not self._tripped):
            try:
                cut = find_cut_index(messages, cfg=self._cfg)
                if cut > 0:  # there is an earlier segment worth summarizing
                    summary = summarize(self.provider, messages[:cut])
                    messages[:] = build_compacted(summary, messages[cut:])
                    summarized = True
                    self._fail = 0
                    if manual:
                        self._tripped = False
                # cut == 0 (nothing to summarize): not a failure, not counted.
            except SummaryError:
                failed_this_call = True
                self._fail += 1
                if self._fail >= _FAIL_TRIP_THRESHOLD:
                    self._tripped = True

        # --- Anchor update (after any rewrite) ---
        self._last_seen_len = len(messages)

        return CompactionResult(
            offloaded=actions,
            summarized=summarized,
            estimated_tokens=est,
            tripped=self._tripped,
            failed_this_call=failed_this_call,
        )
