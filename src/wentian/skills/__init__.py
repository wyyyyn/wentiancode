"""v0.11 · skills 包入口（任务 T126/T127/T128）

导出 Skill 系统的公共符号：
- 数据模型：Skill、SkillMode
- 注册中心：SkillRegistry
- 加载器：discover_skills、parse_skill、render_body

分层铁律：纯叶子包，仅 stdlib + typing / dataclasses / enum / pathlib / re /
importlib.resources。
"""

from __future__ import annotations

from wentian.skills.base import Skill, SkillMode
from wentian.skills.loader import discover_skills, parse_skill, render_body
from wentian.skills.registry import SkillRegistry

__all__ = [
    "Skill",
    "SkillMode",
    "SkillRegistry",
    "discover_skills",
    "parse_skill",
    "render_body",
]
