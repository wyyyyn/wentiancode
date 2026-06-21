"""v0.11 · C103 · F-skills（任务 T129）

LoadSkillTool — the system-level skill-loading tool exposed to the model.

When the model picks a Skill from the「可用 Skill」menu it calls ``load_skill``;
this tool forwards the choice to an injected, duck-typed *activator* which knows
how to run the skill (shared mode injects the full instructions into following
turns; isolated mode runs a sub-conversation and returns a result summary).

分层铁律: this leaf only imports ``wentian.tools.base`` (+ the pure ``Category``
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
    """Load and activate a Skill chosen from the「可用 Skill」menu.

    Constructed with a duck-typed *activator* exposing
    ``activate(name: str, args: str) -> str``. ``run`` validates the model's
    ``name`` argument, defaults ``args`` to the empty string, then delegates to
    the activator and returns its result text unchanged. On a missing/empty/
    non-string ``name`` it returns a structured error string rather than raising,
    so the model can self-correct on the next turn.
    """

    name = "load_skill"
    description = (
        "加载并激活一个 Skill（按「可用 Skill」菜单里的 name）。"
        "共享模式把完整指令注入后续轮次；"
        "独立模式跑一个子对话并返回其结果摘要。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "要加载的 Skill 名称，须与「可用 Skill」菜单里的 name 完全一致。",
            },
            "args": {
                "type": "string",
                "description": "传给 Skill 的可选参数字符串。省略时按空字符串处理。",
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
                "「可用 Skill」menu."
            )
        skill_name = raw_name.strip()

        raw_args = args.get("args", "")
        args_value = raw_args if isinstance(raw_args, str) else ""

        return self._activator.activate(skill_name, args_value)
