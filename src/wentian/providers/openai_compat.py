"""OpenAI-compatible provider — streams via the openai SDK.

Supports any OpenAI-compatible chat-completions endpoint (e.g. DeepSeek).
Client is constructed lazily on the first stream() call and cached so that
constructing the provider is cheap and testable without a real API key.
"""

from __future__ import annotations

from collections.abc import Iterator

from openai import OpenAI

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


class OpenAICompatProvider(Provider):
    """Provider for any OpenAI-compatible chat-completions endpoint."""

    def __init__(self, cfg: ProviderConfig) -> None:
        self._cfg = cfg
        self.name = cfg.name
        self._client: OpenAI | None = None

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
        payload = self._build_messages(messages, system=system)

        resp = client.chat.completions.create(
            model=self._cfg.model,
            messages=payload,
            stream=True,
        )

        last_usage: Usage | None = None

        for chunk in resp:
            # Some compat servers send empty-choices chunks (e.g. usage-only)
            if not chunk.choices:
                usage_raw = getattr(chunk, "usage", None)
                if usage_raw is not None:
                    pt = getattr(usage_raw, "prompt_tokens", None)
                    ct = getattr(usage_raw, "completion_tokens", None)
                    if pt is not None and ct is not None:
                        last_usage = Usage(input_tokens=pt, output_tokens=ct)
                continue

            delta = chunk.choices[0].delta

            # DeepSeek-style reasoning field — use getattr for compat servers
            # that don't expose this attribute at all
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                yield ThinkingDelta(text=reasoning)

            content = delta.content
            if content:
                yield TextDelta(text=content)

            # Track usage if carried on this chunk
            usage_raw = getattr(chunk, "usage", None)
            if usage_raw is not None:
                pt = getattr(usage_raw, "prompt_tokens", None)
                ct = getattr(usage_raw, "completion_tokens", None)
                if pt is not None and ct is not None:
                    last_usage = Usage(input_tokens=pt, output_tokens=ct)

        yield Done(usage=last_usage)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> OpenAI:
        """Return the cached client, constructing it on first call."""
        if self._client is None:
            client_kwargs: dict = {"api_key": self._cfg.api_key}
            if self._cfg.base_url is not None:
                client_kwargs["base_url"] = self._cfg.base_url
            self._client = OpenAI(**client_kwargs)
        return self._client

    def _build_messages(
        self,
        messages: list[Message],
        *,
        system: str | None,
    ) -> list[dict]:
        """Prepend a system message when provided."""
        if system is not None:
            return [{"role": "system", "content": system}] + list(messages)
        return list(messages)
