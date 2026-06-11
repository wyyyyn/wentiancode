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
    """Provider for any OpenAI-compatible chat-completions endpoint.

    v0.2 · C1 · F13（任务 T16）
    """

    def __init__(self, cfg: ProviderConfig) -> None:
        """Initialise provider; client is created lazily on first stream() call.

        v0.2 · C1 · F13（任务 T16）
        """
        self._cfg = cfg
        self.name = cfg.name
        self.model = cfg.model
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

        # openai Stream is a context manager; the with-block ensures the HTTP
        # response is closed even if this generator is abandoned early.
        with resp:
            for chunk in resp:
                usage = self._extract_usage(chunk)
                if usage is not None:
                    last_usage = usage

                # Real OpenAI/DeepSeek streams deliver final usage on a
                # trailing empty-choices chunk — skip event mapping for those.
                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta

                # DeepSeek-style reasoning field — use getattr for compat
                # servers that don't expose this attribute at all
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    yield ThinkingDelta(text=reasoning)

                content = delta.content
                if content:
                    yield TextDelta(text=content)

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

    @staticmethod
    def _extract_usage(chunk: object) -> Usage | None:
        """Map chunk.usage to Usage, or None when absent/incomplete."""
        usage_raw = getattr(chunk, "usage", None)
        if usage_raw is None:
            return None
        pt = getattr(usage_raw, "prompt_tokens", None)
        ct = getattr(usage_raw, "completion_tokens", None)
        if pt is None or ct is None:
            return None
        return Usage(input_tokens=pt, output_tokens=ct)
