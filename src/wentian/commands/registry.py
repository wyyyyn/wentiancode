"""v0.10 · C86 · F70/N34/N37 (task T110) — command registry.

Layering rule: pure leaf module, stdlib + same-package spec only.
Not thread-safe (same as ToolRegistry: constructed at startup, read-only afterward).
"""

from __future__ import annotations

from wentian.commands.spec import CommandSpec

__all__ = ["CommandRegistry"]


class CommandRegistry:
    """Stores :class:`~wentian.commands.spec.CommandSpec` in registration order,
    with case-insensitive lookup and completion by name or alias.

    Conflict rule: if any key in a new entry overlaps with an already-registered
    canonical name or alias, :meth:`register` raises :class:`ValueError` immediately.

    Thread-safety: not required (constructed at startup, read-only afterward,
    following ToolRegistry convention).
    """

    def __init__(self) -> None:
        # key: canonical name / alias (all lowercase) → CommandSpec
        self._by_key: dict[str, CommandSpec] = {}
        # insertion-order list (deduplicated; each spec appears exactly once)
        self._order: list[CommandSpec] = []

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def register(self, spec: CommandSpec) -> None:
        """Register a command spec.

        Parameters
        ----------
        spec:
            The :class:`CommandSpec` to register.

        Raises
        ------
        ValueError
            Any key in ``spec.name`` or ``spec.aliases`` is already taken.
        """
        all_keys = [spec.name.lower()] + [a.lower() for a in spec.aliases]
        for key in all_keys:
            if key in self._by_key:
                raise ValueError(
                    f"Command key {key!r} is already registered (canonical name or alias conflict)."
                    f" New command: {spec.name!r}, existing command: {self._by_key[key].name!r}."
                )
        for key in all_keys:
            self._by_key[key] = spec
        self._order.append(spec)

    def unregister(self, name: str) -> None:
        """Remove the command whose canonical name == *name* (case-insensitive).

        Also removes all alias keys and the entry in the ordered list. No-op
        (no exception) if the command does not exist.

        v0.11 · C107b (task T134b) — provides a "reversible registration" foundation
        for conflict-replacement of Skill slash commands (PROMPT same-name override)
        and old-command cleanup during ``/skills reload``.
        Additive: does not change existing semantics of register/lookup/visible/completions.
        """
        key = name.lower()
        spec = self._by_key.get(key)
        if spec is None:
            return
        # Remove all keys for this spec (canonical name + aliases) — compare by
        # spec identity to avoid accidentally deleting other commands that share
        # the same alias name (registration conflict checks already guarantee
        # unique key mapping; identity check here as a safeguard).
        all_keys = [spec.name.lower()] + [a.lower() for a in spec.aliases]
        for k in all_keys:
            if self._by_key.get(k) is spec:
                del self._by_key[k]
        # Remove by identity from the ordered list (each spec appears exactly once).
        self._order[:] = [s for s in self._order if s is not spec]

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def lookup(self, name: str) -> CommandSpec | None:
        """Look up a command by name (case-insensitive); returns ``None`` if not found."""
        return self._by_key.get(name.lower())

    def visible(self) -> list[CommandSpec]:
        """Return commands with ``hidden=False``, in registration order."""
        return [s for s in self._order if not s.hidden]

    def all(self) -> list[CommandSpec]:
        """Return all commands in registration order."""
        return list(self._order)

    def completions(self, prefix: str) -> list[CommandSpec]:
        """Return the list of visible :class:`CommandSpec` whose canonical name starts with *prefix* (in registration order).

        Parameters
        ----------
        prefix:
            Completion prefix (case-insensitive); empty string returns all visible command specs.
        """
        p = prefix.lower()
        return [s for s in self._order if not s.hidden and s.name.startswith(p)]
