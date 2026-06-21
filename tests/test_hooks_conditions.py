"""v0.12 · C95 · F80（任务 T119）— evaluate(condition, context) -> bool 的单元测试。

TDD 红-绿-重构：先确认因功能缺失而失败，再实现转绿。
"""

from __future__ import annotations


def test_evaluate_none_condition_returns_true():
    """condition=None → True（无条件恒触发）。"""
    from wentian.hooks.conditions import evaluate

    assert evaluate(None, {}) is True
    assert evaluate(None, {"command": "Bash"}) is True


def test_evaluate_empty_clauses_returns_true():
    """Condition(clauses=()) → True（空子句集恒真）。"""
    from wentian.hooks.conditions import evaluate
    from wentian.hooks.spec import Condition, Match

    assert evaluate(Condition(match=Match.ALL, clauses=()), {}) is True
    assert evaluate(Condition(match=Match.ANY, clauses=()), {}) is True


def test_evaluate_all_all_true():
    """Match.ALL：两子句均命中 → True。"""
    from wentian.hooks.conditions import evaluate
    from wentian.hooks.spec import Clause, Condition, Match

    condition = Condition(
        match=Match.ALL,
        clauses=(
            Clause("tool_name", "Bash"),
            Clause("command", "/echo/"),
        ),
    )
    ctx = {"tool_name": "Bash", "command": "echo hello"}
    assert evaluate(condition, ctx) is True


def test_evaluate_all_one_false():
    """Match.ALL：一子句不命中 → False。"""
    from wentian.hooks.conditions import evaluate
    from wentian.hooks.spec import Clause, Condition, Match

    condition = Condition(
        match=Match.ALL,
        clauses=(
            Clause("tool_name", "Bash"),
            Clause("command", "/rm/"),  # "echo hello" 不含 rm
        ),
    )
    ctx = {"tool_name": "Bash", "command": "echo hello"}
    assert evaluate(condition, ctx) is False


def test_evaluate_any_one_true():
    """Match.ANY：一子句命中 → True。"""
    from wentian.hooks.conditions import evaluate
    from wentian.hooks.spec import Clause, Condition, Match

    condition = Condition(
        match=Match.ANY,
        clauses=(
            Clause("tool_name", "NotBash"),  # 不命中
            Clause("command", "/echo/"),  # 命中
        ),
    )
    ctx = {"tool_name": "Bash", "command": "echo hello"}
    assert evaluate(condition, ctx) is True


def test_evaluate_any_all_false():
    """Match.ANY：全子句不命中 → False。"""
    from wentian.hooks.conditions import evaluate
    from wentian.hooks.spec import Clause, Condition, Match

    condition = Condition(
        match=Match.ANY,
        clauses=(
            Clause("tool_name", "NotBash"),
            Clause("command", "/rm/"),
        ),
    )
    ctx = {"tool_name": "Bash", "command": "echo hello"}
    assert evaluate(condition, ctx) is False


def test_evaluate_missing_field_uses_empty_string():
    """字段缺失时按空串参与匹配：Clause("command","/rm/") 对无 command ctx → False。"""
    from wentian.hooks.conditions import evaluate
    from wentian.hooks.spec import Clause, Condition, Match

    condition = Condition(
        match=Match.ALL,
        clauses=(Clause("command", "/rm/"),),
    )
    # ctx 没有 command 键 → 等价于 match_one("/rm/", "") → False
    assert evaluate(condition, {}) is False


def test_evaluate_missing_field_negation_returns_true():
    """字段缺失 → 空串；!Bash 对空串 → match_one("!Bash","") → not match_one("Bash","") → not False → True。"""
    from wentian.hooks.conditions import evaluate
    from wentian.hooks.spec import Clause, Condition, Match

    condition = Condition(
        match=Match.ALL,
        clauses=(Clause("tool_name", "!Bash"),),
    )
    # ctx 没有 tool_name 键 → 等价于 match_one("!Bash", "") → True
    assert evaluate(condition, {}) is True


def test_evaluate_single_all_clause_match():
    """单子句 Match.ALL 精确匹配：Clause("event","SessionStart") ctx 命中 → True。"""
    from wentian.hooks.conditions import evaluate
    from wentian.hooks.spec import Clause, Condition, Match

    condition = Condition(
        match=Match.ALL,
        clauses=(Clause("event", "SessionStart"),),
    )
    assert evaluate(condition, {"event": "SessionStart"}) is True
    assert evaluate(condition, {"event": "SessionEnd"}) is False


def test_evaluate_glob_pattern():
    """glob 模式：Clause("tool_name","*File*") 对 "ReadFile" → True，对 "Bash" → False。"""
    from wentian.hooks.conditions import evaluate
    from wentian.hooks.spec import Clause, Condition, Match

    condition = Condition(
        match=Match.ALL,
        clauses=(Clause("tool_name", "*File*"),),
    )
    assert evaluate(condition, {"tool_name": "ReadFile"}) is True
    assert evaluate(condition, {"tool_name": "Bash"}) is False
