"""Tests for hooks/config.py: parse_hooks + centralized validation.

T120 RED test suite — covers:
  - Legal multi-rule parsing (all fields)
  - Missing 'if' → condition=None
  - Missing block (None / non-list) → []
  - Validation errors: bad event, missing/bad action.type, missing required
    action fields, PreToolUse+background, timeout<=0, bad match
  - Error messages contain rule index / field name
"""

import pytest

from wentian.hooks.spec import HookEvent
from wentian.hooks.config import HookConfigError, parse_hooks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _shell_rule(event_name: str = "SessionStart", **kwargs):
    """Return a minimal valid shell rule dict."""
    rule = {"event": event_name, "action": {"type": "shell", "command": "echo hi"}}
    rule.update(kwargs)
    return rule


def _prompt_rule(event_name: str = "SessionStart", **kwargs):
    rule = {"event": event_name, "action": {"type": "prompt", "text": "remember this"}}
    rule.update(kwargs)
    return rule


def _http_rule(event_name: str = "PostToolUse", **kwargs):
    rule = {
        "event": event_name,
        "action": {"type": "http", "url": "https://example.com/audit"},
    }
    rule.update(kwargs)
    return rule


def _subagent_rule(event_name: str = "SessionEnd", **kwargs):
    rule = {
        "event": event_name,
        "action": {"type": "subagent", "prompt": "do something"},
    }
    rule.update(kwargs)
    return rule


# ---------------------------------------------------------------------------
# T120-1  Legal multi-rule parse — all fields present
# ---------------------------------------------------------------------------


class TestParseHooksLegal:
    def test_empty_list_returns_empty(self):
        assert parse_hooks([]) == []

    def test_single_shell_rule(self):
        raw = [_shell_rule()]
        rules = parse_hooks(raw)
        assert len(rules) == 1
        rule = rules[0]
        assert rule.event == HookEvent.SESSION_START
        from wentian.hooks.spec import ShellAction

        assert isinstance(rule.action, ShellAction)
        assert rule.action.command == "echo hi"

    def test_prompt_rule(self):
        raw = [_prompt_rule()]
        rules = parse_hooks(raw)
        from wentian.hooks.spec import PromptAction

        assert isinstance(rules[0].action, PromptAction)
        assert rules[0].action.text == "remember this"

    def test_http_rule_defaults(self):
        raw = [_http_rule()]
        rules = parse_hooks(raw)
        from wentian.hooks.spec import HttpAction

        a = rules[0].action
        assert isinstance(a, HttpAction)
        assert a.url == "https://example.com/audit"
        assert a.method == "POST"  # default
        assert a.timeout is None

    def test_http_rule_custom_method(self):
        raw = [
            {
                "event": "PostToolUse",
                "action": {
                    "type": "http",
                    "url": "https://x.com",
                    "method": "PUT",
                    "timeout": 10,
                },
            }
        ]
        rules = parse_hooks(raw)
        a = rules[0].action
        assert a.method == "PUT"
        assert a.timeout == 10

    def test_subagent_rule(self):
        raw = [_subagent_rule()]
        rules = parse_hooks(raw)
        from wentian.hooks.spec import SubAgentAction

        assert isinstance(rules[0].action, SubAgentAction)

    def test_once_flag(self):
        raw = [_shell_rule(once=True)]
        rules = parse_hooks(raw)
        assert rules[0].once is True

    def test_background_flag_on_non_intercept(self):
        raw = [_shell_rule(background=True)]
        rules = parse_hooks(raw)
        assert rules[0].background is True

    def test_shell_with_timeout(self):
        raw = [
            {
                "event": "SessionStart",
                "action": {"type": "shell", "command": "ls", "timeout": 5},
            }
        ]
        rules = parse_hooks(raw)
        assert rules[0].action.timeout == 5

    def test_full_condition_all(self):
        raw = [
            {
                "event": "PreToolUse",
                "action": {"type": "shell", "command": "guard"},
                "if": {
                    "match": "all",
                    "clauses": [
                        {"field": "tool_name", "pattern": "run_command"},
                        {"field": "input", "pattern": "*rm*"},
                    ],
                },
            }
        ]
        rules = parse_hooks(raw)
        from wentian.hooks.spec import Clause, Match

        cond = rules[0].condition
        assert cond is not None
        assert cond.match == Match.ALL
        assert len(cond.clauses) == 2
        assert cond.clauses[0] == Clause(field="tool_name", pattern="run_command")
        assert cond.clauses[1] == Clause(field="input", pattern="*rm*")

    def test_condition_any_match(self):
        raw = [
            {
                "event": "PreToolUse",
                "action": {"type": "shell", "command": "guard"},
                "if": {
                    "match": "any",
                    "clauses": [{"field": "tool_name", "pattern": "shell"}],
                },
            }
        ]
        rules = parse_hooks(raw)
        from wentian.hooks.spec import Match

        assert rules[0].condition.match == Match.ANY

    def test_multi_rule_order_preserved(self):
        raw = [
            _shell_rule("SessionStart"),
            _prompt_rule("UserPromptSubmit"),
            _http_rule("PostToolUse"),
        ]
        rules = parse_hooks(raw)
        assert len(rules) == 3
        assert rules[0].event == HookEvent.SESSION_START
        assert rules[1].event == HookEvent.USER_PROMPT_SUBMIT
        assert rules[2].event == HookEvent.POST_TOOL_USE

    def test_all_ten_events_accepted(self):
        event_names = [e.value for e in HookEvent]
        raw = [
            {"event": name, "action": {"type": "shell", "command": "x"}}
            for name in event_names
        ]
        rules = parse_hooks(raw)
        assert len(rules) == 10
        parsed_events = {r.event for r in rules}
        assert parsed_events == set(HookEvent)


# ---------------------------------------------------------------------------
# T120-2  Missing 'if' → condition=None
# ---------------------------------------------------------------------------


class TestMissingIfCondition:
    def test_no_if_key_gives_none_condition(self):
        raw = [_shell_rule()]
        rules = parse_hooks(raw)
        assert rules[0].condition is None

    def test_defaults_once_false(self):
        raw = [_shell_rule()]
        rules = parse_hooks(raw)
        assert rules[0].once is False

    def test_defaults_background_false(self):
        raw = [_shell_rule()]
        rules = parse_hooks(raw)
        assert rules[0].background is False


# ---------------------------------------------------------------------------
# T120-3  Missing block → [] (safe degradation)
# ---------------------------------------------------------------------------


class TestMissingBlock:
    def test_none_returns_empty(self):
        assert parse_hooks(None) == []

    def test_string_returns_empty(self):
        assert parse_hooks("not a list") == []

    def test_int_returns_empty(self):
        assert parse_hooks(42) == []

    def test_dict_returns_empty(self):
        assert parse_hooks({"event": "SessionStart"}) == []

    def test_missing_key_returns_empty(self):
        # simulating config.get("hooks") on a raw dict without "hooks" key
        result = parse_hooks({}.get("hooks"))
        assert result == []


# ---------------------------------------------------------------------------
# T120-4/5  Validation errors — each raises HookConfigError
# ---------------------------------------------------------------------------


class TestValidationErrors:
    # ----- bad event name -----

    def test_bad_event_name_raises(self):
        raw = [
            {"event": "NonExistentEvent", "action": {"type": "shell", "command": "x"}}
        ]
        with pytest.raises(HookConfigError):
            parse_hooks(raw)

    def test_bad_event_name_message_contains_index(self):
        raw = [
            _shell_rule("SessionStart"),
            {"event": "BOGUS", "action": {"type": "shell", "command": "x"}},
        ]
        with pytest.raises(HookConfigError, match=r"hooks\[1\]"):
            parse_hooks(raw)

    def test_bad_event_name_message_contains_name(self):
        raw = [{"event": "TYPO_EVENT", "action": {"type": "shell", "command": "x"}}]
        with pytest.raises(HookConfigError, match="TYPO_EVENT"):
            parse_hooks(raw)

    def test_missing_event_key_raises(self):
        raw = [{"action": {"type": "shell", "command": "x"}}]
        with pytest.raises(HookConfigError):
            parse_hooks(raw)

    # ----- missing action -----

    def test_missing_action_raises(self):
        raw = [{"event": "SessionStart"}]
        with pytest.raises(HookConfigError):
            parse_hooks(raw)

    # ----- bad action.type -----

    def test_missing_action_type_raises(self):
        raw = [{"event": "SessionStart", "action": {"command": "x"}}]
        with pytest.raises(HookConfigError):
            parse_hooks(raw)

    def test_invalid_action_type_raises(self):
        raw = [{"event": "SessionStart", "action": {"type": "webhook", "url": "x"}}]
        with pytest.raises(HookConfigError):
            parse_hooks(raw)

    def test_invalid_action_type_message_contains_index(self):
        raw = [{"event": "SessionStart", "action": {"type": "webhook", "url": "x"}}]
        with pytest.raises(HookConfigError, match=r"hooks\[0\]"):
            parse_hooks(raw)

    def test_invalid_action_type_message_contains_type(self):
        raw = [{"event": "SessionStart", "action": {"type": "webhook", "url": "x"}}]
        with pytest.raises(HookConfigError, match="webhook"):
            parse_hooks(raw)

    # ----- shell missing command -----

    def test_shell_missing_command_raises(self):
        raw = [{"event": "SessionStart", "action": {"type": "shell"}}]
        with pytest.raises(HookConfigError):
            parse_hooks(raw)

    def test_shell_missing_command_message_contains_command(self):
        raw = [{"event": "SessionStart", "action": {"type": "shell"}}]
        with pytest.raises(HookConfigError, match="command"):
            parse_hooks(raw)

    def test_shell_missing_command_message_contains_index(self):
        raw = [
            _shell_rule(),
            {"event": "SessionEnd", "action": {"type": "shell"}},
        ]
        with pytest.raises(HookConfigError, match=r"hooks\[1\]"):
            parse_hooks(raw)

    # ----- http missing url -----

    def test_http_missing_url_raises(self):
        raw = [{"event": "PostToolUse", "action": {"type": "http"}}]
        with pytest.raises(HookConfigError):
            parse_hooks(raw)

    def test_http_missing_url_message_contains_url(self):
        raw = [{"event": "PostToolUse", "action": {"type": "http"}}]
        with pytest.raises(HookConfigError, match="url"):
            parse_hooks(raw)

    # ----- prompt missing text -----

    def test_prompt_missing_text_raises(self):
        raw = [{"event": "UserPromptSubmit", "action": {"type": "prompt"}}]
        with pytest.raises(HookConfigError):
            parse_hooks(raw)

    def test_prompt_missing_text_message_contains_text(self):
        raw = [{"event": "UserPromptSubmit", "action": {"type": "prompt"}}]
        with pytest.raises(HookConfigError, match="text"):
            parse_hooks(raw)

    # ----- PreToolUse + background: True -----

    def test_pretooluse_background_raises(self):
        raw = [
            {
                "event": "PreToolUse",
                "action": {"type": "shell", "command": "guard"},
                "background": True,
            }
        ]
        with pytest.raises(HookConfigError):
            parse_hooks(raw)

    def test_pretooluse_background_message_contains_background(self):
        raw = [
            {
                "event": "PreToolUse",
                "action": {"type": "shell", "command": "guard"},
                "background": True,
            }
        ]
        with pytest.raises(HookConfigError, match="background"):
            parse_hooks(raw)

    def test_pretooluse_background_message_contains_index(self):
        raw = [
            _shell_rule(),
            {
                "event": "PreToolUse",
                "action": {"type": "shell", "command": "guard"},
                "background": True,
            },
        ]
        with pytest.raises(HookConfigError, match=r"hooks\[1\]"):
            parse_hooks(raw)

    def test_non_intercept_background_ok(self):
        # background=True on a non-intercept event is fine
        raw = [_shell_rule("SessionEnd", background=True)]
        rules = parse_hooks(raw)
        assert rules[0].background is True

    # ----- timeout <= 0 -----

    def test_timeout_zero_raises(self):
        raw = [
            {
                "event": "SessionStart",
                "action": {"type": "shell", "command": "x", "timeout": 0},
            }
        ]
        with pytest.raises(HookConfigError):
            parse_hooks(raw)

    def test_timeout_negative_raises(self):
        raw = [
            {
                "event": "SessionStart",
                "action": {"type": "shell", "command": "x", "timeout": -5},
            }
        ]
        with pytest.raises(HookConfigError):
            parse_hooks(raw)

    def test_timeout_zero_message_contains_timeout(self):
        raw = [
            {
                "event": "SessionStart",
                "action": {"type": "shell", "command": "x", "timeout": 0},
            }
        ]
        with pytest.raises(HookConfigError, match="timeout"):
            parse_hooks(raw)

    def test_timeout_zero_message_contains_index(self):
        raw = [
            {
                "event": "SessionStart",
                "action": {"type": "shell", "command": "x", "timeout": 0},
            }
        ]
        with pytest.raises(HookConfigError, match=r"hooks\[0\]"):
            parse_hooks(raw)

    def test_http_timeout_zero_raises(self):
        raw = [
            {
                "event": "PostToolUse",
                "action": {"type": "http", "url": "https://x.com", "timeout": 0},
            }
        ]
        with pytest.raises(HookConfigError):
            parse_hooks(raw)

    def test_http_timeout_positive_ok(self):
        raw = [
            {
                "event": "PostToolUse",
                "action": {"type": "http", "url": "https://x.com", "timeout": 30},
            }
        ]
        rules = parse_hooks(raw)
        assert rules[0].action.timeout == 30

    # ----- bad if.match -----

    def test_bad_match_raises(self):
        raw = [
            {
                "event": "PreToolUse",
                "action": {"type": "shell", "command": "guard"},
                "if": {"match": "none_of", "clauses": []},
            }
        ]
        with pytest.raises(HookConfigError):
            parse_hooks(raw)

    def test_bad_match_message_contains_match(self):
        raw = [
            {
                "event": "PreToolUse",
                "action": {"type": "shell", "command": "guard"},
                "if": {"match": "none_of", "clauses": []},
            }
        ]
        with pytest.raises(HookConfigError, match="match"):
            parse_hooks(raw)

    def test_bad_match_message_contains_index(self):
        raw = [
            {
                "event": "PreToolUse",
                "action": {"type": "shell", "command": "guard"},
                "if": {"match": "bad_value", "clauses": []},
            }
        ]
        with pytest.raises(HookConfigError, match=r"hooks\[0\]"):
            parse_hooks(raw)

    def test_match_all_string_ok(self):
        raw = [
            {
                "event": "PreToolUse",
                "action": {"type": "shell", "command": "guard"},
                "if": {
                    "match": "all",
                    "clauses": [{"field": "tool_name", "pattern": "x"}],
                },
            }
        ]
        rules = parse_hooks(raw)
        from wentian.hooks.spec import Match

        assert rules[0].condition.match == Match.ALL

    def test_match_any_string_ok(self):
        raw = [
            {
                "event": "PreToolUse",
                "action": {"type": "shell", "command": "guard"},
                "if": {
                    "match": "any",
                    "clauses": [{"field": "tool_name", "pattern": "x"}],
                },
            }
        ]
        rules = parse_hooks(raw)
        from wentian.hooks.spec import Match

        assert rules[0].condition.match == Match.ANY

    # ----- action not a dict -----

    def test_action_not_dict_raises(self):
        raw = [{"event": "SessionStart", "action": "shell echo hi"}]
        with pytest.raises(HookConfigError):
            parse_hooks(raw)


# ---------------------------------------------------------------------------
# T120-5  HookConfigError is importable and is an Exception subclass
# ---------------------------------------------------------------------------


class TestHookConfigError:
    def test_is_exception_subclass(self):
        assert issubclass(HookConfigError, Exception)

    def test_can_be_raised_and_caught(self):
        with pytest.raises(HookConfigError, match="test message"):
            raise HookConfigError("test message")
