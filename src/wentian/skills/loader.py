"""v0.11 · C101 · F73 (task T127) — Skill loader: discover / parse / render (leaf module).

Three pure functions:

- ``parse_skill``: parses a ``Skill`` from Markdown text containing ``---``-fenced YAML
  frontmatter. The frontmatter is parsed manually (no third-party YAML library); missing
  name with no name_hint, or malformed fence ⇒ returns None (skip).
- ``render_body``: replaces ``$ARGUMENTS`` / ``$1`` … placeholders in the body with actual
  arguments.
- ``discover_skills``: scans three layers builtin → user → project (low→high), higher
  layers override same-name entries, returns a ``SkillRegistry``.

Layering rule: pure leaf module, stdlib only (pathlib / re / importlib.resources) + same-package
``wentian.skills.base`` / ``wentian.skills.registry`` imports — zero rich /
prompt_toolkit / backend SDKs, and no reverse dependency on wentian.agent / wentian.repl /
wentian.providers / wentian.commands.
"""

from __future__ import annotations

import importlib.resources
import re
from pathlib import Path

from wentian.frontmatter import parse_frontmatter
from wentian.skills.base import Skill, SkillMode
from wentian.skills.registry import SkillRegistry

# Placeholder regex: $ARGUMENTS as a whole or $<digits> (multi-digit captured together,
# to prevent $1 from accidentally matching $10).
_PLACEHOLDER_RE = re.compile(r"\$ARGUMENTS|\$(\d+)")


def _has_frontmatter_fence(text: str) -> bool:
    """Quickly detect whether the text starts with a valid ``---`` fence (first line is
    ``---`` and a closing fence exists later).

    This is the pre-guard for parse_skill: used to distinguish "no frontmatter fence at
    all" (→ None) from "fence present but data may be empty/missing fields", preserving
    the original behaviour of parse_skill.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return False
    return any(lines[i].strip() == "---" for i in range(1, len(lines)))


def parse_skill(
    text: str,
    *,
    name_hint: str | None = None,
    source: str = "builtin",
) -> Skill | None:
    """Parse a single Skill text → Skill; returns None if unparsable (missing name / malformed)."""
    # Preserve original behaviour: no frontmatter fence → None (distinct from "fence
    # present but missing fields").
    if not _has_frontmatter_fence(text):
        return None

    try:
        data, body = parse_frontmatter(text)
    except Exception:
        return None

    # name: frontmatter takes priority, otherwise name_hint
    name = data.get("name")
    name_str = name.strip() if isinstance(name, str) and name.strip() else name_hint
    if not name_str:
        return None

    description = data.get("description", "")
    if not isinstance(description, str):
        description = ""

    # mode
    mode = SkillMode.SHARED
    raw_mode = data.get("mode")
    if isinstance(raw_mode, str):
        try:
            mode = SkillMode(raw_mode.strip().lower())
        except ValueError:
            mode = SkillMode.SHARED

    # allowed_tools: tuple or None
    # parse_frontmatter returns list; compatible with legacy tuple (if any).
    allowed_tools: tuple[str, ...] | None = None
    raw_tools = data.get("allowed_tools")
    if isinstance(raw_tools, (list, tuple)):
        allowed_tools = tuple(str(t) for t in raw_tools)

    # history: int, default 0
    history = 0
    raw_history = data.get("history")
    if isinstance(raw_history, str):
        try:
            history = int(raw_history.strip())
        except ValueError:
            history = 0

    # model: str|None
    model: str | None = None
    raw_model = data.get("model")
    if isinstance(raw_model, str) and raw_model.strip():
        model = raw_model.strip()

    return Skill(
        name=name_str,
        description=description,
        body=body,
        mode=mode,
        allowed_tools=allowed_tools,
        history=history,
        model=model,
        source=source,
    )


# ---------------------------------------------------------------------------
# Body rendering
# ---------------------------------------------------------------------------


def render_body(body: str, args: str) -> str:
    """Replace body placeholders: ``$ARGUMENTS`` → the full args string; ``$N`` → the
    Nth positional argument.

    Positional arguments are split on whitespace; placeholders with no corresponding
    argument are replaced with an empty string. Single regex + callback ensures
    ``$10`` is not corrupted by ``$1``.
    """
    positionals = args.split()

    def _sub(match: re.Match[str]) -> str:
        digits = match.group(1)
        if digits is None:
            # $ARGUMENTS
            return args
        idx = int(digits)
        if 1 <= idx <= len(positionals):
            return positionals[idx - 1]
        return ""

    return _PLACEHOLDER_RE.sub(_sub, body)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _read_text(path: Path) -> str | None:
    """Read file text; return None on failure (no exception raised)."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _load_layer(registry: SkillRegistry, layer_dir: Path | None, source: str) -> None:
    """Scan a single layer directory and add discovered Skills to the registry (same-name
    entries override lower layers)."""
    if layer_dir is None:
        return
    try:
        if not layer_dir.is_dir():
            return
        entries = sorted(layer_dir.iterdir())
    except OSError:
        return

    for entry in entries:
        try:
            is_dir = entry.is_dir()
        except OSError:
            continue
        if is_dir:
            # Directory skill: only loaded when it contains SKILL.md; tools/ subdirectory
            # is recognised but not loaded.
            skill_md = entry / "SKILL.md"
            try:
                has_skill = skill_md.is_file()
            except OSError:
                has_skill = False
            if not has_skill:
                continue
            text = _read_text(skill_md)
            if text is None:
                continue
            skill = parse_skill(text, name_hint=entry.name, source=source)
            if skill is not None:
                registry.add(skill)
        else:
            # Single-file skill: *.md
            if entry.suffix != ".md":
                continue
            text = _read_text(entry)
            if text is None:
                continue
            skill = parse_skill(text, name_hint=entry.stem, source=source)
            if skill is not None:
                registry.add(skill)


def _packaged_builtin_dir() -> Path | None:
    """Return the path to the packaged ``wentian/skills/builtin/`` directory; None if not found."""
    try:
        resource = importlib.resources.files("wentian.skills") / "builtin"
        path = Path(str(resource))
    except (ModuleNotFoundError, FileNotFoundError, TypeError, OSError):
        return None
    try:
        return path if path.is_dir() else None
    except OSError:
        return None


def discover_skills(
    project_dir: Path | None,
    user_dir: Path | None,
    *,
    builtin_dir: Path | None = None,
) -> SkillRegistry:
    """Scan three layers builtin → user → project (low→high), higher layers override
    same-name entries, and return a SkillRegistry.

    - builtin layer: ``builtin_dir`` overrides (for tests), otherwise uses the packaged
      ``wentian/skills/builtin/``; if the directory does not exist it is treated as empty
      (no crash).
    - Single-file ``*.md`` ⇒ name_hint=filename stem; subdirectory containing ``SKILL.md``
      ⇒ directory skill (name_hint=directory name, ``tools/`` subdirectory is recognised
      but not loaded/executed).
    - Files that fail to parse (parse_skill returns None / read failure) are silently
      skipped without interrupting discovery of the rest.
    """
    registry = SkillRegistry()

    builtin = builtin_dir if builtin_dir is not None else _packaged_builtin_dir()
    _load_layer(registry, builtin, "builtin")
    _load_layer(registry, user_dir, "user")
    _load_layer(registry, project_dir, "project")

    return registry
