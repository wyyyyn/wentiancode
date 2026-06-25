"""v0.6 · C37 · F48 (task T77)

confirm_action — the human-in-the-loop three-way approval menu shown when the
permission pipeline returns Ask.

Renders a multi-line block (tool name + key-argument preview + trigger reason +
a three-option menu) and lets the user pick:

* ↑↓ moves the cursor (clamped at edges, no wrap), Enter confirms;
* number keys ``1`` / ``2`` / ``3`` select directly;
* default highlight is **"Allow Once"** (ALLOW_ONCE);
* Esc / Ctrl+C raise :class:`Cancelled` — the caller (REPL) catches it to end
  the current turn cleanly without exiting the program (N13/AC52).

Design mirrors :mod:`wentian.ui.select`: an inline prompt_toolkit
``Application`` with a ``FormattedTextControl`` and ``erase_when_done=True``.
The async entry point :func:`confirm_action` uses ``Application.run_async`` so
the REPL can ``await`` it inside the agent loop's event consumption — no nested
event loop, no task leak (the awaited application owns its own lifecycle and is
torn down on exit/exception).

UI layer: prompt_toolkit import is allowed here.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl

__all__ = ["Choice", "Cancelled", "confirm_action"]


class Choice(Enum):
    """Three-way result states for the human-in-the-loop approval."""

    ALLOW_ONCE = "allow_once"
    ALLOW_ALWAYS = "allow_always"
    DENY = "deny"


class Cancelled(Exception):
    """User pressed Esc / Ctrl+C to cancel the current approval (neither allow nor deny).

    Caught by the caller (REPL) to end the current turn cleanly without exiting
    the entire program (N13/AC52).
    """


#: Menu item order is display order + digit key 1/2/3 mapping; default highlight is item 0 "Allow Once".
_OPTIONS: tuple[tuple[Choice, str], ...] = (
    (Choice.ALLOW_ONCE, "Allow once"),
    (Choice.ALLOW_ALWAYS, "Always allow (write to local rules)"),
    (Choice.DENY, "Deny this time"),
)

#: Cinnabar — shared brand accent color with banner / status bar.
_CINNABAR = "#C84B31"


async def confirm_action(
    *,
    tool_name: str,
    preview: str,
    reason: str,
    input: Any = None,
    output: Any = None,
) -> Choice:
    """Display the multi-line approval block and return the user's choice (async, for await inside the agent loop).

    Parameters
    ----------
    tool_name:
        Name of the tool pending approval (e.g. ``run_command`` / ``write_file``).
    preview:
        Key-argument preview (command string or path), so the user can see what
        is being approved.
    reason:
        Trigger reason (from :class:`~wentian.permissions.decision.Decision.reason`
        or the mode fallback message).
    input / output:
        prompt_toolkit Input/Output (injected for testing; None → real terminal).

    Returns
    -------
    Choice
        Three-way result.

    Raises
    ------
    Cancelled
        User pressed Esc or Ctrl+C.
    """
    current: list[int] = [0]  # default highlight "Allow Once"

    # ------------------------------------------------------------------
    # Renderer
    # ------------------------------------------------------------------
    def get_text() -> FormattedText:
        fragments: list[tuple[str, str]] = []
        # title line + argument preview + trigger reason (multi-line block).
        fragments.append((f"bold {_CINNABAR}", f"Confirmation required: {tool_name}"))
        fragments.append(("", "\n"))
        if preview:
            fragments.append(("", f"  {preview}"))
            fragments.append(("", "\n"))
        if reason:
            fragments.append(("dim", f"  Reason: {reason}"))
            fragments.append(("", "\n"))
        fragments.append(("", "\n"))
        # three-option menu.
        for i, (_choice, label) in enumerate(_OPTIONS):
            num = i + 1
            if i == current[0]:
                fragments.append(("reverse", f"❯ {num}. {label}"))
            else:
                fragments.append(("", f"  {num}. {label}"))
            fragments.append(("", "\n"))
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
        if current[0] < len(_OPTIONS) - 1:
            current[0] += 1

    @kb.add("enter")
    def _enter(event) -> None:
        event.app.exit(result=_OPTIONS[current[0]][0])

    @kb.add("1")
    def _one(event) -> None:
        event.app.exit(result=Choice.ALLOW_ONCE)

    @kb.add("2")
    def _two(event) -> None:
        event.app.exit(result=Choice.ALLOW_ALWAYS)

    @kb.add("3")
    def _three(event) -> None:
        event.app.exit(result=Choice.DENY)

    @kb.add("c-c")
    @kb.add("escape", eager=True)
    def _cancel(event) -> None:
        # exit with a sentinel; raised as Cancelled after run_async returns.
        event.app.exit(result=_CANCEL)

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    n_lines = 1  # title
    if preview:
        n_lines += 1
    if reason:
        n_lines += 1
    n_lines += 1  # blank separator
    n_lines += len(_OPTIONS)

    control = FormattedTextControl(get_text, focusable=True)
    window = Window(content=control, height=n_lines)
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

    app: Application[Any] = Application(**app_kwargs)
    result = await app.run_async()
    if result is _CANCEL or result is None:
        raise Cancelled()
    return result


#: Cancellation sentinel — distinct from the three Choice values and None.
_CANCEL = object()
