"""v0.13 · C109 · F93/N55 — shared frontmatter parsing primitives (stdlib leaf module).

Public API: ``parse_frontmatter(text) -> tuple[dict, str]``

Behavior contract:
- Text starting with a ``---`` line and having a closing ``---`` → parses frontmatter, returns ``(data, body)``.
- No ``---`` fence / unclosed fence → returns ``({}, original text)``, does not raise.
- frontmatter parse error / non-mapping → returns ``({}, body)``, does not raise.
- ``key: [a, b]`` inline list → Python list (not tuple).
- ``key: value`` scalar → str.

Layering rule: stdlib only, no third-party libraries.
"""

from __future__ import annotations

__all__ = ["parse_frontmatter"]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _split_frontmatter(text: str) -> tuple[str, str] | None:
    """Extract frontmatter within ``---`` fences and the body that follows.

    Requires text to start with a ``---`` line, and a closing ``---`` line must exist.
    Returns ``(frontmatter_text, body_text)``; returns None if not satisfied.
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
    """Strip leading/trailing whitespace and matching quotes from a scalar."""
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def _parse_inline_list(raw: str) -> list[str]:
    """Parse ``[a, b, c]`` inline list → list (public API returns list, not tuple)."""
    inner = raw.strip()[1:-1]  # strip [ ]
    items = [_parse_scalar(part) for part in inner.split(",")]
    return [item for item in items if item]


def _parse_front_text(front: str) -> dict[str, object]:
    """Parse frontmatter text into a dict.

    Supports: ``key: scalar``, ``key: [a, b]`` inline list, and immediately following
    ``  - item`` block lists. Comment lines (starting with ``#``) and blank lines are ignored.
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
            # may be followed by a block list: subsequent "  - item" lines
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
# Public API
# ---------------------------------------------------------------------------


def parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Parse ``---`` frontmatter in a Markdown text.

    Parameters
    ----------
    text : str
        Full text (may or may not contain a frontmatter fence).

    Returns
    -------
    ``(data, body)``:
    - Valid frontmatter found and parsed successfully → ``(parsed dict, body after fence)``.
    - No fence / unclosed fence / parse error → ``({}, original text)``, does not raise.
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
