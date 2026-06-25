"""v0.11 · skills package entry point (task T126/T127/T128)

Exports public symbols of the Skill system:
- Data models: Skill, SkillMode
- Registry: SkillRegistry
- Loader: discover_skills, parse_skill, render_body

Layering rule: pure leaf package, stdlib only + typing / dataclasses / enum / pathlib / re /
importlib.resources.
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
