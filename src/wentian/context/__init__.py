"""Context management — token estimation and two-layer compaction.

v0.8 · C47 · F56（任务 T90）

This package is a leaf: it imports only the stdlib plus ``wentian.providers.base``
(for the ``Message`` / ``Usage`` types). It never imports a concrete provider,
rich, or prompt_toolkit — the compaction layer is provider-agnostic and is wired
into the agent orchestration layer by duck typing (N26).
"""
