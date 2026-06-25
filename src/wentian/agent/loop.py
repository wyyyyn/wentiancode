"""v0.5 · C25 · F39/F40 (task T64) — request_decorator + cache field accumulation
v0.4 · C17 · F29 (task T52; T53 adds five halt branches)

AgentLoop: scheduling core for ReAct multi-round tool loop.

Each round = stream phase (StreamBridge + RoundCollector dual-channel) → decision (halt check) →
tool phase (partition_waves batching + run_wave outputs in original order, blocked calls never
reach executor) → atomic history commit → next round. ``run()`` **mutates** *messages* in place;
persistence is the caller's responsibility (REPL).

Five halt conditions (StopReason, AgentDone is always the last event):

- COMPLETED — a round has no tool_calls, normal completion;
- USER_CANCELLED — stream phase was interrupted: if partial text exists, only store assistant text (discard
  tool_calls), zero text means nothing is committed for this round;
- STREAM_ERROR — stream raised an exception: entire round discarded (partial text also not committed), exception never escapes
  ``run()``, error message goes into ``AgentDone.error``;
- MAX_ROUNDS — round max_rounds still requests tools: not executed (runaway brake, brake point no longer
  emits "final batch"), only text is stored (storing unexecuted tool_calls would be rejected by API 400);
- UNKNOWN_TOOL_LOOP — ``unknown_streak_limit`` consecutive rounds (default 2) of calls
  all with unregistered names: error results are committed to history as normal pairs (history stays consistent for next user round) then
  halt. blocked (plan mode intercept) is intentionally excluded from the streak count.

Only imports stdlib, ``wentian.providers.base`` and ``wentian.agent.*`` —
never imports ``wentian.tools``, rich, prompt_toolkit, or specific providers;
registry / executor / interrupt_listener are always passed as duck-typed objects.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from wentian.agent.batch import classify, partition_waves, run_wave
from wentian.agent.bridge import StreamBridge, call_in_thread
from wentian.agent.collector import RoundCollector
from wentian.agent.events import (
    AgentDone,
    AgentEvent,
    RoundEnd,
    RoundStart,
    StopReason,
    StreamEnd,
    ToolCallStarted,
    ToolResultReady,
    UsageUpdate,
)
from wentian.providers.base import Message, ToolCallEvent, ToolSpec, Usage

__all__ = ["AgentLoop"]


@dataclass(frozen=True)
class _BlockedOutcome:
    """Synthesized result for plan mode intercepts — shape duck-compatible with ToolOutcome, never reaches executor.

    ``denied=False``: intercept is not a user rejection, but a mode restriction (distinguishing from
    the denied semantics of the v0.3 confirmation flow).
    """

    call_id: str
    name: str
    content: str
    is_error: bool = True
    denied: bool = False


def _add_usage(total: Usage | None, round_usage: Usage) -> Usage:
    """Accumulate token usage across rounds (including cache fields)."""
    if total is None:
        return round_usage
    return Usage(
        input_tokens=total.input_tokens + round_usage.input_tokens,
        output_tokens=total.output_tokens + round_usage.output_tokens,
        cache_creation_input_tokens=(
            total.cache_creation_input_tokens + round_usage.cache_creation_input_tokens
        ),
        cache_read_input_tokens=(
            total.cache_read_input_tokens + round_usage.cache_read_input_tokens
        ),
    )


class AgentLoop:
    """ReAct multi-round loop scheduler (events yielded as AsyncIterator[AgentEvent])."""

    def __init__(
        self,
        provider,
        *,
        registry: object | None = None,
        executor: object | None = None,
        interrupt_listener: object | None = None,
        max_rounds: int = 20,
        unknown_streak_limit: int = 2,
        allowed_tools: frozenset[str] | None = None,
        # v0.6 · C36 · F46/F49 (task T76) — permission gate: duck-typed async callback,
        # returns None=allow, non-None=already-formed rejection result object (duck-compatible with ToolOutcome).
        # None ⇒ v0.5 behavior (safe fallback). loop does not interpret verdict/source or synthesize outcome.
        permission_gate: Callable[[ToolCallEvent], Awaitable[object | None]]
        | None = None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._executor = executor
        self._interrupt_listener = interrupt_listener
        self._max_rounds = max_rounds
        self._unknown_streak_limit = unknown_streak_limit
        self._allowed_tools = allowed_tools
        self._permission_gate = permission_gate

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
        request_decorator: Callable[[list[Message], int], list[Message]] | None = None,
        # v0.8 · C51 · F62/N25 (task T95) — compaction hook: duck-typed write-back callback,
        # mutates messages in place (returns None via side effect). loop does not hold Compactor,
        # does not interpret CompactionResult — pure duck callback. None (default) ⇒ byte-for-byte
        # equivalent to v0.7.
        pre_round_compact: Callable[[list[Message], Usage | None], None] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Run multi-round loop until completion; **mutates** *messages* in place (persistence is the caller's responsibility).

        *request_decorator(messages, round_index) -> messages* (optional):
        Before each provider call, use it to compute the actual outgoing messages for this round;
        history commits, persistence, and decisions still operate on the original messages — decorator output only feeds
        the current stream, never written back to messages, and not used as the basis for subsequent rounds (recomputed each round).

        *pre_round_compact(messages, last_round_usage)* (v0.8 · C51 · F62/N25,
        optional): each round, called after ``RoundStart`` and before constructing *outgoing* / calling provider,
        **mutates** *messages* in place (write-back hook, returns None via side effect).
        *last_round_usage* stores the previous round's ``round_result.usage`` across rounds — **first round
        passes None**, subsequent rounds pass the **per-round** usage of the previous round (not the cumulative *total_usage*).
        Ordering contract: **compact first, then wrap reminders** — after the hook rewrites *messages*, it is
        ``request_decorator``'s turn to wrap ``<system-reminder>`` (the latter remains read-only, not written back,
        not persisted, two channels do not interfere). loop does not hold Compactor, does not interpret return value (pure
        duck callback); hook is expected not to raise (compactor internally absorbs SummaryError). **When hook is
        None (default) ⇒ byte-for-byte equivalent to v0.7**: not called, no change to any existing behavior (N25).
        """
        total_usage: Usage | None = None
        # Store the previous round's per-round usage across rounds (None for first round), as the anchor for the next round's compaction hook.
        last_round_usage: Usage | None = None
        unknown_streak = 0

        for n in range(1, self._max_rounds + 1):
            yield RoundStart(n)

            # --- Compact first: mutate history in place after RoundStart, before outgoing/provider ---
            #     (None ⇒ not called, zero behavior change, equivalent to v0.7)
            if pre_round_compact is not None:
                pre_round_compact(messages, last_round_usage)

            # --- Recompute outgoing each round: decorator only affects the current provider call ---
            #     (wrap reminders after: based on messages rewritten by hook)
            outgoing = (
                request_decorator(messages, n)
                if request_decorator is not None
                else messages
            )

            # --- Stream phase: listener is only armed here (tool phase cannot be interrupted) ---
            collector = RoundCollector()
            listener_ctx = (
                self._interrupt_listener
                if self._interrupt_listener is not None
                else contextlib.nullcontext(None)
            )
            interrupted = False
            stream_error: Exception | None = None
            with listener_ctx as interrupt_event:
                bridge = StreamBridge(
                    self._provider.stream(outgoing, system=system, tools=tools)
                )
                try:
                    async for ev in bridge.drain(interrupt_event):
                        shown = collector.feed(ev)
                        if shown is not None:
                            yield shown
                except Exception as exc:  # noqa: BLE001 — stream errors must not escape run()
                    stream_error = exc
                interrupted = interrupt_event is not None and interrupt_event.is_set()

            # --- Halt: STREAM_ERROR — entire round discarded (partial text also not committed),
            #     atomic blocks from previous rounds are already in messages ---
            if stream_error is not None:
                yield AgentDone(
                    StopReason.STREAM_ERROR,
                    text="",
                    rounds=n,
                    usage=total_usage,
                    error=str(stream_error),
                )
                return

            round_result = collector.result(interrupted=interrupted)
            if round_result.usage is not None:
                total_usage = _add_usage(total_usage, round_result.usage)
                yield UsageUpdate(round_usage=round_result.usage, total=total_usage)
            # Record this round's per-round usage as the anchor for the next round's compaction hook (None before the first round).
            last_round_usage = round_result.usage
            yield StreamEnd(n, round_result.text, interrupted)

            # --- Halt: USER_CANCELLED — if partial text exists, only store text (discard
            #     tool_calls), zero text means nothing committed ---
            if interrupted:
                if round_result.text:
                    messages.append({"role": "assistant", "content": round_result.text})
                yield AgentDone(
                    StopReason.USER_CANCELLED,
                    text=round_result.text,
                    rounds=n,
                    usage=total_usage,
                )
                return

            # --- Halt: COMPLETED — no tool_calls, normal completion ---
            if not round_result.tool_calls:
                messages.append({"role": "assistant", "content": round_result.text})
                yield AgentDone(
                    StopReason.COMPLETED,
                    text=round_result.text,
                    rounds=n,
                    usage=total_usage,
                )
                return

            # --- Halt: MAX_ROUNDS — last round still requests tools: runaway brake, not executed,
            #     only text stored (storing unexecuted tool_calls would be rejected by API 400) ---
            if n == self._max_rounds:
                messages.append({"role": "assistant", "content": round_result.text})
                yield AgentDone(
                    StopReason.MAX_ROUNDS,
                    text=round_result.text,
                    rounds=n,
                    usage=total_usage,
                )
                return

            # --- unknown streak tracking: only counts a round if calls are non-empty and all unknown;
            #     other tool rounds reset to zero. blocked intentionally excluded (plan mode intercepts should not
            #     be treated as hallucinated tool names) ---
            kinds = [
                classify(call, self._registry, self._allowed_tools)
                for call in round_result.tool_calls
            ]
            if kinds and all(kind == "unknown" for kind in kinds):
                unknown_streak += 1
            else:
                unknown_streak = 0

            # --- Tool phase: executed in batches, results collected in original call order ---
            async def run_call(call: ToolCallEvent) -> object:
                # blocked calls never reach executor — synthesize intercept result directly.
                if classify(call, self._registry, self._allowed_tools) == "blocked":
                    return self._make_blocked_outcome(call)
                # v0.6 · C36 · F46/F49 (task T76) — permission gate: after blocked check,
                # before executor. Gate returns None=allow, non-None=already-formed rejection result object
                # (worded by the assembly layer based on source), loop feeds it back as-is, skips executor, does not interpret.
                if self._permission_gate is not None:
                    denied = await self._permission_gate(call)
                    if denied is not None:
                        return denied
                return await call_in_thread(
                    self._executor.execute, call.id, call.name, call.arguments
                )

            results: list[tuple[ToolCallEvent, object]] = []
            waves = partition_waves(
                round_result.tool_calls, self._registry, self._allowed_tools
            )
            for wave in waves:
                async for kind, call, outcome in run_wave(wave, run_call):
                    if kind == "started":
                        yield ToolCallStarted(call)
                    else:  # "result"
                        yield ToolResultReady(outcome)
                        results.append((call, outcome))

            # --- Atomic history commit: append all results at once after all are collected (v0.3 contract:
            #     executor receives raw arguments including None, committed to history as None→{}) ---
            assistant_msg: Message = {
                "role": "assistant",
                "content": round_result.text,
                "tool_calls": [
                    {
                        "id": call.id,
                        "name": call.name,
                        "arguments": (
                            call.arguments if call.arguments is not None else {}
                        ),
                    }
                    for call in round_result.tool_calls
                ],
            }
            if round_result.raw_content is not None:
                assistant_msg["raw_content"] = round_result.raw_content
            messages.append(assistant_msg)
            # results are already in original call order (run_wave outputs in wave original order,
            # partition_waves preserves global original order).
            for call, outcome in results:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": outcome.content,  # type: ignore[attr-defined]
                        "is_error": outcome.is_error,  # type: ignore[attr-defined]
                    }
                )

            yield RoundEnd(n, tool_results=len(results))

            # --- Halt: UNKNOWN_TOOL_LOOP — results committed first, RoundEnd emitted first,
            #     history stays consistent for the next user round, then halt ---
            if unknown_streak >= self._unknown_streak_limit:
                yield AgentDone(
                    StopReason.UNKNOWN_TOOL_LOOP,
                    text=round_result.text,
                    rounds=n,
                    usage=total_usage,
                )
                return

        # Unreachable: round max_rounds either terminates with COMPLETED/USER_CANCELLED/STREAM_ERROR,
        # or hits the MAX_ROUNDS brake — the loop body always returns.

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_blocked_outcome(self, call: ToolCallEvent) -> _BlockedOutcome:
        """Synthesize plan mode intercept result (informs the model of current restrictions and how to exit)."""
        allowed = ", ".join(sorted(self._allowed_tools or ())) or "(none)"
        return _BlockedOutcome(
            call_id=call.id,
            name=call.name,
            content=(
                f"Tool {call.name} was intercepted in plan mode: only"
                f" read-only tools are currently allowed ({allowed}). To perform"
                f" write operations, ask the user to exit plan mode with /do."
            ),
        )
