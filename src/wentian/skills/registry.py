"""v0.11 · C102 · F73（任务 T128）— Skill 注册中心（叶子模块）。

``SkillRegistry`` 以 name 为键持有一组 Skill：

- ``add`` 同名覆盖（高层覆盖低层，由调用方按 低→高 顺序加入控制优先级）。
- ``get`` 按名取，缺失返回 None。
- ``list`` 按 name 升序、稳定返回。
- ``menu`` 输出 ((name, description), ...)，喂给 PromptContext.available_skills。

分层铁律：纯叶子模块，仅 stdlib + 同包 ``wentian.skills.base`` import——
零 rich / prompt_toolkit / 后端 SDK，也不反向依赖 wentian.agent / wentian.repl /
wentian.providers / wentian.commands。
"""

from __future__ import annotations

from wentian.skills.base import Skill


class SkillRegistry:
    """以 name 为键的 Skill 集合。"""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def add(self, skill: Skill) -> None:
        """按 name 加入；同名覆盖（后者胜，调用方控制 低→高 顺序）。"""
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        """按 name 取 Skill；缺失返回 None。"""
        return self._skills.get(name)

    def list(self) -> list[Skill]:
        """按 name 升序、稳定返回所有 Skill。"""
        return [self._skills[name] for name in sorted(self._skills)]

    def menu(self) -> tuple[tuple[str, str], ...]:
        """返回 ((name, description), ...)，按 name 升序。"""
        return tuple((s.name, s.description) for s in self.list())
