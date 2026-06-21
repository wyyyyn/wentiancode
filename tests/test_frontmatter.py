"""v0.13 · C109 · F93/N55 — parse_frontmatter 共享原语单元测试。

TDD 红-绿-重构：先跑此文件确认因 frontmatter.py 不存在而失败，再实现转绿。
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# parse_frontmatter — 有效 frontmatter
# ---------------------------------------------------------------------------


def test_parse_frontmatter_full():
    """完整 frontmatter（str 与 list 混合）正确解析，正文分离。"""
    from wentian.frontmatter import parse_frontmatter

    text = "---\nname: x\ndescription: d\ntools: [read_file, write_file]\n---\n正文内容"
    data, body = parse_frontmatter(text)
    assert data == {
        "name": "x",
        "description": "d",
        "tools": ["read_file", "write_file"],
    }
    assert body == "正文内容"


def test_parse_frontmatter_scalar_string():
    """key: value 行解析为 str。"""
    from wentian.frontmatter import parse_frontmatter

    text = "---\nkey: hello world\n---\nbody\n"
    data, body = parse_frontmatter(text)
    assert isinstance(data["key"], str)
    assert data["key"] == "hello world"
    assert body == "body\n"


def test_parse_frontmatter_list_value():
    """list 值 [a, b] 解析为 Python list（而非 tuple）。"""
    from wentian.frontmatter import parse_frontmatter

    text = "---\ntools: [read_file, write_file]\n---\nbody\n"
    data, body = parse_frontmatter(text)
    assert isinstance(data["tools"], list)
    assert data["tools"] == ["read_file", "write_file"]


def test_parse_frontmatter_empty_body():
    """frontmatter 后无正文 → body 为空串。"""
    from wentian.frontmatter import parse_frontmatter

    text = "---\nname: bare\n---\n"
    data, body = parse_frontmatter(text)
    assert data["name"] == "bare"
    assert body == ""


def test_parse_frontmatter_body_preserved():
    """正文内容原样保留，不被 frontmatter 解析器改动。"""
    from wentian.frontmatter import parse_frontmatter

    text = "---\nname: x\n---\n# Header\n\nSome text\n"
    data, body = parse_frontmatter(text)
    assert body == "# Header\n\nSome text\n"


# ---------------------------------------------------------------------------
# parse_frontmatter — 无 frontmatter / 缺围栏
# ---------------------------------------------------------------------------


def test_parse_frontmatter_no_fence_returns_empty_dict_and_original():
    """无 --- 围栏 → ({}, 原文)，不抛。"""
    from wentian.frontmatter import parse_frontmatter

    original = "# 只有正文，没有 frontmatter\n"
    data, body = parse_frontmatter(original)
    assert data == {}
    assert body == original


def test_parse_frontmatter_unclosed_fence_returns_empty_dict_and_original():
    """--- 开头但未闭合 → ({}, 原文)，不抛。"""
    from wentian.frontmatter import parse_frontmatter

    original = "---\nname: broken\n没有闭合围栏\n"
    data, body = parse_frontmatter(original)
    assert data == {}
    assert body == original


def test_parse_frontmatter_empty_string():
    """空字符串 → ({}, '')，不抛。"""
    from wentian.frontmatter import parse_frontmatter

    data, body = parse_frontmatter("")
    assert data == {}
    assert body == ""


# ---------------------------------------------------------------------------
# parse_frontmatter — 异常容错（不抛）
# ---------------------------------------------------------------------------


def test_parse_frontmatter_no_throw_on_weird_input():
    """奇怪但有围栏的输入 → 返回 ({}, body) 或解析结果，不抛。"""
    from wentian.frontmatter import parse_frontmatter

    text = "---\n: no key\n---\nbody\n"
    # 应不抛；若解析失败返回 ({}, body) 也可
    data, body = parse_frontmatter(text)
    assert isinstance(data, dict)
    assert isinstance(body, str)


def test_parse_frontmatter_comments_ignored():
    """frontmatter 内 # 注释行被忽略。"""
    from wentian.frontmatter import parse_frontmatter

    text = "---\n# 这是注释\nname: myskill\n---\nbody\n"
    data, body = parse_frontmatter(text)
    assert data.get("name") == "myskill"
    assert "#" not in str(data.keys())
