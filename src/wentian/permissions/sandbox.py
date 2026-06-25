"""v0.6 · C30 · F42 (task T70)

Path sandbox — the second layer of the permission pipeline, confining every
path-bearing file tool (read / write / edit / find / search) to the project
root.

:func:`check_path` normalises a tool's target path to an absolute path,
*resolves symlinks first* and only *then* does the prefix comparison against the
project root. A path that escapes the root — directly, via ``..``, via an
absolute path, or via a symlink whose target lies outside — yields a
``Decision(DENY, SANDBOX, reason)``; an in-root path yields ``None`` so the next
layer of the pipeline can run.

**N11 (no symlink escape):** resolution happens *before* the prefix test, so a
link living inside the root but pointing outside is still caught. For a target
that does not exist yet (a new file, or a file under several not-yet-created
intermediate directories) we resolve the *nearest existing ancestor* and
re-attach the not-yet-existing tail, so a legitimate new path is never misjudged
merely for being absent.

Layering rule: pure leaf module — stdlib ``pathlib`` only, plus the sibling decision
types. No backend SDK, no terminal-UI libraries, no cross-layer wentian
imports.
"""

from __future__ import annotations

from pathlib import Path

from wentian.permissions.decision import Decision, Source, Verdict

__all__ = ["check_path"]


def _resolve_with_missing_tail(target: Path) -> Path:
    """Resolve *target*, tolerating a not-yet-existing leaf / tail.

    If *target* exists it is resolved directly (following symlinks). Otherwise
    we climb to the nearest existing ancestor, resolve *that* (so any symlink in
    the existing prefix is followed), and re-attach the not-yet-existing tail
    segments. This keeps a legitimate new path inside the root while still
    catching a ``..`` escape baked into the tail.
    """
    if target.exists():
        return target.resolve()

    existing = target
    tail: list[str] = []
    while not existing.exists():
        tail.append(existing.name)
        parent = existing.parent
        if parent == existing:  # reached the filesystem root
            break
        existing = parent

    resolved = existing.resolve()
    for name in reversed(tail):
        resolved = resolved / name
    return resolved


def check_path(path: str, project_root: Path) -> Decision | None:
    """Confine *path* to *project_root*; ``None`` if inside, else a Deny.

    Steps (order is load-bearing — see N11):

    1. Normalise to an absolute path (relative paths are based on
       *project_root*).
    2. Resolve symlinks: existing targets via :meth:`Path.resolve`; not-yet
       existing targets via the nearest existing ancestor (see
       :func:`_resolve_with_missing_tail`).
    3. Prefix test: is the resolved path under ``project_root.resolve()``
       (:meth:`Path.is_relative_to`)? If not → ``Decision(DENY, SANDBOX,
       reason)``; if so → ``None``.
    """
    target = Path(path)
    if not target.is_absolute():
        target = project_root / target

    resolved = _resolve_with_missing_tail(target)
    root = project_root.resolve()

    if resolved.is_relative_to(root):
        return None

    return Decision(
        verdict=Verdict.DENY,
        source=Source.SANDBOX,
        reason=(
            f"Path {path!r} resolves to {resolved}, which is outside the project root {root}; "
            "blocked by sandbox (file tools may only read/write within the project directory)."
        ),
    )
