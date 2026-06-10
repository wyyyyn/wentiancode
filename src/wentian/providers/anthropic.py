"""Anthropic provider skeleton.

Stores provider config and declares the Provider interface.
Actual streaming is implemented in T11.

No anthropic SDK import here — kept import-light until T11.
"""

from __future__ import annotations

from collections.abc import Iterator

from wentian.config import ProviderConfig
from wentian.providers.base import Message, Provider, StreamEvent


class AnthropicProvider(Provider):
    """Provider backed by the Anthropic Messages API."""

    def __init__(self, cfg: ProviderConfig) -> None:
        self._cfg = cfg
        self.name = cfg.name

    def stream(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
    ) -> Iterator[StreamEvent]:
        raise NotImplementedError("AnthropicProvider.stream() is implemented in T11")
