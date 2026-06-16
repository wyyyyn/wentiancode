"""v0.3 · C8 · F19/F21（任务 T31）

ToolRegistry — an ordered, name-keyed store of Tool instances.

Stdlib-only: no third-party imports (spec N6/N7).
"""

from __future__ import annotations

from wentian.providers.base import ToolSpec
from wentian.tools.base import Tool, spec

__all__ = ["ToolRegistry"]


class ToolRegistry:
    """An ordered, name-keyed registry of :class:`~wentian.tools.base.Tool`
    instances.

    Tools are stored in registration order so that ``specs()`` and ``names()``
    return results in a deterministic, stable sequence — important when building
    the tool list sent to the model.

    Thread-safety: not required for the v0.3 CLI use-case (single-threaded
    construction at startup, read-only afterwards).
    """

    def __init__(self) -> None:
        # OrderedDict-style semantics via plain dict (Python 3.7+ insertion order).
        self._tools: dict[str, Tool] = {}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def register(self, tool: Tool) -> None:
        """Add *tool* to the registry.

        Parameters
        ----------
        tool:
            A fully-initialised :class:`Tool` instance.

        Raises
        ------
        ValueError
            If a tool with ``tool.name`` is already registered.
        """
        if tool.name in self._tools:
            raise ValueError(
                f"A tool named {tool.name!r} is already registered. "
                "Each tool name must be unique within a registry."
            )
        self._tools[tool.name] = tool

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, name: str) -> Tool | None:
        """Return the registered :class:`Tool` with *name*, or ``None``."""
        return self._tools.get(name)

    def specs(self) -> list[ToolSpec]:
        """Return a :class:`~wentian.providers.base.ToolSpec` for every
        registered tool, in registration order."""
        return [spec(tool) for tool in self._tools.values()]

    def names(self) -> list[str]:
        """Return the names of all registered tools, in registration order."""
        return list(self._tools.keys())
