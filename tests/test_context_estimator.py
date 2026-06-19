"""Tests for context/estimator.py: char_estimate + estimate_total.

v0.8 · C47 · F56（任务 T90）

Pure offline tests: the estimator is a leaf module (stdlib + Message type only).
"""

import math

from wentian.context.estimator import char_estimate, estimate_total


# --- char_estimate ---


def test_char_estimate_empty_list_is_zero():
    """Empty message list estimates to 0 tokens."""
    assert char_estimate([]) == 0


def test_char_estimate_single_message_ceil_divides_by_ratio():
    """A single message's content chars / char_per_token, rounded up."""
    msg = {"role": "user", "content": "x" * 10}
    # default char_per_token = 3.5 → ceil(10 / 3.5) == 3
    assert char_estimate([msg]) == math.ceil(10 / 3.5)
    assert char_estimate([msg]) == 3


def test_char_estimate_accumulates_multiple_messages():
    """Multiple messages: chars accumulate, then a single ceil division."""
    msgs = [
        {"role": "user", "content": "a" * 7},
        {"role": "assistant", "content": "b" * 7},
    ]
    # 14 chars total / 3.5 == 4 exactly
    assert char_estimate(msgs) == 4


def test_char_estimate_char_per_token_configurable():
    """char_per_token is configurable and changes the result."""
    msg = {"role": "user", "content": "x" * 12}
    assert char_estimate([msg], char_per_token=4.0) == math.ceil(12 / 4.0)
    assert char_estimate([msg], char_per_token=4.0) == 3
    assert char_estimate([msg], char_per_token=2.0) == 6


def test_char_estimate_counts_tool_call_arguments():
    """tool_calls arguments serialized length is folded into the char count."""
    plain = {"role": "assistant", "content": "hi"}
    with_calls = {
        "role": "assistant",
        "content": "hi",
        "tool_calls": [
            {"id": "c1", "name": "read", "arguments": {"path": "/tmp/longish/file"}}
        ],
    }
    assert char_estimate([with_calls]) > char_estimate([plain])


def test_char_estimate_missing_content_is_zero_contribution():
    """A message with no content contributes 0 chars (no crash)."""
    assert char_estimate([{"role": "assistant"}]) == 0


# --- estimate_total ---


def test_estimate_total_is_prompt_plus_char_estimate():
    """estimate_total == prompt_total + char_estimate(new_messages)."""
    new = [{"role": "user", "content": "x" * 14}]
    assert estimate_total(1000, new) == 1000 + char_estimate(new)
    assert estimate_total(1000, new) == 1000 + 4


def test_estimate_total_empty_new_messages_equals_prompt():
    """With no new messages, total equals the prompt anchor."""
    assert estimate_total(500, []) == 500


def test_estimate_total_char_per_token_forwarded():
    """char_per_token forwards to the underlying char_estimate."""
    new = [{"role": "user", "content": "x" * 12}]
    assert estimate_total(100, new, char_per_token=4.0) == 100 + 3
