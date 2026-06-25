"""v0.6 · C32 · F44 (task T72)

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

Layering rule: pure leaf module — stdlib (``os`` / ``pathlib`` / ``dataclasses``)
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
    """Runtime settings after loading and merging three-layer configuration."""

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
    """Parse a ``Friendly(pattern)`` / ``Friendly`` string into a :class:`Rule`.

    Unrecognized (non-string, unknown friendly name, empty) → returns ``None`` (skip entry, no permissions granted).
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
    """Parse an allow/deny list into a Rule list (non-list → empty)."""
    if not isinstance(raw, list):
        return []
    rules: list[Rule] = []
    for item in raw:
        rule = _parse_rule(item, effect)
        if rule is not None:
            rules.append(rule)
    return rules


def _load_layer(path: Path) -> tuple[RuleSet, Mode | None]:
    """Load a single-layer config file → (RuleSet, defaultMode|None).

    Missing / invalid YAML / malformed structure → degrade to empty set + None (never raises).
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
    """Load three-layer configuration and merge into :class:`Settings`.

    Precedence ``local > project > user``; within the same layer deny takes priority over allow (see
    :class:`~wentian.permissions.rules.RuleSet`). Any missing or malformed single layer degrades
    to an empty set — never raises, never causes construction failure (N14). ``defaultMode`` takes the first
    valid value in local>project>user order; if none, uses :attr:`Mode.DEFAULT`.
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
