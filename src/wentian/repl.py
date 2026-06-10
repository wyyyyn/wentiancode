"""REPL — main interactive loop for wentian.

Responsibilities:
- Read user input; run one chat round per non-empty, non-slash line.
- Maintain conversation history in the session; persist after each round.
- All dependencies injected: provider, session, store, renderer, console,
  input_fn — fully testable offline.

No anthropic/openai/yaml imports here.
"""
from __future__ import annotations

from collections.abc import Callable

from rich.console import Console

from wentian.providers.base import Message, Provider
from wentian.render import Renderer
from wentian.session import Session, SessionStore

__all__ = ["REPL"]

_PROMPT = "文天> "


class REPL:
    """Interactive REPL.

    Parameters
    ----------
    provider:
        Active LLM backend.
    session:
        Active conversation session.
    store:
        SessionStore for persistence.
    renderer:
        Renderer for displaying stream events.
    provider_factory:
        Callable(name) -> Provider — reserved for /provider command.
    input_fn:
        Callable used to read a line of user input (default: builtins.input).
    system:
        Optional system prompt passed to provider.stream(). Default None.
    """

    def __init__(
        self,
        provider: Provider,
        session: Session,
        store: SessionStore,
        renderer: Renderer,
        *,
        provider_factory: Callable[[str], Provider],
        input_fn: Callable[..., str] = input,
        system: str | None = None,
    ) -> None:
        self._provider = provider
        self._session = session
        self._store = store
        self._renderer = renderer
        self._provider_factory = provider_factory
        self._input_fn = input_fn
        self._system = system
        self._console: Console = renderer._console

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Enter the REPL loop; returns when the user types /exit or sends EOF."""
        while True:
            try:
                raw = self._input_fn(_PROMPT)
            except (EOFError, KeyboardInterrupt):
                self._console.print()
                return

            line = raw.strip()

            if not line:
                continue

            if line == "/exit":
                return

            if not line.startswith("/"):
                self._chat_once(line)

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    def _chat_once(self, user_text: str) -> None:
        """Run one conversation turn.

        Appends the user message, calls the provider, renders the stream,
        appends the assistant message, and saves.
        """
        user_msg: Message = {"role": "user", "content": user_text}
        self._session.messages.append(user_msg)

        events = self._provider.stream(
            self._session.messages, system=self._system
        )
        body = self._renderer.render_stream(events)

        assistant_msg: Message = {"role": "assistant", "content": body}
        self._session.messages.append(assistant_msg)
        self._store.save(self._session)
