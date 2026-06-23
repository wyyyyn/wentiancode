"""Custom, self-contained Markdown theme for WentianCode output (v0.14 · C131 · F105).

Replaces Rich's bare default Markdown rendering with a centrally-tuned theme that
strengthens typesetting in three areas:

1. Spacing / whitespace — paragraph, heading and list-item vertical rhythm are
   unified via outer :class:`~rich.padding.Padding` (not cramped, not too loose).
2. Code blocks & syntax highlight — a solid dark Pygments ``code_theme`` plus a
   distinct inline ``code`` style, baked in so highlighting never depends on the
   printing console's theme.
3. Headings / lists / tables — Rich's centered, heavy default ``h1`` is replaced
   by a lightweight left-aligned heading with a marker affordance; list bullets
   are unified to a single glyph; tables keep aligned columns with a light border.

Public API
----------
``render_markdown(text)`` is the load-bearing entry point. It returns a fully
themed renderable that is *self-contained*: every ``markdown.*`` style it relies
on is pushed onto the console during its own render (via ``console.use_theme``),
so callers can simply ``console.print(render_markdown(text))`` on any vanilla
console — in both the streaming Live composer and the final scrollback print —
without having to install a theme elsewhere.

LEAF module: imports only from ``rich`` and the stdlib. No imports from
``wentian.agent`` / ``repl`` / ``providers`` / ``tools`` / ``render``.
"""

from __future__ import annotations

from rich.console import Console, ConsoleOptions, RenderableType, RenderResult
from rich.markdown import Heading, ListItem, Markdown
from rich.padding import Padding
from rich.segment import Segment
from rich.text import Text
from rich.theme import Theme

__all__ = [
    "CODE_THEME",
    "INLINE_CODE_THEME",
    "LIST_BULLET",
    "HEADING_MARKERS",
    "MARKDOWN_THEME",
    "WentianMarkdown",
    "render_markdown",
]

# A solid, widely-available dark Pygments theme for fenced code blocks. Chosen
# for good contrast on dark terminals and broad lexer coverage.
CODE_THEME: str = "github-dark"

# Pygments theme used for inline ``code`` lexing (kept consistent with blocks).
INLINE_CODE_THEME: str = "github-dark"

# A single, unified bullet glyph for every unordered list item.
LIST_BULLET: str = "•"

# Lightweight left-aligned heading markers (no centered layout, no framed panel).
HEADING_MARKERS: dict[str, str] = {
    "h1": "▌ ",
    "h2": "▎ ",
    "h3": "› ",
    "h4": "· ",
    "h5": "· ",
    "h6": "· ",
}

# Centrally-tuned style map. Pushed onto the printing console during render so
# the returned renderable is self-contained regardless of the outer console.
MARKDOWN_THEME: Theme = Theme(
    {
        "markdown.paragraph": "none",
        "markdown.text": "none",
        "markdown.em": "italic",
        "markdown.emph": "italic",
        "markdown.strong": "bold",
        # Distinct, readable inline code (not the default washed-out cyan-on-black).
        "markdown.code": "bold #e6db74 on #2b2b2b",
        "markdown.code_block": "#f8f8f2 on #1e1e1e",
        "markdown.block_quote": "italic #9aa0a6",
        "markdown.list": "none",
        "markdown.item": "none",
        "markdown.item.bullet": "bold #5fafff",
        "markdown.item.number": "bold #5fafff",
        "markdown.hr": "dim",
        # Lightweight headings: clear hierarchy via color/weight, no heavy panel.
        "markdown.h1": "bold #ffffff",
        "markdown.h1.border": "none",
        "markdown.h2": "bold #5fafff",
        "markdown.h3": "bold #87d7ff",
        "markdown.h4": "italic #87d7ff",
        "markdown.h5": "italic",
        "markdown.h6": "dim italic",
        "markdown.h7": "dim italic",
        "markdown.link": "underline #5fafff",
        "markdown.link_url": "dim underline #5fafff",
        "markdown.s": "strike",
        # Aligned tables with a quiet border.
        "markdown.table.border": "#6c7086",
        "markdown.table.header": "bold #87d7ff",
        "markdown.table.title": "bold",
    }
)


class _LightHeading(Heading):
    """Lightweight, left-aligned heading with a marker affordance.

    Rich's default ``h1`` centers the text across the full width (and older Rich
    framed it in a panel). This renders every heading left-aligned, prefixed with
    a level-specific marker, so headings read as compact section labels rather
    than billboard banners.
    """

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        text = self.text.copy()
        text.justify = "left"
        marker = HEADING_MARKERS.get(self.tag, "")
        if marker:
            prefix = Text(marker, style=self.style_name)
            prefix.append_text(text)
            text = prefix
        yield text


class _UnifiedListItem(ListItem):
    """List item that renders every unordered bullet with :data:`LIST_BULLET`."""

    def render_bullet(self, console: Console, options: ConsoleOptions) -> RenderResult:
        render_options = options.update(width=options.max_width - 3)
        lines = console.render_lines(self.elements, render_options, style=self.style)
        bullet_style = console.get_style("markdown.item.bullet", default="none")

        bullet = Segment(f" {LIST_BULLET} ", bullet_style)
        padding = Segment(" " * 3, bullet_style)
        new_line = Segment("\n")
        first = True
        for line in lines:
            yield bullet if first else padding
            first = False
            yield from line
            yield new_line


class WentianMarkdown(Markdown):
    """:class:`~rich.markdown.Markdown` subclass wired with the WentianCode theme.

    Overrides the ``heading_open`` and ``list_item_open`` element renderers and
    defaults ``code_theme`` / ``inline_code_theme`` to the project's dark themes.
    Styling still resolves through ``markdown.*`` style names; :func:`render_markdown`
    pushes :data:`MARKDOWN_THEME` during render so it is self-contained.
    """

    elements = {
        **Markdown.elements,
        "heading_open": _LightHeading,
        "list_item_open": _UnifiedListItem,
    }

    def __init__(self, markup: str) -> None:
        super().__init__(
            markup,
            code_theme=CODE_THEME,
            inline_code_theme=INLINE_CODE_THEME,
        )


class _ThemedMarkdown:
    """Self-contained wrapper: pushes :data:`MARKDOWN_THEME` while rendering.

    This is what makes :func:`render_markdown` independent of the printing
    console's theme — all ``markdown.*`` styles resolve against our theme for the
    duration of the render, then the console's prior theme is restored.
    """

    def __init__(self, renderable: RenderableType) -> None:
        self._renderable = renderable

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        with console.use_theme(MARKDOWN_THEME):
            yield self._renderable


def render_markdown(text: str) -> RenderableType:
    """Return a fully-themed, self-contained renderable for ``text``.

    All styling (code theme, lightweight headings, inter-block spacing,
    list/table styling) is baked into the returned renderable — it does not
    depend on the console carrying a special :class:`~rich.theme.Theme` pushed
    elsewhere. ``render.py`` simply does ``console.print(render_markdown(text))``
    in both the streaming Live composer and the final scrollback print.
    """
    markdown = WentianMarkdown(text)
    # One blank line of top/bottom breathing room for inter-block rhythm; the
    # element-level styles handle the rest. Padding(top, right, bottom, left).
    padded = Padding(markdown, (1, 0, 1, 0))
    return _ThemedMarkdown(padded)
