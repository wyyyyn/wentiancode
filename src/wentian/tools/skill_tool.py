"""v0.11 · C103 · F-skills (task T129)

LoadSkillTool — the system-level skill-loading tool exposed to the model.

When the model picks a Skill from the "Available Skills" menu it calls ``load_skill``;
this tool forwards the choice to an injected, duck-typed *activator* which knows
how to run the skill (shared mode injects the full instructions into following
turns; isolated mode runs a sub-conversation and returns a result summary).

Layering rule: this leaf only imports ``wentian.tools.base`` (+ the pure ``Category``
enum it re-exports through there is not needed) and stdlib. It MUST NOT import
``wentian.repl``, ``wentian.agent``, ``wentian.skills``, or any concrete
activator class — the activator is injected and used purely by duck typing.

Stdlib-only: no third-party imports (spec N6/N7).
"""

from __future__ import annotations

from wentian.permissions.decision import Category
from wentian.tools.base import Tool

__all__ = ["LoadSkillTool"]


class LoadSkillTool(Tool):
    """Load and activate a Skill chosen from the "Available Skills" menu.

    Constructed with a duck-typed *activator* exposing
    ``activate(name: str, args: str) -> str``. ``run`` validates the model's
    ``name`` argument, defaults ``args`` to the empty string, then delegates to
    the activator and returns its result text unchanged. On a missing/empty/
    non-string ``name`` it returns a structured error string rather than raising,
    so the model can self-correct on the next turn.
    """

    name = "load_skill"
    description = (
        "Load and activate a Skill (by name from the Available Skills menu). "
        "Shared mode injects the full instructions into subsequent turns; "
        "isolated mode runs a sub-conversation and returns a result summary."
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The Skill name to load, must exactly match the name in the Available Skills menu.",
            },
            "args": {
                "type": "string",
                "description": "Optional argument string to pass to the Skill. Treated as empty string when omitted.",
            },
        },
        "required": ["name"],
    }
    # Activating a skill is a context operation, not a side-effecting action.
    category = Category.READ_ONLY
    friendly_name = "LoadSkill"

    def __init__(self, activator: object) -> None:
        self._activator = activator

    def run(self, args: dict) -> str:
        raw_name = args.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            return (
                "Error: missing required parameter 'name'. "
                "Provide the Skill name exactly as it appears in the "
                '"Available Skills" menu.'
            )
        skill_name = raw_name.strip()

        raw_args = args.get("args", "")
        args_value = raw_args if isinstance(raw_args, str) else ""

        return self._activator.activate(skill_name, args_value)
