"""v0.3 · C8 · F19/F21 (task T31)
v0.6 · C35 · F43/F45 (task T75) — Tool gains category / friendly_name /
parameter-extraction declarations; requires_confirmation becomes a derived
property of category.

Tool ABC and ToolError — the tool-layer contract.

Stdlib-only: no third-party imports (spec N6/N7).
Cross-layer imports allowed: wentian.providers.base (ToolSpec) and
wentian.permissions.decision (Category — pure data enum, no behaviour).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from wentian.permissions.decision import Category
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
    category:
        Coarse security classification (read-only / file-write / command-exec);
        subclasses MUST override. Drives which permission layers apply and the
        derived ``requires_confirmation``.
    friendly_name:
        User-facing name used by the permission rule engine (e.g. Read / Write /
        Edit / Glob / Grep / Bash); subclasses MUST override.
    command_arg:
        Name of the argument carrying the command string (command-exec tools),
        or None. Used by the permission pipeline to extract the match target.
    path_args:
        Names of arguments carrying file paths (file tools). Used by the
        permission pipeline to extract the project-relative match target.
    requires_confirmation:
        Derived property — True for any non-read-only tool. Kept for backward
        compatibility with ``agent.batch.classify`` (v0.6 · C35 · F43/F45).
    """

    #: Short unique identifier; subclasses MUST override.
    name: str

    #: Human/model-readable description; subclasses MUST override.
    description: str

    #: JSON Schema for run() arguments; subclasses MUST override.
    parameters: dict

    #: Maximum execution time in seconds.
    timeout_s: float = 60.0

    # v0.6 · C35 · F43/F45 (task T75) — security metadata for the permission layer.

    #: Coarse security classification; subclasses MUST override.
    category: Category

    #: User-facing name for permission rules; subclasses MUST override.
    friendly_name: str

    #: Argument name carrying the command string (command-exec tools), else None.
    command_arg: str | None = None

    #: Argument names carrying file paths (file tools).
    path_args: tuple[str, ...] = ()

    @property
    def requires_confirmation(self) -> bool:
        """Derived: any non-read-only tool needs confirmation.

        Kept so ``agent.batch.classify`` (which reads this flag via duck typing)
        behaves exactly as before — read-only ⇒ False, everything else ⇒ True.
        """
        return self.category != Category.READ_ONLY

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
