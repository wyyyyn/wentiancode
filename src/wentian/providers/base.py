"""Unified stream events and Provider ABC.

This module is the sole contract between the providers layer and the rest of
the application (session, REPL, render). All other modules import only from
here — never from concrete provider implementations.

Stdlib-only: no third-party imports.

v0.3 adds the protocol-neutral tool contract (ToolSpec / ToolCallEvent /
Done.raw_content / tool-role messages / Provider.stream tools= kwarg). With
``tools=None`` the contract is byte-for-byte equivalent to v0.2.
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
    "ToolSpec",
    "ToolCallEvent",
    "ToolCallDict",
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
# Tool declaration
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ToolSpec:
    """v0.3 · 契约 · F19/F22/F28（任务 T30）

    Protocol-neutral declaration of a tool the model may call.

    ``parameters`` is a JSON Schema dict describing the tool's arguments; each
    concrete provider translates this into its own wire format (Anthropic
    ``input_schema`` / OpenAI ``function.parameters``).  frozen guards against
    reassignment; ``parameters`` being an (unhashable) dict is fine because
    frozen only blocks attribute assignment, not mutation of contained dicts.
    """
    name: str
    description: str
    parameters: dict


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
class ToolCallEvent:
    """v0.3 · 契约 · F19/F22/F28（任务 T30）

    A fully-assembled tool call the model wants executed.

    ``arguments`` is the parsed argument dict, or ``None`` when the model's
    argument JSON could not be parsed (callers treat None as a hard error).
    frozen guards reassignment; ``arguments`` being a dict (unhashable) is fine
    because frozen only blocks attribute assignment.
    """
    id: str
    name: str
    arguments: dict | None


@dataclass(frozen=True, slots=True)
class Done:
    """Signals end of stream; optionally carries token usage.

    ``raw_content`` (v0.3) carries the provider's original assistant content
    blocks (e.g. Anthropic thinking + tool_use blocks) so a follow-up turn can
    replay them verbatim during the tool-result continuation.  It is None when
    no raw content needs preserving (the v0.2 behaviour).
    """
    usage: Usage | None = None
    raw_content: list[dict] | None = None


# Union type exported for type annotations in upper layers.
StreamEvent = ThinkingDelta | TextDelta | ToolCallEvent | Done


# ---------------------------------------------------------------------------
# Message type
# ---------------------------------------------------------------------------

class ToolCallDict(TypedDict):
    """v0.3 · 契约 · F19/F22/F28（任务 T30）

    Serialized form of a tool call stored on an assistant ``Message``.
    ``arguments`` is always a parsed dict here (unparseable calls never reach
    the stored history).
    """
    id: str
    name: str
    arguments: dict


class Message(TypedDict, total=False):
    """A single conversation turn.

    v0.2 used only ``user`` / ``assistant`` text turns; openai_compat injects
    system messages internally when building the request payload.

    v0.3 adds the ``tool`` role and tool-call fields.  Declared with
    ``total=False`` so each turn carries only the keys it needs:

    - ``role`` — ``user`` / ``assistant`` / ``tool`` (v0.3 adds ``tool``).
    - ``content`` — the textual content of the turn.
    - ``tool_calls`` — (v0.3, assistant turns) tool calls the model requested.
    - ``tool_call_id`` — (v0.3, tool turns) id of the call this result answers.
    - ``is_error`` — (v0.3, tool turns) marks the result as an error.
    - ``raw_content`` — (v0.3, assistant turns) provider-native content blocks
      preserved for faithful continuation (e.g. Anthropic thinking + tool_use).
    """
    role: Literal["user", "assistant", "tool"]
    content: str
    tool_calls: list[ToolCallDict]
    tool_call_id: str
    is_error: bool
    raw_content: list[dict]


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
        tools: list[ToolSpec] | None = None,
    ) -> Iterator[StreamEvent]:
        """Issue a streaming request carrying the full message history.

        Yields events in order; the final event MUST be ``Done``.

        ``tools`` (v0.3) declares the tools the model may call.  When
        ``tools`` is None the behaviour is identical to v0.2 — no tool
        capability is advertised and no ``ToolCallEvent`` is ever emitted.
        """
        ...
