"""v0.13 · C109 · F93/N55 — 共享 frontmatter 解析原语（stdlib 叶子模块）。

公开 API：``parse_frontmatter(text) -> tuple[dict, str]``

行为约定：
- 文本以 ``---`` 开头行且存在闭合 ``---`` → 解析 frontmatter，返回 ``(data, body)``。
- 无 ``---`` 围栏 / 围栏未闭合 → 返回 ``({}, 原文)``，不抛。
- frontmatter 解析出错 / 非映射 → 返回 ``({}, body)``，不抛。
- ``key: [a, b]`` 内联列表 → Python list（不是 tuple）。
- ``key: value`` 标量 → str。

分层铁律：stdlib only，不引任何第三方库。
"""

from __future__ import annotations

__all__ = ["parse_frontmatter"]


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


def _split_frontmatter(text: str) -> tuple[str, str] | None:
    """切出 ``---`` 围栏内的 frontmatter 与其后正文。

    要求文本以 ``---`` 起始行开头，且后续存在闭合的 ``---`` 行。
    返回 ``(frontmatter_text, body_text)``；不符合 ⇒ None。
    """
    lines = text.splitlines(keepends=True)
    if not lines:
        return None
    if lines[0].strip() != "---":
        return None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            front = "".join(lines[1:idx])
            body = "".join(lines[idx + 1 :])
            return front, body
    return None


def _parse_scalar(raw: str) -> str:
    """去掉标量首尾空白与成对引号。"""
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def _parse_inline_list(raw: str) -> list[str]:
    """解析 ``[a, b, c]`` 内联列表 → list（公开 API 返回 list，非 tuple）。"""
    inner = raw.strip()[1:-1]  # 去掉 [ ]
    items = [_parse_scalar(part) for part in inner.split(",")]
    return [item for item in items if item]


def _parse_front_text(front: str) -> dict[str, object]:
    """把 frontmatter 文本解析成 dict。

    支持：``key: scalar``、``key: [a, b]`` 内联列表、以及紧随的
    ``  - item`` 块状列表。注释行（``#`` 起始）与空行忽略。
    """
    data: dict[str, object] = {}
    lines = front.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue
        key, _, rest = line.partition(":")
        key = key.strip()
        if not key:
            i += 1
            continue
        rest = rest.strip()
        if rest.startswith("[") and rest.endswith("]"):
            data[key] = _parse_inline_list(rest)
            i += 1
            continue
        if rest == "":
            # 可能跟随块状列表：随后的 "  - item" 行
            items: list[str] = []
            j = i + 1
            while j < len(lines):
                item_line = lines[j]
                item_stripped = item_line.strip()
                if item_stripped.startswith("- "):
                    items.append(_parse_scalar(item_stripped[2:]))
                    j += 1
                elif item_stripped == "-":
                    items.append("")
                    j += 1
                elif item_stripped == "" or item_stripped.startswith("#"):
                    j += 1
                else:
                    break
            if items:
                data[key] = [item for item in items if item]
                i = j
                continue
            data[key] = ""
            i += 1
            continue
        data[key] = _parse_scalar(rest)
        i += 1
    return data


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------


def parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """解析 Markdown 文本中的 ``---`` frontmatter。

    参数
    ----
    text : str
        完整文本（可含也可不含 frontmatter 围栏）。

    返回
    ----
    ``(data, body)``：
    - 有有效 frontmatter 且解析成功 → ``(解析结果 dict, 围栏后正文)``。
    - 无围栏 / 围栏未闭合 / 解析出错 → ``({}, 原始 text)``，不抛。
    """
    split = _split_frontmatter(text)
    if split is None:
        return {}, text

    front, body = split
    try:
        data = _parse_front_text(front)
    except Exception:
        return {}, body

    return data, body
