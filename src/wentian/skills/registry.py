"""v0.11 · C102 · F73 (task T128) — Skill registry (leaf module).

``SkillRegistry`` holds a set of Skills keyed by name:

- ``add`` overwrites on duplicate name (higher layer overrides lower; caller controls priority by adding in low→high order).
- ``get`` retrieves by name, returns None if missing.
- ``list`` returns all skills in stable ascending order by name.
- ``menu`` outputs ((name, description), ...), fed to PromptContext.available_skills.

Layering rule: pure leaf module, only stdlib + same-package ``wentian.skills.base`` import —
no rich / prompt_toolkit / backend SDK, and no reverse dependency on wentian.agent / wentian.repl /
wentian.providers / wentian.commands.
"""

from __future__ import annotations

from wentian.skills.base import Skill


class SkillRegistry:
    """Collection of Skills keyed by name."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def add(self, skill: Skill) -> None:
        """Add by name; duplicate name overwrites (last wins; caller controls low→high order)."""
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        """Retrieve Skill by name; returns None if missing."""
        return self._skills.get(name)

    def list(self) -> list[Skill]:
        """Return all Skills in stable ascending order by name."""
        return [self._skills[name] for name in sorted(self._skills)]

    def menu(self) -> tuple[tuple[str, str], ...]:
        """Return ((name, description), ...) in ascending order by name."""
        return tuple((s.name, s.description) for s in self.list())
