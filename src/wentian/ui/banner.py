"""v0.2 · C1 · F13 (task T16, T27/T28/T29 revision: =^_^= text face + info lines)

Startup banner for WentianCode — pure function, no side effects.

Layout mirrors Claude Code's header: the ``=^_^=`` text face on the left,
three info lines on the right, no panel box. Text face renders identically
in every monospace font (the pixel-art attempt did not — see ui/mascot.py).
"""

from __future__ import annotations

from rich.table import Table
from rich.text import Text

from wentian.ui.mascot import FACE_STYLE, TEXT_FACES

__all__ = ["build_banner"]


def build_banner(
    *,
    version: str,
    provider_name: str,
    model: str,
    session_id: str,
    resumed: bool,
) -> Table:
    """Return the WentianCode startup banner (=^_^= face + info lines).

    v0.2 · C1 · F13 (task T29 revision) — Claude Code-style two-column layout, no border panel.

    Parameters
    ----------
    version:       Semver string, e.g. ``"0.2.0"``.
    provider_name: Human-readable provider name from ``Provider.name``.
    model:         Model identifier string; may be empty.
    session_id:    Short ID of the current session.
    resumed:       ``True`` when restoring an existing session.
    """
    info = Text()
    info.append("WentianCode", style="bold")
    info.append(f" v{version}", style="dim")
    info.append("\n")
    if model:
        info.append(provider_name, style="#C84B31")
        info.append(" · ", style="dim")
        info.append(model)
    else:
        info.append(provider_name, style="#C84B31")
    info.append("\n")
    label = "Resumed" if resumed else "New session"
    info.append(f"{label} ", style="dim")
    info.append(session_id, style="italic dim")

    face = Text(TEXT_FACES[0], style=FACE_STYLE)

    grid = Table.grid(padding=(0, 2))
    grid.add_column(no_wrap=True)
    grid.add_column()
    grid.add_row(face, info)
    return grid
