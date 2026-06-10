"""Anthropic provider — streams via the official anthropic SDK.

Client is constructed lazily on the first stream() call and cached so that
constructing the provider is cheap and testable without a real API key.
"""

from __future__ import annotations

from collections.abc import Iterator

import anthropic

from wentian.config import ProviderConfig
from wentian.providers.base import (
    Done,
    Message,
    Provider,
    StreamEvent,
    TextDelta,
    ThinkingDelta,
    Usage,
)


class AnthropicProvider(Provider):
    """Provider backed by the Anthropic Messages API."""

    def __init__(self, cfg: ProviderConfig) -> None:
        self._cfg = cfg
        self.name = cfg.name
        self._client: anthropic.Anthropic | None = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def stream(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
    ) -> Iterator[StreamEvent]:
        """Yield ThinkingDelta / TextDelta events then a final Done."""
        client = self._get_client()
        kwargs = self._build_kwargs(messages, system=system)

        with client.messages.stream(**kwargs) as sdk_stream:
            for event in sdk_stream:
                mapped = self._map_event(event)
                if mapped is not None:
                    yield mapped
            final = sdk_stream.get_final_message()

        usage = Usage(
            input_tokens=final.usage.input_tokens,
            output_tokens=final.usage.output_tokens,
        )
        yield Done(usage=usage)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> anthropic.Anthropic:
        """Return the cached client, constructing it on first call."""
        if self._client is None:
            client_kwargs: dict = {"api_key": self._cfg.api_key}
            if self._cfg.base_url is not None:
                client_kwargs["base_url"] = self._cfg.base_url
            self._client = anthropic.Anthropic(**client_kwargs)
        return self._client

    def _build_kwargs(
        self,
        messages: list[Message],
        *,
        system: str | None,
    ) -> dict:
        """Assemble kwargs for client.messages.stream()."""
        kwargs: dict = {
            "model": self._cfg.model,
            "max_tokens": 64000,
            "messages": messages,
        }
        if system is not None:
            kwargs["system"] = system
        if self._cfg.thinking:
            kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
        return kwargs

    @staticmethod
    def _map_event(event: object) -> StreamEvent | None:
        """Map a raw SDK event to a unified StreamEvent, or None to skip."""
        if getattr(event, "type", None) != "content_block_delta":
            return None
        delta = event.delta  # type: ignore[attr-defined]
        delta_type = getattr(delta, "type", None)
        if delta_type == "thinking_delta":
            return ThinkingDelta(delta.thinking)
        if delta_type == "text_delta":
            return TextDelta(delta.text)
        return None
