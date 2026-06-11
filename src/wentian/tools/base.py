"""v0.3 · C8 · F19/F21（任务 T31）

Tool ABC and ToolError — the tool-layer contract.

Stdlib-only: no third-party imports (spec N6/N7).
The only cross-layer import allowed is from wentian.providers.base (ToolSpec).
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from wentian.providers.base import ToolSpec

__all__ = ["ToolError", "Tool", "spec"]


class ToolError(Exception):
    """Raised by Tool.run() on failure.

    The message is intended to be forwarded to the model as a tool-result
    error, so it should describe the failure in plain language rather than
    expose internal tracebacks.
    """


class Tool(ABC):
    """Abstract base for all executable tools.

    Subclasses declare three class attributes and implement ``run()``:

    Attributes
    ----------
    name:
        Short, unique identifier used when registering the tool and when the
        model refers to it in a tool call.  Must be a non-empty string.
    description:
        Human/model-readable description of what the tool does and when to
        use it.
    parameters:
        JSON Schema dict describing the arguments accepted by ``run()``.
        Must be a valid JSON Schema object (typically ``{"type": "object",
        "properties": {...}, "required": [...]}`).
    timeout_s:
        Maximum seconds to allow ``run()`` to execute before the caller
        should treat it as timed out.  Default: 60.0.
    requires_confirmation:
        When True the REPL should ask the user to confirm before executing
        this tool.  Default: False.
    """

    #: Short unique identifier; subclasses MUST override.
    name: str

    #: Human/model-readable description; subclasses MUST override.
    description: str

    #: JSON Schema for run() arguments; subclasses MUST override.
    parameters: dict

    #: Maximum execution time in seconds.
    timeout_s: float = 60.0

    #: Whether this tool requires explicit user confirmation before running.
    requires_confirmation: bool = False

    @abstractmethod
    def run(self, args: dict) -> str:
        """Execute the tool with the given arguments.

        Parameters
        ----------
        args:
            Parsed argument dict matching ``self.parameters`` schema.

        Returns
        -------
        str
            Result text to be forwarded to the model as a tool result.

        Raises
        ------
        ToolError
            On any failure; the message is forwarded to the model.
        """


def spec(tool: Tool) -> ToolSpec:
    """Build a protocol-neutral :class:`~wentian.providers.base.ToolSpec`
    from a :class:`Tool` instance.

    The returned ``ToolSpec`` is frozen and carries only the three fields the
    provider layer needs — ``name``, ``description``, ``parameters`` — keeping
    the tools layer decoupled from provider-specific wire formats.
    """
    return ToolSpec(
        name=tool.name,
        description=tool.description,
        parameters=tool.parameters,
    )
