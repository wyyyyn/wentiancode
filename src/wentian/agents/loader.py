"""v0.13 · C110 · F92/F93 (task T139) — four-source sub-Agent role loader.

Public API:

- ``parse_agent``: parse ``AgentDef`` from Markdown text containing ``---``-fenced YAML
  frontmatter. Missing name with no usable name_hint, or parse failure ⇒ return None (skip).
- ``AgentRegistry``: name → AgentDef dictionary wrapper; ``get`` / ``list`` / ``add``.
- ``discover_agents``: scan plugin → builtin → user → project four layers (low→high),
  higher-layer definition overwrites same-name entirely, returns ``AgentRegistry``.

Layering rule: pure leaf module, stdlib only (pathlib / importlib.resources) +
``wentian.frontmatter`` + ``wentian.agents.spec`` — zero rich / prompt_toolkit /
backend SDK, and no reverse dependency on wentian.agent / wentian.repl / wentian.providers /
wentian.commands / wentian.skills.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

from wentian.agents.spec import AgentDef
from wentian.frontmatter import parse_frontmatter


# ---------------------------------------------------------------------------
# AgentRegistry
# ---------------------------------------------------------------------------


class AgentRegistry:
    """name → AgentDef dictionary; add overwrites lower-layer definitions with the same name."""

    def __init__(self) -> None:
        self._store: dict[str, AgentDef] = {}

    def add(self, agent_def: AgentDef) -> None:
        """Add (or overwrite same-name) AgentDef."""
        self._store[agent_def.name] = agent_def

    def get(self, name: str) -> AgentDef | None:
        """Look up by name; returns None if not found."""
        return self._store.get(name)

    def list(self) -> list[AgentDef]:
        """Return all AgentDef, sorted by name."""
        return sorted(self._store.values(), key=lambda a: a.name)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_agent(
    text: str,
    *,
    name_hint: str | None = None,
    source: str = "builtin",
) -> AgentDef | None:
    """Parse a single AgentDef from Markdown text; returns None if parsing fails.

    Field mapping (frontmatter key → AgentDef field):
    - ``name``            → name
    - ``description``     → description
    - ``tools``           → tools  (list → tuple | None if absent)
    - ``disallowed-tools``→ disallowed_tools  (list → tuple; default ())
    - ``model``           → model  (str; default "inherit")
    - ``max-turns``       → max_turns  (int; default None)
    - ``permission-mode`` → permission_mode  (str; default None)
    - body (body text)    → body
    """
    try:
        data, body = parse_frontmatter(text)
    except Exception:
        return None

    # When no fence, parse_frontmatter returns ({}, original text); data is empty dict and body==text.
    # Empty file / no frontmatter both return None.
    if not data and body == text:
        return None

    # name: frontmatter takes priority, otherwise name_hint
    raw_name = data.get("name")
    name_str = (
        raw_name.strip()
        if isinstance(raw_name, str) and raw_name.strip()
        else name_hint
    )
    if not name_str:
        return None

    # description
    description = data.get("description", "")
    if not isinstance(description, str):
        description = ""

    # tools: list → tuple | None if absent
    tools: tuple[str, ...] | None = None
    raw_tools = data.get("tools")
    if isinstance(raw_tools, (list, tuple)):
        tools = tuple(str(t) for t in raw_tools)

    # disallowed-tools: list → tuple; default ()
    disallowed_tools: tuple[str, ...] = ()
    raw_disallowed = data.get("disallowed-tools")
    if isinstance(raw_disallowed, (list, tuple)):
        disallowed_tools = tuple(str(t) for t in raw_disallowed)

    # model: str; default "inherit"
    model = "inherit"
    raw_model = data.get("model")
    if isinstance(raw_model, str) and raw_model.strip():
        model = raw_model.strip()

    # max-turns: int | None; default None
    max_turns: int | None = None
    raw_max_turns = data.get("max-turns")
    if raw_max_turns is not None:
        if isinstance(raw_max_turns, int):
            max_turns = raw_max_turns
        elif isinstance(raw_max_turns, str):
            try:
                max_turns = int(raw_max_turns.strip())
            except ValueError:
                max_turns = None

    # permission-mode: str | None; default None
    permission_mode: str | None = None
    raw_perm = data.get("permission-mode")
    if isinstance(raw_perm, str) and raw_perm.strip():
        permission_mode = raw_perm.strip()

    return AgentDef(
        name=name_str,
        description=description,
        body=body,
        tools=tools,
        disallowed_tools=disallowed_tools,
        model=model,
        max_turns=max_turns,
        permission_mode=permission_mode,
        source=source,
    )


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _read_text(path: Path) -> str | None:
    """Read file text; returns None on failure (does not raise)."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _load_layer(registry: AgentRegistry, layer_dir: Path | None, source: str) -> None:
    """Scan a single layer directory, adding discovered AgentDef entries to registry (same name overwrites lower layer).

    Only scans *.md files (single-file agents); name_hint taken from file stem.
    Files that fail to parse (parse_agent returns None / read failure) are silently skipped without interrupting other discoveries.
    """
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
            is_file = entry.is_file()
        except OSError:
            continue
        if not is_file:
            continue
        if entry.suffix != ".md":
            continue
        text = _read_text(entry)
        if text is None:
            continue
        try:
            agent = parse_agent(text, name_hint=entry.stem, source=source)
        except Exception:
            continue
        if agent is not None:
            registry.add(agent)


def _packaged_builtin_dir() -> Path | None:
    """Return the path to ``wentian/agents/builtin/`` directory inside the package; None if not found."""
    try:
        resource = importlib.resources.files("wentian.agents") / "builtin"
        path = Path(str(resource))
    except (ModuleNotFoundError, FileNotFoundError, TypeError, OSError):
        return None
    try:
        return path if path.is_dir() else None
    except OSError:
        return None


def discover_agents(
    project_dir: Path | None,
    user_dir: Path | None,
    *,
    builtin_dir: Path | None = None,
    plugin_dir: Path | None = None,
) -> AgentRegistry:
    """Scan four layers (low→high), higher-layer same-name entries overwrite entirely, returns AgentRegistry.

    Priority high→low (spec F93): project ▸ user ▸ builtin ▸ plugin (plugin lowest)

    - builtin layer: ``builtin_dir`` overrides (for testing), otherwise uses packaged
      ``wentian/agents/builtin/``; if directory does not exist, treated as empty (no crash).
    - plugin layer (lowest priority): skipped when ``plugin_dir`` is None (pass None by default when no plugin system).
    - Single file ``*.md`` ⇒ name_hint=file stem.
    - Files that fail to parse (parse_agent returns None / read failure) are silently skipped without interrupting other discoveries.
    """
    registry = AgentRegistry()

    # Load order: lower priority loaded first, higher priority loaded last (overwrites same name).
    # spec F93 priority project > user > builtin > plugin ⇒ load order plugin→builtin→user→project.
    builtin = builtin_dir if builtin_dir is not None else _packaged_builtin_dir()
    _load_layer(registry, plugin_dir, "plugin")
    _load_layer(registry, builtin, "builtin")
    _load_layer(registry, user_dir, "user")
    _load_layer(registry, project_dir, "project")

    return registry
