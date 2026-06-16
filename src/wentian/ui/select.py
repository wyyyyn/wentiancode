"""v0.2 · C4 · F14（任务 T19）

select_provider — inline arrow-key list selector for choosing a backend
before entering a conversation.

Design:
- Inline prompt_toolkit Application (full_screen=False, erase_when_done=True).
- Single Window with FormattedTextControl; height equals number of providers.
- Highlighted row: ``❯ name``; others: ``  name``.  Default name carries
  suffix ``（默认）``.
- Initial highlight = names.index(default), fallback 0.
- Up/Down clamp at edges (no wrap).
- Enter confirms; Ctrl+C cancels (returns default).
- Bare Escape cancel: attempted with eager=True on the escape binding, but
  bare ``\\x1b`` conflicts with the ANSI escape sequences used by arrow keys
  (``\\x1b[A`` / ``\\x1b[B``).  To avoid eating arrow-key events, bare-Escape
  cancel is **dropped**; Ctrl+C is the spec-required cancel path.
- input/output forwarded to Application only when not None.
"""

from __future__ import annotations

from typing import Any

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl

__all__ = ["select_provider"]


def select_provider(
    names: list[str],
    default: str,
    *,
    input: Any = None,
    output: Any = None,
) -> str:
    """Display an inline arrow-key list and return the selected provider name.

    Parameters
    ----------
    names:
        Ordered list of provider names to display.
    default:
        The name pre-highlighted and returned on cancel (Ctrl+C).
    input:
        prompt_toolkit Input object (inject for tests; None → real terminal).
    output:
        prompt_toolkit Output object (inject for tests; None → real terminal).

    Returns
    -------
    str
        The selected provider name, or *default* on cancel.
    """
    if not names:
        return default

    # Initial highlight index: find default in list, fallback to 0.
    try:
        current: list[int] = [names.index(default)]
    except ValueError:
        current = [0]

    # ------------------------------------------------------------------
    # Renderer: builds FormattedText for the current state.
    # ------------------------------------------------------------------

    def get_text() -> FormattedText:
        fragments: list[tuple[str, str]] = []
        for i, name in enumerate(names):
            suffix = "（默认）" if name == default else ""
            if i == current[0]:
                # Highlighted row
                fragments.append(("reverse", f"❯ {name}{suffix}"))
            else:
                fragments.append(("", f"  {name}{suffix}"))
            fragments.append(("", "\n"))
        # Remove trailing newline to avoid blank line at bottom
        if fragments and fragments[-1] == ("", "\n"):
            fragments.pop()
        return FormattedText(fragments)

    # ------------------------------------------------------------------
    # Key bindings
    # ------------------------------------------------------------------

    kb = KeyBindings()

    @kb.add("up")
    def _up(event) -> None:
        if current[0] > 0:
            current[0] -= 1

    @kb.add("down")
    def _down(event) -> None:
        if current[0] < len(names) - 1:
            current[0] += 1

    @kb.add("enter")
    def _enter(event) -> None:
        event.app.exit(result=names[current[0]])

    @kb.add("c-c")
    def _cancel(event) -> None:
        event.app.exit(result=default)

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------

    control = FormattedTextControl(get_text, focusable=True)
    window = Window(
        content=control,
        height=len(names),
    )
    layout = Layout(HSplit([window]))

    app_kwargs: dict[str, Any] = dict(
        layout=layout,
        key_bindings=kb,
        full_screen=False,
        erase_when_done=True,
    )
    if input is not None:
        app_kwargs["input"] = input
    if output is not None:
        app_kwargs["output"] = output

    app: Application[str] = Application(**app_kwargs)
    result: str = app.run()
    # app.exit(result=...) sets result; if somehow None (shouldn't happen),
    # fall back to default.
    return result if result is not None else default
