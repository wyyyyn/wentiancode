"""v0.5 · C21/C22 (task T59/T60) — system prompt assembly and dynamic reminder injection package.

Submodules (all pure functions, no backend SDK / no rich / no prompt_toolkit dependencies):

- ``system``    — ordered assembly of seven fixed modules + optional slots: ``build_system_prompt``.
- ``reminders`` — ``<system-reminder>`` injection for environment info and session switches:
  ``EnvInfo`` / ``build_request_decorator`` (assembled at request time, never persisted).

No re-exports in ``__init__``; callers import by full path (e.g.
``from wentian.prompt.system import build_system_prompt``) — avoids write conflicts
on this file during parallel development, and makes dependencies explicit at the import site.
"""

from __future__ import annotations

__all__: list[str] = []
