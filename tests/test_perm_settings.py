"""v0.6 · C32 · F44（任务 T72）

RED-first tests for the three-layer settings loader. These assert the
*behaviour* of ``load_settings``:

* ``defaultMode`` resolves local > project > user, first valid wins, else
  ``Mode.DEFAULT`` (F44/AC58);
* allow/deny rules from three files merge into a ``LayeredRules`` whose
  precedence matches T71 semantics (F44/AC45);
* a missing file degrades to an empty layer; all three missing → empty rules +
  default mode (F44/N14);
* an illegal layer (bad YAML, or ``permissions`` not a mapping) degrades *that
  layer* to empty — the loader must not raise, must not fail construction, and
  the other layers load normally (N14/AC46).

Three config files are written under ``tmp_path``.
"""
from __future__ import annotations

from pathlib import Path

from wentian.permissions.decision import Mode, Verdict
from wentian.permissions.settings import Settings, load_settings


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _layout(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Return (project_root, user_path) layout + the project dir."""
    root = tmp_path / "proj"
    root.mkdir(parents=True, exist_ok=True)
    user_path = tmp_path / "user_settings.yaml"
    return root, user_path, root / ".wentian"


# --- defaultMode precedence -------------------------------------------------


def test_default_mode_local_over_project_over_user(tmp_path: Path):
    root, user_path, wt = _layout(tmp_path)
    _write(user_path, "defaultMode: bypassPermissions\n")
    _write(wt / "settings.yaml", "defaultMode: acceptEdits\n")
    _write(wt / "settings.local.yaml", "defaultMode: plan\n")

    s = load_settings(root, user_path=user_path)
    assert s.default_mode is Mode.PLAN


def test_default_mode_falls_through_to_project_then_user(tmp_path: Path):
    root, user_path, wt = _layout(tmp_path)
    _write(user_path, "defaultMode: bypassPermissions\n")
    _write(wt / "settings.yaml", "defaultMode: acceptEdits\n")
    # no local file
    s = load_settings(root, user_path=user_path)
    assert s.default_mode is Mode.ACCEPT_EDITS


def test_default_mode_none_configured_is_default(tmp_path: Path):
    root, user_path, _ = _layout(tmp_path)
    # no files at all
    s = load_settings(root, user_path=user_path)
    assert isinstance(s, Settings)
    assert s.default_mode is Mode.DEFAULT


def test_invalid_default_mode_value_is_skipped(tmp_path: Path):
    root, user_path, wt = _layout(tmp_path)
    _write(wt / "settings.local.yaml", "defaultMode: nonsense\n")
    _write(wt / "settings.yaml", "defaultMode: acceptEdits\n")

    s = load_settings(root, user_path=user_path)
    # local value is invalid -> skipped -> project value wins
    assert s.default_mode is Mode.ACCEPT_EDITS


# --- rules merge & precedence (end-to-end, reusing T71 semantics) -----------


def test_rules_merge_layered_precedence(tmp_path: Path):
    root, user_path, wt = _layout(tmp_path)
    _write(
        user_path,
        "permissions:\n  allow: ['Read']\n",
    )
    _write(
        wt / "settings.yaml",
        "permissions:\n  deny: ['Bash(git push)']\n",
    )
    _write(
        wt / "settings.local.yaml",
        "permissions:\n  allow: ['Bash(git push)']\n",
    )

    s = load_settings(root, user_path=user_path)

    # local allow overrides project deny (nearest hit wins)
    assert s.rules.match(friendly="Bash", target="git push", is_path=False) is Verdict.ALLOW
    # user allow still reachable when nearer layers don't match
    assert s.rules.match(friendly="Read", target="a.py", is_path=True) is Verdict.ALLOW


def test_same_layer_deny_beats_allow_via_loaded_config(tmp_path: Path):
    root, user_path, wt = _layout(tmp_path)
    _write(
        wt / "settings.yaml",
        "permissions:\n  allow: ['Bash(git *)']\n  deny: ['Bash(git push)']\n",
    )

    s = load_settings(root, user_path=user_path)
    assert s.rules.match(friendly="Bash", target="git push", is_path=False) is Verdict.DENY
    assert s.rules.match(friendly="Bash", target="git status", is_path=False) is Verdict.ALLOW


# --- missing files ----------------------------------------------------------


def test_all_files_missing_is_empty_rules_and_default_mode(tmp_path: Path):
    root, user_path, _ = _layout(tmp_path)
    s = load_settings(root, user_path=user_path)

    assert s.default_mode is Mode.DEFAULT
    assert s.rules.match(friendly="Bash", target="git status", is_path=False) is None


def test_missing_one_layer_others_load(tmp_path: Path):
    root, user_path, wt = _layout(tmp_path)
    # only project file present
    _write(wt / "settings.yaml", "permissions:\n  allow: ['Bash(git *)']\n")

    s = load_settings(root, user_path=user_path)
    assert s.rules.match(friendly="Bash", target="git log", is_path=False) is Verdict.ALLOW


# --- degradation: bad YAML / bad structure ----------------------------------


def test_malformed_yaml_layer_degrades_to_empty_not_raises(tmp_path: Path):
    root, user_path, wt = _layout(tmp_path)
    # invalid YAML in local layer
    _write(wt / "settings.local.yaml", "permissions: [ : : unbalanced\n")
    # valid project layer
    _write(wt / "settings.yaml", "permissions:\n  allow: ['Bash(git *)']\n")

    s = load_settings(root, user_path=user_path)  # must not raise
    # bad local layer degraded to empty; project still loads
    assert s.rules.match(friendly="Bash", target="git log", is_path=False) is Verdict.ALLOW


def test_bad_structure_permissions_not_dict_degrades(tmp_path: Path):
    root, user_path, wt = _layout(tmp_path)
    # permissions is a list, not a mapping -> structural error -> degrade
    _write(wt / "settings.local.yaml", "permissions:\n  - Bash(rm *)\n")
    _write(wt / "settings.yaml", "permissions:\n  allow: ['Read']\n")

    s = load_settings(root, user_path=user_path)  # must not raise
    # bad local degraded; project loads
    assert s.rules.match(friendly="Read", target="a.py", is_path=True) is Verdict.ALLOW
    # nothing from the malformed local layer leaked through
    assert s.rules.match(friendly="Bash", target="rm -rf x", is_path=False) is None
