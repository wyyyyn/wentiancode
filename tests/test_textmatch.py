"""v0.12 · C93 · F80（任务 T117）— 四模式匹配叶子 textmatch.match_one 的单元测试。

TDD 红-绿-重构：先确认因功能缺失而失败，再实现转绿。
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# 精确匹配（exact）
# ---------------------------------------------------------------------------


def test_exact_hit():
    """精确——pattern 与 value 完全相同 → True。"""
    from wentian.textmatch import match_one

    assert match_one("Bash", "Bash") is True


def test_exact_miss():
    """精确——pattern 与 value 不同 → False。"""
    from wentian.textmatch import match_one

    assert match_one("Bash", "Read") is False


def test_exact_empty_matches_empty():
    """空 pattern 仅匹配空串 → True。"""
    from wentian.textmatch import match_one

    assert match_one("", "") is True


def test_exact_empty_no_match_nonempty():
    """空 pattern 不匹配非空串 → False。"""
    from wentian.textmatch import match_one

    assert match_one("", "Bash") is False


# ---------------------------------------------------------------------------
# 反向（negation）：! 前缀
# ---------------------------------------------------------------------------


def test_negation_hit():
    """!Bash 对非 Bash 值 → True（内层不命中则外层命中）。"""
    from wentian.textmatch import match_one

    assert match_one("!Bash", "Read") is True


def test_negation_miss():
    """!Bash 对 Bash → False（内层命中则外层不命中）。"""
    from wentian.textmatch import match_one

    assert match_one("!Bash", "Bash") is False


def test_negation_recursive_glob():
    """!git * 对 "npm i" → True（内层 glob 不命中）。"""
    from wentian.textmatch import match_one

    assert match_one("!git *", "npm i") is True


def test_negation_recursive_glob_miss():
    """!git * 对 "git push" → False（内层 glob 命中）。"""
    from wentian.textmatch import match_one

    assert match_one("!git *", "git push") is False


# ---------------------------------------------------------------------------
# 正则（regex）：/pattern/ 包裹，re.search 语义
# ---------------------------------------------------------------------------


def test_regex_search_hit():
    """/rm\\s+-rf/ 对含 rm  -rf 的串 → True（re.search，非全匹配）。"""
    from wentian.textmatch import match_one

    assert match_one("/rm\\s+-rf/", "rm  -rf /") is True


def test_regex_search_miss():
    """/^git/ 对 "npm i" → False。"""
    from wentian.textmatch import match_one

    assert match_one("/^git/", "npm i") is False


def test_regex_search_not_fullmatch():
    """/git/ 对 "do git push" → True（re.search 子串匹配）。"""
    from wentian.textmatch import match_one

    assert match_one("/git/", "do git push") is True


def test_regex_anchor_hit():
    """/^git/ 对 "git push" → True（以 git 开头）。"""
    from wentian.textmatch import match_one

    assert match_one("/^git/", "git push") is True


def test_regex_malformed_returns_false():
    """非法正则 /[/ → 不抛异常，返回 False（AC98）。"""
    from wentian.textmatch import match_one

    result = match_one("/[/", "anything")
    assert result is False


def test_regex_malformed_does_not_raise():
    """非法正则 /[/ 调用不抛任何异常。"""
    from wentian.textmatch import match_one

    try:
        match_one("/[/", "value")
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(f"match_one raised unexpectedly: {exc}") from exc


# ---------------------------------------------------------------------------
# Glob 匹配：含 * ? [ 元字符，fnmatch.fnmatchcase
# ---------------------------------------------------------------------------


def test_glob_star_hit():
    """git * 对 "git push" → True。"""
    from wentian.textmatch import match_one

    assert match_one("git *", "git push") is True


def test_glob_star_miss():
    """git * 对 "npm i" → False。"""
    from wentian.textmatch import match_one

    assert match_one("git *", "npm i") is False


def test_glob_question_mark_hit():
    """Ba?h 对 "Bash" → True（? 匹配单字符）。"""
    from wentian.textmatch import match_one

    assert match_one("Ba?h", "Bash") is True


def test_glob_question_mark_miss():
    """Ba?h 对 "Baash" → False（? 只匹配一个字符）。"""
    from wentian.textmatch import match_one

    assert match_one("Ba?h", "Baash") is False


def test_glob_bracket_hit():
    """[BR]ead 对 "Read" → True（字符集）。"""
    from wentian.textmatch import match_one

    assert match_one("[BR]ead", "Read") is True


def test_glob_bracket_miss():
    """[BR]ead 对 "Lead" → False。"""
    from wentian.textmatch import match_one

    assert match_one("[BR]ead", "Lead") is False


def test_glob_case_sensitive():
    """fnmatchcase 区分大小写：git * 不匹配 "Git push"。"""
    from wentian.textmatch import match_one

    assert match_one("git *", "Git push") is False
