"""AC83 (N30/N31) — module import-boundary assertions, automated via ``ast``.

v0.9 review fix — #7

The v0.9 memory / instructions / session modules are **leaf-ish**: they must not
reverse-depend on the orchestration layer (agent / loop / concrete providers /
the Compactor / the assembled system-prompt builder). The contract type modules
(``providers.base`` carrying ``Message`` / ``TextDelta``, ``config`` carrying the
config dataclasses) are the allowed leaf dependencies — exactly mirroring how
``context.*`` is allowed to import ``providers.base`` only.

These tests parse each module's source with :mod:`ast` and assert no forbidden
``import`` / ``from ... import`` statement is present (string-grep would be fooled
by comments / docstrings; ast looks only at real import nodes).
"""

from __future__ import annotations

import ast
from pathlib import Path

import wentian

_SRC = Path(wentian.__file__).parent


def _imported_modules(module_path: Path) -> set[str]:
    """Return every fully-qualified module name imported by *module_path*.

    Covers both ``import a.b`` and ``from a.b import c`` (the ``a.b`` part).
    Local/relative imports inside function bodies are included too (ast walks
    the whole tree), so an assembly-only deferred import would still be caught.
    """
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None and node.level == 0:
                names.add(node.module)
    return names


def _assert_none_imported(module_rel: str, forbidden: tuple[str, ...]) -> None:
    imported = _imported_modules(_SRC / module_rel)
    offenders = sorted(
        name
        for name in imported
        for bad in forbidden
        if name == bad or name.startswith(bad + ".")
    )
    assert not offenders, f"{module_rel} must not import {offenders}"


# ---------------------------------------------------------------------------
# prompt/instructions.py — pure stdlib leaf (no provider/agent/registry/system)
# ---------------------------------------------------------------------------


def test_instructions_is_stdlib_only():
    imported = _imported_modules(_SRC / "prompt" / "instructions.py")
    wentian_imports = {n for n in imported if n.startswith("wentian")}
    assert wentian_imports == set(), (
        f"instructions.py must import no wentian modules, got {wentian_imports}"
    )


def test_instructions_no_orchestration_imports():
    _assert_none_imported(
        "prompt/instructions.py",
        (
            "wentian.providers",
            "wentian.agent",
            "wentian.tools",
            "wentian.prompt.system",
            "wentian.permissions",
            "wentian.context",
        ),
    )


# ---------------------------------------------------------------------------
# memory package — no agent / loop / concrete provider / Compactor
# ---------------------------------------------------------------------------

_FORBIDDEN_FOR_MEMORY = (
    "wentian.agent",
    "wentian.providers.anthropic",
    "wentian.providers.openai_compat",
    "wentian.providers.factory",
    "wentian.context.compactor",
    "wentian.repl",
    "wentian.cli",
    "wentian.tools",
)


def test_memory_store_no_orchestration_imports():
    _assert_none_imported("memory/store.py", _FORBIDDEN_FOR_MEMORY)


def test_memory_extractor_no_orchestration_imports():
    _assert_none_imported("memory/extractor.py", _FORBIDDEN_FOR_MEMORY)


def test_memory_runner_no_orchestration_imports():
    _assert_none_imported("memory/runner.py", _FORBIDDEN_FOR_MEMORY)


def test_memory_store_imports_no_concrete_provider_at_all():
    """store.py needs neither provider nor estimator — it is a pure note/IO leaf."""
    imported = _imported_modules(_SRC / "memory" / "store.py")
    assert not any(n.startswith("wentian.providers") for n in imported)
    assert "wentian.context.estimator" not in imported


# ---------------------------------------------------------------------------
# session.py — no concrete provider, no Compactor
# ---------------------------------------------------------------------------


def test_session_no_provider_or_compactor_imports():
    _assert_none_imported(
        "session.py",
        (
            "wentian.providers.anthropic",
            "wentian.providers.openai_compat",
            "wentian.providers.factory",
            "wentian.context",
            "wentian.agent",
            "wentian.repl",
            "wentian.cli",
        ),
    )
