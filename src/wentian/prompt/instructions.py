"""v0.9 · C53 · F63 (task T98) — three-layer project instruction loading + @include inline expansion.

Leaf module: depends only on stdlib (``pathlib`` / ``os`` / ``sys`` / ``re``).
Never import ``prompt.system`` / provider / agent / registry / permissions.

Responsibilities
----------------
Reads handwritten Markdown instruction files from three locations at startup, concatenates
in priority order (**highest first**), and injects into the system prompt's
"project/custom instructions" section:

1. ``<cwd>/.wentian/WENTIAN.md``           (project-local override, highest priority, placed first)
2. ``<cwd>/WENTIAN.md``                     (project root, team-shared, second priority)
3. ``<user_home>/.config/wentian/WENTIAN.md``(user global, lowest priority, placed last)

Each layer is optional; missing layers are silently skipped. If all three are absent, returns ``""``.

A ``@include <relative-path>`` on its own line triggers inline expansion of the target
file's content (resolved relative to the directory containing the including file).
Three guardrails:

- **Depth limit**: stops expansion and warns when nesting depth exceeds
  :data:`DEFAULT_INCLUDE_DEPTH` (default 5).
- **visited cycle guard**: skips and warns if the same file appears more than once in an
  include chain; prevents infinite recursion.
- **Out-of-bounds interception**: resolves symlinks via ``os.path.realpath``, then checks
  the prefix against the project root (``cwd``); files outside the project root or with
  out-of-bounds absolute paths are refused with a warning and not read (same rule as
  v0.6 N11 sandbox; prevents symlink escape).

If the total concatenated size exceeds :data:`DEFAULT_MAX_BYTES`, the result is truncated
and a warning is emitted.

All error conditions (missing / out-of-bounds / over-limit / read failure) only warn to
stderr and never raise exceptions.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

__all__ = [
    "DEFAULT_INCLUDE_DEPTH",
    "DEFAULT_MAX_BYTES",
    "load_project_instructions",
    "expand_includes",
]

# Module-level default constants (this task does not read config.py; cfg parameter reserved for future injection).
DEFAULT_INCLUDE_DEPTH = 5
DEFAULT_MAX_BYTES = 64 * 1024  # 64 KiB, prevents bloating the context

# ``@include <relative-path>`` on its own line: leading/trailing whitespace allowed, path segment must not contain whitespace.
_INCLUDE_RE = re.compile(r"^[ \t]*@include[ \t]+(\S+)[ \t]*$")


def _warn(msg: str) -> None:
    """Unified stderr warning (follows the ``[wentian] Warning:`` style from session.py)."""
    print(f"[wentian] Warning: {msg}", file=sys.stderr)


def _within_root(resolved: Path, project_root: Path) -> bool:
    """Resolves symlinks first (already done by the caller), then performs prefix comparison.

    ``resolved`` must equal ``project_root`` or be a descendant of it. Same rule as v0.6 N11 sandbox.
    """
    if resolved == project_root:
        return True
    return project_root in resolved.parents


def expand_includes(
    text: str,
    base_dir: Path,
    project_root: Path,
    *,
    depth: int = 0,
    visited: frozenset[Path] = frozenset(),
    max_depth: int = DEFAULT_INCLUDE_DEPTH,
) -> str:
    """Inline-expands ``@include <rel>`` directives that occupy their own line.

    Parameters
    ----------
    text:
        Text to expand.
    base_dir:
        Base directory for resolving ``@include`` relative paths (i.e., the directory of
        the file containing it).
    project_root:
        Project root (out-of-bounds check prefix).
    depth:
        Current recursion depth.
    visited:
        Set of real paths already visited in the current include chain (cycle prevention).
    max_depth:
        Maximum nesting depth; stops expansion and warns if exceeded.

    Returns
    -------
    str
        Expanded text (line-ending newline structure preserved as-is).
    """
    out_lines: list[str] = []
    for line in text.splitlines():
        m = _INCLUDE_RE.match(line)
        if m is None:
            out_lines.append(line)
            continue

        rel = m.group(1)
        # Resolve symlinks (realpath) before prefix comparison to prevent symlink escape.
        target = Path(os.path.realpath(base_dir / rel))

        if not _within_root(target, project_root):
            _warn(f"@include '{rel}' resolves outside project root; refusing to read.")
            continue

        if target in visited:
            _warn(f"@include cycle detected at '{rel}'; skipping.")
            continue

        if depth >= max_depth:
            _warn(
                f"@include depth limit ({max_depth}) exceeded at '{rel}'; "
                "stopping expansion."
            )
            continue

        try:
            included = target.read_text(encoding="utf-8")
        except OSError as exc:
            _warn(f"@include '{rel}' could not be read: {exc}")
            continue

        out_lines.append(
            expand_includes(
                included,
                target.parent,
                project_root,
                depth=depth + 1,
                visited=visited | {target},
                max_depth=max_depth,
            )
        )

    return "\n".join(out_lines)


def _read_layer(path: Path, project_root: Path, max_depth: int) -> str | None:
    """Reads one instruction file layer and expands its ``@include`` directives; missing or unreadable => None (silently skipped)."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return expand_includes(raw, path.parent, project_root, max_depth=max_depth)


def load_project_instructions(
    cwd: Path,
    *,
    user_home: Path | None = None,
    cfg=None,  # noqa: ANN001 — future injection point (wave four connects MemoryConfig); this task uses defaults
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_depth: int = DEFAULT_INCLUDE_DEPTH,
) -> str:
    """Reads three ``WENTIAN.md`` layers, concatenates highest priority first, inlines ``@include`` directives, and truncates to the size limit.

    Parameters
    ----------
    cwd:
        Current working directory (also the project root; prefix for ``@include``
        out-of-bounds check).
    user_home:
        User home directory; defaults to :meth:`Path.home`.
    cfg:
        Future injection point (reserved); this task uses module-level default constants.
    max_bytes:
        Maximum total size after concatenation (bytes). Truncates to this limit and warns
        if exceeded.
    max_depth:
        ``@include`` maximum nesting depth.

    Returns
    -------
    str
        Assembled project instruction text; returns ``""`` if all three layers are absent.
    """
    cwd = Path(cwd)
    project_root = Path(os.path.realpath(cwd))
    if user_home is None:
        user_home = Path.home()

    layers = (
        cwd / ".wentian" / "WENTIAN.md",  # project-local override (highest priority)
        cwd / "WENTIAN.md",  # project root
        user_home / ".config" / "wentian" / "WENTIAN.md",  # user global (lowest priority)
    )

    parts: list[str] = []
    for path in layers:
        text = _read_layer(path, project_root, max_depth)
        if text is not None and text != "":
            parts.append(text)

    joined = "\n\n".join(parts)

    encoded = joined.encode("utf-8")
    if len(encoded) > max_bytes:
        _warn(f"project instructions exceed {max_bytes} bytes; truncating.")
        # After byte truncation, retreat to a valid UTF-8 boundary.
        joined = encoded[:max_bytes].decode("utf-8", errors="ignore")

    return joined
