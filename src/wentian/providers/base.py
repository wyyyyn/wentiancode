"""Unified stream events and Provider ABC.

This module is the sole contract between the providers layer and the rest of
the application (session, REPL, render). All other modules import only from
here — never from concrete provider implementations.

Stdlib-only: no third-party imports.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal, TypedDict

__all__ = [
    "Usage",
    "ThinkingDelta",
    "TextDelta",
    "Done",
    "StreamEvent",
    "Message",
    "Provider",
]


# ---------------------------------------------------------------------------
# Token usage
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Usage:
    """Token usage reported at the end of a stream."""
    input_tokens: int
    output_tokens: int


# ---------------------------------------------------------------------------
# Stream events
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ThinkingDelta:
    """Incremental chunk of the model's reasoning/thinking text."""
    text: str


@dataclass(frozen=True, slots=True)
class TextDelta:
    """Incremental chunk of the model's visible response text."""
    text: str


@dataclass(frozen=True, slots=True)
class Done:
    """Signals end of stream; optionally carries token usage."""
    usage: Usage | None = None


# Union type exported for type annotations in upper layers.
StreamEvent = ThinkingDelta | TextDelta | Done


# ---------------------------------------------------------------------------
# Message type
# ---------------------------------------------------------------------------

class Message(TypedDict):
    """A single conversation turn.  Only user and assistant roles are needed
    here; openai_compat injects system messages internally when building the
    request payload.
    """
    role: Literal["user", "assistant"]
    content: str


# ---------------------------------------------------------------------------
# Provider ABC
# ---------------------------------------------------------------------------

class Provider(ABC):
    """Abstract base for all LLM backends.

    Concrete implementations (AnthropicProvider, OpenAICompatProvider, …) live
    in sibling modules; only this ABC is imported by upper layers.

    Subclasses MUST set ``name`` (as a class attribute or in ``__init__``);
    it is the human-readable provider name shown by the /provider command.

    v0.2 · C1 · F13（任务 T16）
    """

    #: Human-readable provider name — subclasses MUST set this.
    name: str

    #: Active model identifier; set by subclasses in ``__init__`` from cfg.model.
    model: str = ""

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
    ) -> Iterator[StreamEvent]:
        """Issue a streaming request carrying the full message history.

        Yields events in order; the final event MUST be ``Done``.
        """
        ...
