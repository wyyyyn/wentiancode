"""OpenAI-compatible provider skeleton.

Stores provider config and declares the Provider interface.
Actual streaming is implemented in T12.

No openai SDK import here — kept import-light until T12.
"""

from __future__ import annotations

from collections.abc import Iterator

from wentian.config import ProviderConfig
from wentian.providers.base import Message, Provider, StreamEvent


class OpenAICompatProvider(Provider):
    """Provider for any OpenAI-compatible chat-completions endpoint."""

    def __init__(self, cfg: ProviderConfig) -> None:
        self._cfg = cfg
        self.name = cfg.name

    def stream(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
    ) -> Iterator[StreamEvent]:
        raise NotImplementedError("OpenAICompatProvider.stream() is implemented in T12")
