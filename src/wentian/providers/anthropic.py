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
    ToolCallEvent,
    ToolSpec,
    Usage,
)


class AnthropicProvider(Provider):
    """Provider backed by the Anthropic Messages API.

    v0.2 · C1 · F13（任务 T16）
    """

    def __init__(self, cfg: ProviderConfig) -> None:
        """Initialise provider; client is created lazily on first stream() call.

        v0.2 · C1 · F13（任务 T16）
        """
        self._cfg = cfg
        self.name = cfg.name
        self.model = cfg.model
        self._client: anthropic.Anthropic | None = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def stream(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
    ) -> Iterator[StreamEvent]:
        """Yield ThinkingDelta / TextDelta / ToolCallEvent events then a Done.

        ``tools`` (v0.3) declares the tools the model may call; when present
        each is translated to Anthropic's ``input_schema`` wire format.  After
        the streamed deltas, any ``tool_use`` blocks on the final message are
        emitted as ``ToolCallEvent``s and the full content blocks are preserved
        in ``Done.raw_content`` for faithful continuation.

        v0.3 · C11 · F22（任务 T37）
        """
        client = self._get_client()
        kwargs = self._build_kwargs(messages, system=system, tools=tools)

        with client.messages.stream(**kwargs) as sdk_stream:
            for event in sdk_stream:
                mapped = self._map_event(event)
                if mapped is not None:
                    yield mapped
            final = sdk_stream.get_final_message()

        content = getattr(final, "content", None) or []
        has_tool_use = False
        for block in content:
            if getattr(block, "type", None) == "tool_use":
                has_tool_use = True
                yield ToolCallEvent(
                    id=block.id,
                    name=block.name,
                    arguments=block.input,
                )

        usage = (
            Usage(
                input_tokens=final.usage.input_tokens,
                output_tokens=final.usage.output_tokens,
            )
            if final.usage
            else None
        )
        raw_content = (
            [block.model_dump() for block in content] if has_tool_use else None
        )
        yield Done(usage=usage, raw_content=raw_content)

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
        tools: list[ToolSpec] | None = None,
    ) -> dict:
        """Assemble kwargs for client.messages.stream().

        ``tools`` (v0.3) are translated to Anthropic's wire format
        (``name`` / ``description`` / ``input_schema``); when None the ``tools``
        key is omitted entirely (v0.2-equivalent payload).

        v0.3 · C11 · F22（任务 T37）
        """
        kwargs: dict = {
            "model": self._cfg.model,
            "max_tokens": 64000,
            "messages": messages,
        }
        if system is not None:
            kwargs["system"] = system
        if self._cfg.thinking:
            kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
        if tools is not None:
            kwargs["tools"] = [
                {
                    "name": spec.name,
                    "description": spec.description,
                    "input_schema": spec.parameters,
                }
                for spec in tools
            ]
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
