"""Auto-memory package —越用越懂你 (store / extractor / runner).

v0.9 · C56/C57/C58 · F67/F68/N30/N31（任务 T103/T104/T105）

A leaf-ish package: ``store`` is stdlib + pyyaml + reuse of v0.8
``context.estimator``; ``extractor`` and ``runner`` take the provider via duck
typing (only ``stream`` is used, mirroring v0.8 ``summarizer``) and import NO
concrete provider / agent / registry. The whole package has zero reverse
dependency on the orchestration layer (N30).
"""

from __future__ import annotations

from wentian.memory.store import (
    CATEGORIES,
    MemoryConfig,
    MemoryStore,
    Note,
    default_scope,
    read_note,
)

__all__ = [
    "CATEGORIES",
    "MemoryConfig",
    "MemoryStore",
    "Note",
    "default_scope",
    "read_note",
]
