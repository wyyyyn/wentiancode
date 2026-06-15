"""OpenAI-compatible provider — streams via the openai SDK.

Supports any OpenAI-compatible chat-completions endpoint (e.g. DeepSeek).
Client is constructed lazily on the first stream() call and cached so that
constructing the provider is cheap and testable without a real API key.
"""

from __future__ import annotations

import json
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
    ToolCallEvent,
    ToolSpec,
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
        tools: list[ToolSpec] | None = None,
    ) -> Iterator[StreamEvent]:
        """Yield ThinkingDelta / TextDelta / ToolCallEvent events then a final Done.

        v0.3 · C11 · F22（任务 T39）

        When ``tools`` is provided, each ToolSpec is translated into the OpenAI
        ``function`` wire format and advertised on the request; streaming
        ``delta.tool_calls`` fragments are accumulated per ``index`` and emitted
        as fully-assembled ToolCallEvents (in index order) just before Done.
        With ``tools=None`` the behaviour is byte-for-byte identical to v0.2.
        """
        client = self._get_client()
        payload = self._build_messages(messages, system=system)

        create_kwargs: dict = {
            "model": self._cfg.model,
            "messages": payload,
            "stream": True,
        }
        if tools is not None:
            create_kwargs["tools"] = [self._tool_to_wire(t) for t in tools]

        resp = client.chat.completions.create(**create_kwargs)

        last_usage: Usage | None = None
        # Accumulate tool-call fragments per delta index: {id, name, fragments}.
        tool_acc: dict[int, dict] = {}

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

                # Tool-call fragments — getattr-guarded for compat servers that
                # never expose this attribute.
                self._accumulate_tool_calls(delta, tool_acc)

        # Emit assembled tool calls in ascending index order, then Done.
        for index in sorted(tool_acc):
            yield self._build_tool_call_event(tool_acc[index])

        yield Done(usage=last_usage)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _tool_to_wire(spec: ToolSpec) -> dict:
        """Translate a neutral ToolSpec into OpenAI function wire format.

        v0.3 · C11 · F22（任务 T39）
        """
        return {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            },
        }

    @staticmethod
    def _accumulate_tool_calls(delta: object, acc: dict[int, dict]) -> None:
        """Fold streaming delta.tool_calls fragments into the accumulator.

        v0.3 · C11 · F22（任务 T39）

        Fragments are grouped by ``.index``; the first fragment of a call
        carries ``.id`` and ``.function.name``, later fragments carry
        ``.function.arguments`` string pieces to concatenate.  All field
        access is getattr-guarded so compat servers omitting attributes (or
        the field entirely) never crash the stream.
        """
        fragments = getattr(delta, "tool_calls", None)
        if not fragments:
            return
        for frag in fragments:
            index = getattr(frag, "index", 0)
            slot = acc.setdefault(index, {"id": None, "name": None, "args": ""})
            frag_id = getattr(frag, "id", None)
            if frag_id is not None:
                slot["id"] = frag_id
            func = getattr(frag, "function", None)
            if func is not None:
                name = getattr(func, "name", None)
                if name is not None:
                    slot["name"] = name
                args = getattr(func, "arguments", None)
                if args:
                    slot["args"] += args

    @staticmethod
    def _build_tool_call_event(slot: dict) -> ToolCallEvent:
        """Build a ToolCallEvent from an accumulator slot.

        v0.3 · C11 · F22（任务 T39）

        Parses the concatenated argument JSON; unparseable JSON yields
        ``arguments=None`` rather than raising.
        """
        raw_args = slot["args"]
        try:
            parsed: dict | None = json.loads(raw_args) if raw_args else {}
        except (ValueError, TypeError):
            parsed = None
        return ToolCallEvent(
            id=slot["id"] or "",
            name=slot["name"] or "",
            arguments=parsed,
        )

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
        """Convert neutral Message history into OpenAI chat wire format.

        v0.3 · C11 · F22（任务 T40）

        Prepends a system message when provided (unchanged from v0.2), then
        translates each turn:

        - assistant turns with ``tool_calls`` emit OpenAI ``tool_calls`` with
          ``type:"function"`` and JSON-serialized ``arguments``;
        - ``tool`` turns emit ``{role, tool_call_id, content}``, prefixing the
          content with ``[error] `` when ``is_error`` is set;
        - plain user/assistant text turns pass through (v0.2 behaviour).

        Provider-native fields like ``raw_content`` are dropped — they have no
        place in the OpenAI wire format.
        """
        out: list[dict] = []
        if system is not None:
            out.append({"role": "system", "content": system})
        for msg in messages:
            out.append(self._message_to_wire(msg))
        return out

    @staticmethod
    def _message_to_wire(msg: Message) -> dict:
        """Translate a single neutral Message into OpenAI wire format.

        v0.3 · C11 · F22（任务 T40）
        """
        role = msg["role"]

        if role == "tool":
            content = msg.get("content", "")
            if msg.get("is_error"):
                content = f"[error] {content}"
            return {
                "role": "tool",
                "tool_call_id": msg.get("tool_call_id", ""),
                "content": content,
            }

        tool_calls = msg.get("tool_calls")
        if tool_calls:
            return {
                "role": role,
                "content": msg.get("content"),
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["arguments"]),
                        },
                    }
                    for tc in tool_calls
                ],
            }

        # Plain text turn — pass content through (v0.2 behaviour), dropping any
        # provider-native fields such as raw_content.
        return {"role": role, "content": msg.get("content")}

    @staticmethod
    def _extract_usage(chunk: object) -> Usage | None:
        """Map chunk.usage to Usage, or None when absent/incomplete.

        v0.5 · C24 · F38/F40（任务 T63）

        Also reads prompt_tokens_details.cached_tokens and maps it to
        cache_read_input_tokens.  cache_creation_input_tokens is always 0
        for this protocol (no explicit cache-write concept).

        Defensive access: prompt_tokens_details may be absent entirely, or
        cached_tokens may be absent within it.  Both the SDK object form
        (getattr) and dict form (.get()) are handled.
        """
        usage_raw = getattr(chunk, "usage", None)
        if usage_raw is None:
            return None
        pt = getattr(usage_raw, "prompt_tokens", None)
        ct = getattr(usage_raw, "completion_tokens", None)
        if pt is None or ct is None:
            return None

        # Resolve cached_tokens: details may be an SDK object or a plain dict.
        cached_read = 0
        details = getattr(usage_raw, "prompt_tokens_details", None)
        if details is not None:
            if isinstance(details, dict):
                cached_read = details.get("cached_tokens", 0) or 0
            else:
                cached_read = getattr(details, "cached_tokens", None) or 0

        return Usage(
            input_tokens=pt,
            output_tokens=ct,
            cache_read_input_tokens=cached_read,
        )
