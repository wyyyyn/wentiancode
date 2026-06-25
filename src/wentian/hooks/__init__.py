"""v0.12 · hooks package public interface.

Exports only public symbols from spec.py; does not import any other hooks submodules
(conditions / config / actions / engine are implemented independently in subsequent waves).
"""

from wentian.hooks.spec import (
    INTERCEPT_EVENTS,
    Action,
    Clause,
    Condition,
    HookEvent,
    HookRule,
    HttpAction,
    Match,
    PromptAction,
    ShellAction,
    SubAgentAction,
)

__all__ = [
    "HookEvent",
    "INTERCEPT_EVENTS",
    "Match",
    "Clause",
    "Condition",
    "ShellAction",
    "PromptAction",
    "HttpAction",
    "SubAgentAction",
    "Action",
    "HookRule",
]
