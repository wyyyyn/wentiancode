"""v0.10 · C90 · F75 (task T113) — slash command Tab completer.

``CommandCompleter`` extends ``prompt_toolkit.completion.Completer``,
providing command name candidates for input starting with ``/``,
displaying the canonical name (``/cmd``) and command summary.

Layering rules:
- Lives in the ui layer, may import prompt_toolkit.
- Retrieves candidates via **duck-typed** registry (only calls ``.completions``) —
  the ``commands/`` package must never depend on this file (direction: ui→commands, not reversed).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

__all__ = ["CommandCompleter"]


class CommandCompleter(Completer):
    """Slash command completer.

    Generates candidates only for input starting with ``/`` where no space has
    been typed yet (i.e., still in the command name entry phase).
    Each candidate:

    - ``text``           — canonical name (no slash, used to replace the prefix before the cursor)
    - ``start_position`` — ``-len(prefix)`` (replaces the already-typed prefix characters)
    - ``display``        — ``/name`` (with slash, used for menu display)
    - ``display_meta``   — command summary (one-line description)

    Parameters
    ----------
    registry:
        Duck-typed command registry, must provide:

        - ``.completions(prefix: str) -> list[CommandSpec]``
          Returns a list of CommandSpec items whose canonical name starts with *prefix*
          among visible commands (in registration order).
    """

    def __init__(self, registry: Any) -> None:
        self._registry = registry

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent | None,
    ) -> Iterator[Completion]:
        """Generate completion candidates.

        Logic:
        1. Get text before cursor (``document.text_before_cursor``).
        2. If not starting with ``/`` → skip (return).
        3. If contains space → skip (user is already typing arguments).
        4. Extract prefix = ``text[1:].lower()``; query registry for CommandSpec list.
        5. For each spec, directly read name/summary, yield ``Completion``.
        """
        text = document.text_before_cursor

        # Condition 1: not starting with "/"
        if not text.startswith("/"):
            return

        # Condition 2: contains space (already typing arguments)
        if " " in text:
            return

        prefix = text[1:].lower()  # strip "/" and lowercase

        for spec in self._registry.completions(prefix):
            yield Completion(
                text=spec.name,
                start_position=-len(prefix),
                display=f"/{spec.name}",
                display_meta=spec.summary,
            )
