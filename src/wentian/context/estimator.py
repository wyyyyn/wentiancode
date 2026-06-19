"""Approximate token estimation — no precise tokenizer.

v0.8 · C47 · F56（任务 T90）

Two pure, IO-free functions:

- ``char_estimate(messages, char_per_token)`` — sum each message's content
  character count (plus a rough count of any ``tool_calls`` arguments, serialized
  via JSON), then ceil-divide by ``char_per_token``. Empty input → 0.
- ``estimate_total(prompt_total, new_messages, char_per_token)`` — the provider's
  real prompt-token anchor plus the char estimate of messages appended since the
  anchor.

Leaf module: stdlib only plus the ``Message`` type from ``providers.base``.
"""

from __future__ import annotations

import json
import math

from wentian.providers.base import Message

__all__ = ["char_estimate", "estimate_total"]

DEFAULT_CHAR_PER_TOKEN = 3.5


def _message_chars(message: Message) -> int:
    """Count the characters a single message contributes to the estimate.

    Counts the textual ``content`` plus the serialized length of any
    ``tool_calls`` arguments (a rough proxy for the tokens the wire payload
    carries). Missing fields contribute nothing.
    """
    chars = len(message.get("content") or "")
    for call in message.get("tool_calls") or []:
        arguments = call.get("arguments")
        if arguments:
            chars += len(json.dumps(arguments, ensure_ascii=False))
    return chars


def char_estimate(
    messages: list[Message],
    char_per_token: float = DEFAULT_CHAR_PER_TOKEN,
) -> int:
    """Estimate tokens for ``messages`` by character count.

    Sums each message's content characters (folding in tool-call arguments),
    then ceil-divides the total by ``char_per_token``. An empty list → 0.
    """
    total_chars = sum(_message_chars(m) for m in messages)
    if total_chars == 0:
        return 0
    return math.ceil(total_chars / char_per_token)


def estimate_total(
    prompt_total: int,
    new_messages: list[Message],
    char_per_token: float = DEFAULT_CHAR_PER_TOKEN,
) -> int:
    """Total estimate = real prompt anchor + char estimate of new messages."""
    return prompt_total + char_estimate(new_messages, char_per_token=char_per_token)
