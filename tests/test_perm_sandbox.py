"""v0.6 · C30 · F42（任务 T70）

RED-first tests for the path sandbox. These assert the *behaviour* of
``check_path``: in-root paths (existing or not-yet-created, including multi-level
missing intermediate dirs) are allowed (``None``); paths escaping the project
root — directly, via ``..``, via an absolute path, or via a symlink that points
outside — are denied (``Decision(DENY, SANDBOX)``).

All filesystem fixtures are built for real under ``tmp_path`` (directories and
symlinks), so the symlink-resolution-before-prefix-check invariant (N11) is
exercised against the actual OS resolver, not a mock.
"""
from __future__ import annotations

from pathlib import Path

from wentian.permissions.decision import Decision, Source, Verdict
from wentian.permissions.sandbox import check_path


# --- in-root: allowed (None) -----------------------------------------------


def test_existing_in_root_file_allowed(tmp_path: Path):
    root = tmp_path / "proj"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "a.txt").write_text("hi", encoding="utf-8")

    assert check_path("sub/a.txt", root) is None


def test_absolute_in_root_path_allowed(tmp_path: Path):
    root = tmp_path / "proj"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "a.txt").write_text("hi", encoding="utf-8")

    assert check_path(str(root / "sub" / "a.txt"), root) is None


def test_new_file_with_missing_intermediate_dirs_allowed(tmp_path: Path):
    """RED 4: not-yet-existing target with multi-level missing parents → None.

    Resolution must climb to the nearest existing ancestor and not misjudge
    just because the leaf (and several parents) do not exist yet.
    """
    root = tmp_path / "proj"
    root.mkdir()

    assert check_path("a/b/c/new.txt", root) is None


# --- out-of-root: denied (Decision(DENY, SANDBOX)) -------------------------


def _assert_denied(decision):
    assert isinstance(decision, Decision)
    assert decision.verdict is Verdict.DENY
    assert decision.source is Source.SANDBOX
    assert decision.reason  # deny must carry a model-facing reason


def test_absolute_outside_root_denied(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()

    _assert_denied(check_path("/etc/passwd", root))


def test_dotdot_escape_denied(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    (tmp_path / "outside").mkdir()

    _assert_denied(check_path("../outside", root))


def test_dotdot_escape_to_nonexistent_denied(tmp_path: Path):
    """`..` escape must be caught even when the target does not exist."""
    root = tmp_path / "proj"
    root.mkdir()

    _assert_denied(check_path("../outside/new.txt", root))


# --- symlink: resolve-then-compare (N11) -----------------------------------


def test_symlink_pointing_outside_denied(tmp_path: Path):
    """RED 3: an in-root symlink whose target is outside → Deny.

    The link itself lives inside the root; only after resolving it does the
    escape become visible. This pins down the resolve-before-prefix ordering.
    """
    root = tmp_path / "proj"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("s", encoding="utf-8")

    link = root / "link"
    link.symlink_to(outside)

    _assert_denied(check_path("link/secret.txt", root))


def test_symlink_dir_pointing_inside_allowed(tmp_path: Path):
    """A symlink resolving back inside the root stays allowed."""
    root = tmp_path / "proj"
    (root / "real").mkdir(parents=True)
    (root / "real" / "a.txt").write_text("a", encoding="utf-8")

    link = root / "link"
    link.symlink_to(root / "real")

    assert check_path("link/a.txt", root) is None
