"""v0.2 · C1 · F13（任务 T16）

Startup banner for WentianCode — pure function returning a Rich Panel.
No side effects; caller is responsible for printing.
"""

from __future__ import annotations

from rich import box
from rich.panel import Panel
from rich.text import Text

__all__ = ["build_banner"]

# ---------------------------------------------------------------------------
# Wordmark — small stylised representation of 文天 / WENTIAN (3 lines, tasteful)
# ---------------------------------------------------------------------------

_WORDMARK = """\
  ╶─  文 天  ─╴
  W  E  N  T  I  A  N\
"""


def build_banner(
    *,
    version: str,
    provider_name: str,
    model: str,
    session_id: str,
    resumed: bool,
) -> Panel:
    """Return a Rich Panel containing the WentianCode startup banner.

    Parameters
    ----------
    version:       Semver string, e.g. ``"0.2.0"``.
    provider_name: Human-readable provider name from ``Provider.name``.
    model:         Model identifier string; may be empty.
    session_id:    UUID or short ID of the current session.
    resumed:       ``True`` when restoring an existing session.

    v0.2 · C1 · F13（任务 T16）
    """
    body = Text()

    # Wordmark
    body.append(_WORDMARK, style="bold cyan")
    body.append("\n\n")

    # Version
    body.append(f"v{version}", style="dim")
    body.append("\n")

    # Provider + model
    if model:
        body.append(f"{provider_name}:{model}", style="cyan")
    else:
        body.append(provider_name, style="cyan")
    body.append("\n")

    # Session state
    label = "已恢复" if resumed else "新会话"
    body.append(f"{label} ", style="dim")
    body.append(session_id, style="italic dim")

    return Panel(
        body,
        box=box.ROUNDED,
        border_style="cyan",
        expand=False,
    )
