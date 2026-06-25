"""v0.3 · C8 · F19/F21 (task T31)

Tools sub-package for WentianCode.

Convention: this __init__.py intentionally exports NOTHING.
All consumers must import directly from the submodules:

    from wentian.tools.base import Tool, ToolError, spec
    from wentian.tools.registry import ToolRegistry

This keeps the package boundary explicit and avoids coupling parallel
development tasks (T32, T33, …) to a shared re-export surface that would
require co-ordinated edits.
"""
