"""v0.6 · C32 · F44（任务 T72）

Three-layer settings loader for the permission system. Reads the user-, project-
and local-level YAML config files, parses their ``permissions.allow`` /
``permissions.deny`` rule strings into :class:`~wentian.permissions.rules.Rule`
objects, and stacks them into a :class:`~wentian.permissions.rules.LayeredRules`
(precedence ``local > project > user``). The startup ``defaultMode`` is taken
from the same three files, first valid value in precedence order, else
:attr:`~wentian.permissions.decision.Mode.DEFAULT`.

File locations (XDG style, mirroring :mod:`wentian.config`):

* user-level   ``~/.config/wentian/settings.yaml`` (overridable via *user_path*)
* project-level ``<project_root>/.wentian/settings.yaml``
* local-level   ``<project_root>/.wentian/settings.local.yaml``

Degradation (N14): a missing file is an empty layer; a layer whose YAML fails to
parse or whose structure is malformed (e.g. ``permissions`` not a mapping)
degrades *that layer* to empty — never raising, never failing construction, and
never widening permissions. Other layers load normally.

分层铁律: pure leaf module — stdlib (``os`` / ``pathlib`` / ``dataclasses``)
plus ``yaml``, plus :mod:`wentian.permissions.decision` and
:mod:`wentian.permissions.rules`. No backend SDK, no terminal-UI libraries.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from wentian.permissions.decision import Mode, Verdict
from wentian.permissions.rules import FRIENDLY_TO_TOOL, LayeredRules, Rule, RuleSet

__all__ = ["Settings", "load_settings"]


@dataclass(frozen=True)
class Settings:
    """加载并合并三层配置后的运行期设置。"""

    rules: LayeredRules
    default_mode: Mode


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def _default_user_settings_path() -> Path:
    """XDG-aware user-level settings path (``~/.config/wentian/settings.yaml``)."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "wentian" / "settings.yaml"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_rule(spec: str, effect: Verdict) -> Rule | None:
    """把 ``Friendly(pattern)`` / ``Friendly`` 字符串解析成 :class:`Rule`。

    无法识别（非字符串、未知友好名、空）→ 返回 ``None``（跳过该条，不放权）。
    """
    if not isinstance(spec, str):
        return None
    text = spec.strip()
    if not text:
        return None

    if text.endswith(")") and "(" in text:
        friendly = text[: text.index("(")].strip()
        pattern: str | None = text[text.index("(") + 1 : -1].strip()
    else:
        friendly = text
        pattern = None

    if friendly not in FRIENDLY_TO_TOOL:
        return None
    return Rule(friendly=friendly, pattern=pattern, effect=effect)


def _parse_rule_list(raw: object, effect: Verdict) -> list[Rule]:
    """把一个 allow/deny 列表解析成 Rule 列表（非列表 → 空）。"""
    if not isinstance(raw, list):
        return []
    rules: list[Rule] = []
    for item in raw:
        rule = _parse_rule(item, effect)
        if rule is not None:
            rules.append(rule)
    return rules


def _load_layer(path: Path) -> tuple[RuleSet, Mode | None]:
    """加载单层配置文件 → (RuleSet, defaultMode|None)。

    缺失 / YAML 非法 / 结构错 → 降级为空集 + None（绝不抛）。
    """
    empty = (RuleSet(), None)
    if not path.exists():
        return empty

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return empty

    if raw is None:
        return empty
    if not isinstance(raw, dict):
        # whole document malformed -> degrade
        return empty

    # --- defaultMode ---
    mode: Mode | None = None
    raw_mode = raw.get("defaultMode")
    if isinstance(raw_mode, str):
        try:
            mode = Mode(raw_mode)
        except ValueError:
            mode = None

    # --- permissions block ---
    perms = raw.get("permissions")
    if not isinstance(perms, dict):
        # missing or malformed permissions -> empty rules (but keep mode)
        return RuleSet(), mode

    allow = _parse_rule_list(perms.get("allow"), Verdict.ALLOW)
    deny = _parse_rule_list(perms.get("deny"), Verdict.DENY)
    return RuleSet(allow=allow, deny=deny), mode


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_settings(project_root: Path, *, user_path: Path | None = None) -> Settings:
    """加载三层配置并合并为 :class:`Settings`。

    优先级 ``local > project > user``；同层 deny 优先 allow（见
    :class:`~wentian.permissions.rules.RuleSet`）。任何单层的缺失或格式错都降级
    为空集，绝不抛、绝不致构造失败（N14）。``defaultMode`` 取 local>project>user
    首个合法值，皆无则 :attr:`Mode.DEFAULT`。
    """
    user_file = user_path if user_path is not None else _default_user_settings_path()
    wt = Path(project_root) / ".wentian"
    project_file = wt / "settings.yaml"
    local_file = wt / "settings.local.yaml"

    user_rs, user_mode = _load_layer(user_file)
    project_rs, project_mode = _load_layer(project_file)
    local_rs, local_mode = _load_layer(local_file)

    rules = LayeredRules(user=user_rs, project=project_rs, local=local_rs)

    default_mode = Mode.DEFAULT
    for candidate in (local_mode, project_mode, user_mode):
        if candidate is not None:
            default_mode = candidate
            break

    return Settings(rules=rules, default_mode=default_mode)
