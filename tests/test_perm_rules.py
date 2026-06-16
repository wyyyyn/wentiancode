"""v0.6 · C31 · F43（任务 T71）

RED-first tests for the rule engine. These assert the *behaviour* of
``Rule`` / ``RuleSet`` / ``LayeredRules`` and the friendly-name routing:

* exact and glob matching (command strings vs. file paths) — F43/AC43;
* friendly names Bash/Read/Write/Edit/Glob/Grep routing to the six builtin
  tool names — F44/AC44;
* same-layer ``deny`` taking precedence over ``allow`` — F44/AC45;
* three-layer ``local > project > user`` nearest-hit-wins short-circuit —
  F44/AC45.

Pure-package tests: only ``wentian.permissions.rules`` and
``wentian.permissions.decision`` are imported (no SDK / rich / prompt_toolkit).
"""
from __future__ import annotations

from wentian.permissions.decision import Verdict
from wentian.permissions.rules import (
    Rule,
    RuleSet,
    LayeredRules,
    FRIENDLY_TO_TOOL,
)


# --- exact + glob: command strings ------------------------------------------


def test_exact_command_match_allows_only_that_command():
    rs = RuleSet(allow=[Rule(friendly="Bash", pattern="git status", effect=Verdict.ALLOW)])

    assert rs.match(friendly="Bash", target="git status", is_path=False) is Verdict.ALLOW
    # exact pattern must NOT match a different command
    assert rs.match(friendly="Bash", target="git push", is_path=False) is None


def test_glob_command_match_allows_all_subcommands():
    rs = RuleSet(allow=[Rule(friendly="Bash", pattern="git *", effect=Verdict.ALLOW)])

    assert rs.match(friendly="Bash", target="git status", is_path=False) is Verdict.ALLOW
    assert rs.match(friendly="Bash", target="git push --force", is_path=False) is Verdict.ALLOW
    assert rs.match(friendly="Bash", target="ls -la", is_path=False) is None


# --- exact + glob: file paths -----------------------------------------------


def test_path_glob_double_star_crosses_directories():
    rs = RuleSet(allow=[Rule(friendly="Write", pattern="src/**", effect=Verdict.ALLOW)])

    # ** crosses directory boundaries for file paths
    assert rs.match(friendly="Write", target="src/a/b.py", is_path=True) is Verdict.ALLOW
    assert rs.match(friendly="Write", target="src/top.py", is_path=True) is Verdict.ALLOW
    assert rs.match(friendly="Write", target="docs/x", is_path=True) is None


def test_command_double_star_equals_single_star_no_cross_dir():
    # for command strings, ** must behave exactly like * (no cross-dir semantics)
    rs_dd = RuleSet(allow=[Rule(friendly="Bash", pattern="git **", effect=Verdict.ALLOW)])
    rs_s = RuleSet(allow=[Rule(friendly="Bash", pattern="git *", effect=Verdict.ALLOW)])

    # both should match a multi-segment, slash-containing command identically
    cmd = "git log --oneline path/to/file"
    assert rs_dd.match(friendly="Bash", target=cmd, is_path=False) is Verdict.ALLOW
    assert rs_s.match(friendly="Bash", target=cmd, is_path=False) is Verdict.ALLOW


# --- friendly-name routing --------------------------------------------------


def test_friendly_names_map_to_builtin_tools():
    assert FRIENDLY_TO_TOOL == {
        "Bash": "run_command",
        "Read": "read_file",
        "Write": "write_file",
        "Edit": "edit_file",
        "Glob": "find_files",
        "Grep": "search_text",
    }


# --- pattern None matches all calls of that tool ----------------------------


def test_pattern_none_matches_every_call_of_tool():
    rs = RuleSet(allow=[Rule(friendly="Read", pattern=None, effect=Verdict.ALLOW)])

    assert rs.match(friendly="Read", target="anything/at/all.py", is_path=True) is Verdict.ALLOW
    assert rs.match(friendly="Read", target="", is_path=True) is Verdict.ALLOW
    # but does not leak to a different tool
    assert rs.match(friendly="Write", target="x", is_path=True) is None


# --- same-layer deny > allow ------------------------------------------------


def test_same_layer_deny_beats_allow():
    rs = RuleSet(
        allow=[Rule(friendly="Bash", pattern="git *", effect=Verdict.ALLOW)],
        deny=[Rule(friendly="Bash", pattern="git push", effect=Verdict.DENY)],
    )

    # git push is hit by both allow(git *) and deny(git push) -> DENY wins
    assert rs.match(friendly="Bash", target="git push", is_path=False) is Verdict.DENY
    # git status only hit by allow -> ALLOW
    assert rs.match(friendly="Bash", target="git status", is_path=False) is Verdict.ALLOW


def test_lone_deny_rule_denies_on_hit():
    rs = RuleSet(deny=[Rule(friendly="Bash", pattern="git push", effect=Verdict.DENY)])

    assert rs.match(friendly="Bash", target="git push", is_path=False) is Verdict.DENY
    assert rs.match(friendly="Bash", target="git status", is_path=False) is None


# --- layered: local > project > user, nearest hit wins ----------------------


def test_local_allow_overrides_project_deny():
    user = RuleSet()
    project = RuleSet(deny=[Rule(friendly="Bash", pattern="git push", effect=Verdict.DENY)])
    local = RuleSet(allow=[Rule(friendly="Bash", pattern="git push", effect=Verdict.ALLOW)])

    layered = LayeredRules(user=user, project=project, local=local)

    # local hits first (nearest) -> ALLOW, project deny never consulted
    assert layered.match(friendly="Bash", target="git push", is_path=False) is Verdict.ALLOW


def test_layered_falls_through_to_project_then_user():
    user = RuleSet(allow=[Rule(friendly="Read", pattern=None, effect=Verdict.ALLOW)])
    project = RuleSet(deny=[Rule(friendly="Bash", pattern="rm *", effect=Verdict.DENY)])
    local = RuleSet()

    layered = LayeredRules(user=user, project=project, local=local)

    # not in local -> project hits the deny
    assert layered.match(friendly="Bash", target="rm -rf x", is_path=False) is Verdict.DENY
    # not in local/project -> user allows
    assert layered.match(friendly="Read", target="a.py", is_path=True) is Verdict.ALLOW
    # nobody matches
    assert layered.match(friendly="Edit", target="z.py", is_path=True) is None
