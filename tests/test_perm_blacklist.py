"""v0.6 · C29 · F41/N10（任务 T69）

RED-first tests for the dangerous-command blacklist and the permission
decision types. These assert the *behaviour* of ``check_command`` and the
*structure* of the ``permissions`` package — including the hard rule that the
blacklist cannot be disabled (N10).
"""
from __future__ import annotations

import re

import pytest

from wentian.permissions import (
    Category,
    Decision,
    Mode,
    Source,
    Verdict,
    check_command,
)
from wentian.permissions import blacklist as blacklist_mod
from wentian.permissions import MODE_CYCLE


# --- decision types ---------------------------------------------------------


def test_enums_have_spec_values():
    assert Mode.DEFAULT.value == "default"
    assert Mode.ACCEPT_EDITS.value == "acceptEdits"
    assert Mode.PLAN.value == "plan"
    assert Mode.BYPASS.value == "bypassPermissions"

    assert Category.READ_ONLY.value == "read_only"
    assert Category.FILE_WRITE.value == "file_write"
    assert Category.COMMAND_EXEC.value == "command_exec"

    assert Verdict.ALLOW.value == "allow"
    assert Verdict.DENY.value == "deny"
    assert Verdict.ASK.value == "ask"

    assert Source.BLACKLIST.value == "blacklist"
    assert Source.SANDBOX.value == "sandbox"
    assert Source.RULE.value == "rule"
    assert Source.MODE.value == "mode"
    assert Source.HUMAN.value == "human"


def test_mode_cycle_order():
    assert MODE_CYCLE == (Mode.DEFAULT, Mode.ACCEPT_EDITS, Mode.PLAN, Mode.BYPASS)


def test_decision_is_frozen_dataclass():
    d = Decision(verdict=Verdict.DENY, source=Source.BLACKLIST, reason="x")
    assert d.verdict is Verdict.DENY
    assert d.source is Source.BLACKLIST
    assert d.reason == "x"
    with pytest.raises(Exception):
        d.verdict = Verdict.ALLOW  # type: ignore[misc]


def test_decision_reason_defaults_empty():
    d = Decision(verdict=Verdict.ALLOW, source=Source.MODE)
    assert d.reason == ""


# --- blacklist: dangerous commands are denied -------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "rm -fr /",
        "rm -rf  /",
        "sudo rm -rf /",
        "rm -rf ~",
        "rm -rf $HOME",
        "rm --recursive --force /",
        "dd of=/dev/sda",
        "dd if=/dev/zero of=/dev/sda bs=1M",
        "mkfs.ext4 /dev/sdb",
        "mkfs -t ext4 /dev/sdb",
        ":(){ :|:& };:",
        "echo x > /dev/sda",
        "cat foo > /dev/disk0",
    ],
)
def test_dangerous_commands_denied(command):
    decision = check_command(command)
    assert decision is not None, f"expected DENY for {command!r}"
    assert decision.verdict is Verdict.DENY
    assert decision.source is Source.BLACKLIST
    assert decision.reason  # non-empty, model-facing reason


# --- blacklist: ordinary commands pass through ------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "git status",
        "echo hi",
        "rm -rf build/",
        "rm -rf ./node_modules",
        "python -m pytest",
        "cat README.md",
        "dd if=in.img of=out.img",
        "mkdir -p src/wentian",
    ],
)
def test_ordinary_commands_pass(command):
    assert check_command(command) is None


# --- N10: blacklist is not bypassable / configurable ------------------------


def test_check_command_signature_has_no_toggle():
    """N10: ``check_command`` takes only the command string — no enable/disable
    or config parameter that could relax the blacklist."""
    import inspect

    sig = inspect.signature(check_command)
    params = list(sig.parameters.values())
    assert len(params) == 1
    (only,) = params
    assert only.name == "command"
    assert only.kind in (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    )


def test_module_exposes_no_enable_disable_api():
    """N10: the blacklist module must not offer any toggle surface."""
    banned = {"enable", "disable", "configure", "set_enabled", "clear", "add_pattern"}
    public = {name for name in dir(blacklist_mod) if not name.startswith("_")}
    assert not (banned & public), f"unexpected toggle surface: {banned & public}"


def test_dangerous_patterns_are_compiled_regexes():
    """Structural: the blacklist is a tuple of compiled regexes (no mutable
    public registry to tamper with)."""
    patterns = blacklist_mod._DANGEROUS
    assert isinstance(patterns, tuple)
    assert patterns
    assert all(isinstance(p, re.Pattern) for p in patterns)


def test_blacklist_module_is_stdlib_only():
    """分层铁律: permissions is a pure package — no SDK / rich / prompt_toolkit /
    cross-layer wentian imports."""
    import wentian.permissions.blacklist as m
    import wentian.permissions.decision as d

    forbidden = (
        "anthropic",
        "openai",
        "rich",
        "prompt_toolkit",
        "wentian.providers",
        "wentian.agent",
        "wentian.tools",
    )
    for mod in (m, d):
        src = inspect_source(mod)
        for token in forbidden:
            assert token not in src, f"{mod.__name__} imports forbidden {token!r}"


def inspect_source(module) -> str:
    import inspect

    return inspect.getsource(module)
