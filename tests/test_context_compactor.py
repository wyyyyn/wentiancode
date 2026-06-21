"""Tests for context/compactor.py: Compactor orchestration + circuit breaker.

v0.8 · C50 · F61/F62（任务 T94）

Pure offline tests. The provider is supplied via a *fake* (duck-typed) object
implementing ``stream(messages, *, system, tools)`` + ``prompt_token_total``;
no real backend, no network. Covers: L1 runs first, estimate anchor +
``_last_seen_len`` update, threshold (auto 13K vs manual 3K margin), L2 success
rewrites history + resets fail count, and the circuit breaker (3 fails → trip →
auto skips L2 but L1 still runs → success resets → manual force-retry + reset).
"""

import pytest

from wentian.config import ContextConfig
from wentian.context.compactor import CompactionResult, Compactor
from wentian.providers.base import Done, TextDelta, Usage


# ---------------------------------------------------------------------------
# Fake provider
# ---------------------------------------------------------------------------


class FakeProvider:
    """Duck-typed provider: scripted ``stream`` + ``prompt_token_total``.

    ``summary_text`` is wrapped in ``<final_summary>`` and replayed as a single
    TextDelta (followed by Done). ``fail`` makes ``stream`` yield empty text so
    that ``summarize`` raises ``SummaryError`` (no usable summary).
    """

    def __init__(self, *, summary_text="正式摘要内容", fail=False):
        self._summary_text = summary_text
        self._fail = fail
        self.stream_calls = 0

    def stream(self, messages, *, system=None, tools=None):
        self.stream_calls += 1
        if self._fail:
            yield TextDelta("")  # empty → summarize raises SummaryError
        else:
            yield TextDelta(f"<final_summary>{self._summary_text}</final_summary>")
        yield Done()

    def prompt_token_total(self, usage):
        # Mirror the Anthropic-style total used by the real estimator.
        return (
            usage.input_tokens
            + usage.cache_creation_input_tokens
            + usage.cache_read_input_tokens
        )


# ---------------------------------------------------------------------------
# Message helpers
# ---------------------------------------------------------------------------


def _user(content):
    return {"role": "user", "content": content}


def _assistant(content="", tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    return msg


def _tool(content, tool_call_id="call_1"):
    return {"role": "tool", "content": content, "tool_call_id": tool_call_id}


def _cfg(**overrides):
    return ContextConfig(**overrides)


# ---------------------------------------------------------------------------
# RED 1: L1 offload runs first
# ---------------------------------------------------------------------------


def test_l1_offload_runs_first(tmp_path):
    """A huge tool result is offloaded (L1) even when L2 does not trigger."""
    cfg = _cfg(offload_single_tokens=10)
    big = "x" * 5_000
    messages = [_user("hi"), _assistant("ok"), _tool(big)]
    compactor = Compactor(
        FakeProvider(),
        artifacts_dir=tmp_path,
        context_window=1_000_000,  # huge window → L2 won't trigger
        cfg=cfg,
    )

    result = compactor.compact(messages, None)

    assert len(result.offloaded) == 1
    assert messages[2]["offloaded"] is True
    assert big not in messages[2]["content"]  # replaced by preview + path
    assert result.summarized is False


# ---------------------------------------------------------------------------
# RED 2: estimate anchor + _last_seen_len update
# ---------------------------------------------------------------------------


def test_estimate_none_usage_counts_all_chars(tmp_path):
    """last_usage=None → prompt_total 0 ⇒ est = full char estimate of messages."""
    cfg = _cfg(char_per_token=1.0)
    messages = [_user("abcd"), _assistant("efgh")]  # 8 chars total
    compactor = Compactor(
        FakeProvider(), artifacts_dir=tmp_path, context_window=1_000_000, cfg=cfg
    )

    result = compactor.compact(messages, None)

    assert result.estimated_tokens == 8


def test_estimate_uses_prompt_anchor_plus_new_messages(tmp_path):
    """est = prompt_token_total(usage) + char(messages[_last_seen_len:])."""
    cfg = _cfg(char_per_token=1.0)
    compactor = Compactor(
        FakeProvider(), artifacts_dir=tmp_path, context_window=1_000_000, cfg=cfg
    )
    # First call sets _last_seen_len = 2.
    messages = [_user("abcd"), _assistant("efgh")]
    compactor.compact(messages, None)
    assert compactor._last_seen_len == 2

    # Second call: append two new messages; anchor (input_tokens=100) + new chars.
    messages.append(_assistant("ij"))  # 2 chars
    messages.append(_user("kl"))  # 2 chars
    usage = Usage(input_tokens=100, output_tokens=0)
    result = compactor.compact(messages, usage)

    assert result.estimated_tokens == 100 + 4
    assert compactor._last_seen_len == 4  # updated to final len


def test_last_seen_len_updates_after_rewrite(tmp_path):
    """_last_seen_len is taken AFTER any L2 rewrite (final len)."""
    cfg = _cfg(
        char_per_token=1.0,
        recent_keep_tokens=1,
        recent_keep_min_messages=1,
        reserved_output=0,
        auto_margin=0,
    )
    # Long history → L2 will collapse it; final len < original len.
    messages = [_user("a" * 50), _assistant("b" * 50), _user("c" * 50), _user("d")]
    compactor = Compactor(
        FakeProvider(), artifacts_dir=tmp_path, context_window=10, cfg=cfg
    )
    result = compactor.compact(messages, None)

    assert result.summarized is True
    assert compactor._last_seen_len == len(messages)  # final, post-rewrite


# ---------------------------------------------------------------------------
# RED 3: threshold — auto 13K vs manual 3K margin difference
# ---------------------------------------------------------------------------


def test_threshold_auto_vs_manual_margin(tmp_path):
    """With est between the two thresholds, manual triggers L2 but auto does not.

    window - reserved_output = 20_000. auto_margin=13_000 → auto threshold 7_000;
    manual_margin=3_000 → manual threshold 17_000. Choose est = 10_000:
      auto: 10_000 > 7_000  → triggers
      ... so instead pick est so only manual triggers. Use est = 5_000:
      auto: 5_000 > 7_000 → False (no trigger)
      manual: 5_000 > 17_000 → False too.
    We want est strictly between: 7_000 < est <= 17_000, e.g. 10_000.
      auto threshold 7_000 → 10_000 > 7_000 triggers (not what we want).
    So invert: a SMALLER margin gives a HIGHER threshold → harder to trip.
    auto_margin large → low threshold → easy trip; manual margin small → high
    threshold → hard trip. To show the difference we pick est that trips auto
    but NOT manual.
    """
    cfg = _cfg(
        char_per_token=1.0,
        reserved_output=0,
        auto_margin=13_000,  # auto threshold = window - 13_000
        manual_margin=3_000,  # manual threshold = window - 3_000
        recent_keep_tokens=1,
        recent_keep_min_messages=1,
    )
    window = 20_000
    # est = 10_000 (10_000-char message). auto threshold = 7_000 → trips;
    # manual threshold = 17_000 → does NOT trip.
    big = "x" * 10_000

    auto_msgs = [_user("seed"), _assistant("a"), _user(big), _user("tail")]
    c_auto = Compactor(
        FakeProvider(), artifacts_dir=tmp_path, context_window=window, cfg=cfg
    )
    r_auto = c_auto.compact(auto_msgs, None, manual=False)
    assert r_auto.summarized is True  # 10_000 > 7_000

    manual_msgs = [_user("seed"), _assistant("a"), _user(big), _user("tail")]
    c_manual = Compactor(
        FakeProvider(), artifacts_dir=tmp_path, context_window=window, cfg=cfg
    )
    r_manual = c_manual.compact(manual_msgs, None, manual=True)
    assert r_manual.summarized is False  # 10_000 < 17_000 → below manual threshold


def test_no_trigger_when_below_threshold(tmp_path):
    """est below threshold → L2 never runs."""
    cfg = _cfg(char_per_token=1.0, reserved_output=0, auto_margin=0)
    messages = [_user("short"), _assistant("reply")]
    compactor = Compactor(
        FakeProvider(), artifacts_dir=tmp_path, context_window=1_000_000, cfg=cfg
    )
    result = compactor.compact(messages, None)
    assert result.summarized is False


# ---------------------------------------------------------------------------
# RED 4: L2 success rewrites history + resets fail count
# ---------------------------------------------------------------------------


def test_l2_success_rewrites_history(tmp_path):
    """L2 success → messages[:] = summary+boundary + kept tail, summarized True."""
    cfg = _cfg(
        char_per_token=1.0,
        reserved_output=0,
        auto_margin=0,
        recent_keep_tokens=1,
        recent_keep_min_messages=1,
    )
    messages = [
        _user("e" * 100),
        _assistant("a" * 100),
        _user("recent tail"),
    ]
    compactor = Compactor(
        FakeProvider(summary_text="SUM"),
        artifacts_dir=tmp_path,
        context_window=10,
        cfg=cfg,
    )
    result = compactor.compact(messages, None)

    assert result.summarized is True
    assert result.failed_this_call is False
    assert result.tripped is False
    # First message is now the merged summary + boundary user message.
    assert messages[0]["role"] == "user"
    assert "SUM" in messages[0]["content"]
    assert "<conversation_summary>" in messages[0]["content"]
    # Kept tail preserved verbatim.
    assert messages[-1]["content"] == "recent tail"


def test_l2_success_resets_fail_count(tmp_path):
    """A success zeroes the consecutive-fail counter."""
    cfg = _cfg(
        char_per_token=1.0,
        reserved_output=0,
        auto_margin=0,
        recent_keep_tokens=1,
        recent_keep_min_messages=1,
    )
    compactor = Compactor(
        FakeProvider(), artifacts_dir=tmp_path, context_window=10, cfg=cfg
    )
    compactor._fail = 2  # pretend two prior failures
    messages = [_user("e" * 100), _assistant("a" * 100), _user("tail")]
    compactor.compact(messages, None)
    assert compactor._fail == 0


def test_cut_zero_is_not_a_failure(tmp_path):
    """When find_cut_index returns 0 (nothing to summarize), no fail is counted."""
    cfg = _cfg(
        char_per_token=1.0,
        reserved_output=0,
        auto_margin=0,
        recent_keep_min_messages=10,  # history shorter → cut == 0
    )
    messages = [_user("x" * 100), _assistant("y" * 100)]
    compactor = Compactor(
        FakeProvider(), artifacts_dir=tmp_path, context_window=10, cfg=cfg
    )
    result = compactor.compact(messages, None)
    assert result.summarized is False
    assert result.failed_this_call is False
    assert compactor._fail == 0


# ---------------------------------------------------------------------------
# RED 5: circuit breaker — 3 fails → trip → auto skips L2 (L1 still runs) →
#        success resets → manual force-retry + reset
# ---------------------------------------------------------------------------


def _trip_cfg():
    return _cfg(
        char_per_token=1.0,
        reserved_output=0,
        auto_margin=0,
        manual_margin=0,
        recent_keep_tokens=1,
        recent_keep_min_messages=1,
        offload_single_tokens=10,
    )


def _trigger_msgs():
    # Long enough to exceed threshold and have a summarizable earlier segment.
    return [_user("e" * 100), _assistant("a" * 100), _user("tail")]


def test_circuit_breaker_trips_after_three_failures(tmp_path):
    """Three consecutive SummaryError → _tripped True, failed_this_call True.

    Models a growing history: each round appends a large message so the
    estimate (anchored past ``_last_seen_len``) keeps crossing the threshold.
    The failing fake provider never shrinks the history, so every round attempts
    L2 and fails — exactly the death-loop the breaker exists to cut.
    """
    compactor = Compactor(
        FakeProvider(fail=True),
        artifacts_dir=tmp_path,
        context_window=10,
        cfg=_trip_cfg(),
    )
    messages = _trigger_msgs()
    for i in range(2):
        r = compactor.compact(messages, None)
        assert r.failed_this_call is True
        assert r.tripped is False
        assert compactor._fail == i + 1
        messages.append(_user("more " * 50))  # grow past the anchor

    r = compactor.compact(messages, None)
    assert r.failed_this_call is True
    assert r.tripped is True
    assert compactor._fail == 3


def test_tripped_auto_skips_l2_but_l1_still_runs(tmp_path):
    """Once tripped, an AUTO call skips L2 but L1 offload still happens."""
    cfg = _trip_cfg()
    compactor = Compactor(
        FakeProvider(fail=True),
        artifacts_dir=tmp_path,
        context_window=10,
        cfg=cfg,
    )
    compactor._tripped = True  # already tripped
    big = "x" * 5_000
    messages = [_user("e" * 100), _assistant("a"), _tool(big), _user("tail")]
    result = compactor.compact(messages, None, manual=False)

    assert result.summarized is False  # L2 skipped
    assert result.failed_this_call is False  # no attempt → no new failure
    # L1 still ran: the big tool result was offloaded.
    assert len(result.offloaded) == 1
    assert messages[2]["offloaded"] is True


def test_success_after_trip_resets_fail_count(tmp_path):
    """A successful summary (e.g. via manual) zeroes _fail."""
    cfg = _trip_cfg()
    # Healthy provider; manual call to force a retry past the trip.
    compactor = Compactor(
        FakeProvider(summary_text="OK"),
        artifacts_dir=tmp_path,
        context_window=10,
        cfg=cfg,
    )
    compactor._fail = 3
    compactor._tripped = True
    result = compactor.compact(_trigger_msgs(), None, manual=True)
    assert result.summarized is True
    assert compactor._fail == 0


def test_manual_force_retries_and_clears_trip(tmp_path):
    """manual=True ignores _tripped, retries, and on success clears _tripped."""
    cfg = _trip_cfg()
    compactor = Compactor(
        FakeProvider(summary_text="OK"),
        artifacts_dir=tmp_path,
        context_window=10,
        cfg=cfg,
    )
    compactor._tripped = True
    compactor._fail = 3
    result = compactor.compact(_trigger_msgs(), None, manual=True)

    assert result.summarized is True
    assert result.tripped is False  # trip cleared
    assert compactor._fail == 0
    assert compactor.provider.stream_calls == 1  # actually attempted


def test_manual_skips_when_below_threshold_even_if_tripped(tmp_path):
    """manual ignores _tripped but still respects the (manual) threshold."""
    cfg = _cfg(char_per_token=1.0, reserved_output=0, manual_margin=0)
    compactor = Compactor(
        FakeProvider(), artifacts_dir=tmp_path, context_window=1_000_000, cfg=cfg
    )
    compactor._tripped = True
    messages = [_user("short"), _assistant("reply")]
    result = compactor.compact(messages, None, manual=True)
    assert result.summarized is False
    assert result.tripped is True  # untouched — no successful summary


# ---------------------------------------------------------------------------
# CompactionResult shape
# ---------------------------------------------------------------------------


def test_compaction_result_is_frozen_dataclass(tmp_path):
    compactor = Compactor(
        FakeProvider(), artifacts_dir=tmp_path, context_window=1_000_000, cfg=_cfg()
    )
    result = compactor.compact([_user("hi")], None)
    assert isinstance(result, CompactionResult)
    assert isinstance(result.offloaded, list)
    assert isinstance(result.summarized, bool)
    assert isinstance(result.estimated_tokens, int)
    assert isinstance(result.tripped, bool)
    assert isinstance(result.failed_this_call, bool)
    with pytest.raises(Exception):
        result.summarized = True  # frozen


# ---------------------------------------------------------------------------
# v0.12 · C99 · T124 — on_pre_compact callback
# ---------------------------------------------------------------------------


def test_on_pre_compact_called_before_summary_manual(tmp_path):
    """on_pre_compact is called with trigger='manual' before L2 summary (manual=True)."""
    cfg = _cfg(
        char_per_token=1.0,
        reserved_output=0,
        auto_margin=0,
        recent_keep_tokens=1,
        recent_keep_min_messages=1,
    )
    calls: list[str] = []

    def hook(trigger: str) -> None:
        calls.append(trigger)

    messages = [_user("e" * 100), _assistant("a" * 100), _user("tail")]
    compactor = Compactor(
        FakeProvider(summary_text="SUM"),
        artifacts_dir=tmp_path,
        context_window=10,
        cfg=cfg,
        on_pre_compact=hook,
    )
    result = compactor.compact(messages, None, manual=True)
    assert result.summarized is True
    assert calls == ["manual"]


def test_on_pre_compact_called_before_summary_auto(tmp_path):
    """on_pre_compact is called with trigger='auto' before L2 summary (manual=False)."""
    cfg = _cfg(
        char_per_token=1.0,
        reserved_output=0,
        auto_margin=0,
        recent_keep_tokens=1,
        recent_keep_min_messages=1,
    )
    calls: list[str] = []

    def hook(trigger: str) -> None:
        calls.append(trigger)

    messages = [_user("e" * 100), _assistant("a" * 100), _user("tail")]
    compactor = Compactor(
        FakeProvider(summary_text="SUM"),
        artifacts_dir=tmp_path,
        context_window=10,
        cfg=cfg,
        on_pre_compact=hook,
    )
    result = compactor.compact(messages, None, manual=False)
    assert result.summarized is True
    assert calls == ["auto"]


def test_on_pre_compact_none_does_not_call(tmp_path):
    """on_pre_compact=None (default) → not called, no crash, behavior identical."""
    cfg = _cfg(
        char_per_token=1.0,
        reserved_output=0,
        auto_margin=0,
        recent_keep_tokens=1,
        recent_keep_min_messages=1,
    )
    messages = [_user("e" * 100), _assistant("a" * 100), _user("tail")]
    # No on_pre_compact kwarg (default)
    compactor = Compactor(
        FakeProvider(summary_text="SUM"),
        artifacts_dir=tmp_path,
        context_window=10,
        cfg=cfg,
    )
    result = compactor.compact(messages, None, manual=True)
    assert result.summarized is True  # same result as before
