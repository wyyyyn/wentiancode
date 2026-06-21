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


# ---------------------------------------------------------------------------
# v0.10 commands/ 包 — 分层 import 断言（AC94/N36）
#
# commands/{spec,parser,registry,context}.py 为叶子层；
# commands/builtins.py 为次叶子（可引 spec/registry + permissions.decision）。
# 所有 commands/ 模块均不得反向依赖 ui 层，也不得引入渲染/编排/provider/repl 依赖。
# ---------------------------------------------------------------------------

_FORBIDDEN_FOR_COMMANDS_LEAF = (
    "rich",
    "prompt_toolkit",
    "wentian.providers",
    "wentian.repl",
    "wentian.agent",
    "wentian.ui",
)


def test_commands_spec_no_render_or_orchestration_imports():
    """commands/spec.py 为纯叶子：禁止 import 渲染/编排/provider/repl/ui 层。

    AC94/N36
    """
    _assert_none_imported("commands/spec.py", _FORBIDDEN_FOR_COMMANDS_LEAF)


def test_commands_parser_no_render_or_orchestration_imports():
    """commands/parser.py 为纯叶子（仅 stdlib dataclasses）：禁止 import 渲染/编排/provider/repl/ui 层。

    AC94/N36
    """
    _assert_none_imported("commands/parser.py", _FORBIDDEN_FOR_COMMANDS_LEAF)


def test_commands_registry_no_render_or_orchestration_imports():
    """commands/registry.py 为叶子（仅引 commands.spec）：禁止 import 渲染/编排/provider/repl/ui 层。

    AC94/N36
    """
    _assert_none_imported("commands/registry.py", _FORBIDDEN_FOR_COMMANDS_LEAF)


def test_commands_context_no_render_or_orchestration_imports():
    """commands/context.py 为叶子（Protocol 定义）：禁止 import 渲染/编排/provider/repl/ui 层。

    TYPE_CHECKING 下引用 wentian.permissions.decision.Mode 和
    wentian.commands.spec.CommandSpec，均不在 forbidden 列表内。

    AC94/N36
    """
    _assert_none_imported("commands/context.py", _FORBIDDEN_FOR_COMMANDS_LEAF)


def test_commands_builtins_no_render_or_orchestration_imports():
    """commands/builtins.py 为次叶子：禁止 import 渲染/prompt_toolkit/repl/provider/agent/ui 层。

    允许 import wentian.commands.* 与 wentian.permissions.decision。

    AC94/N36
    """
    _assert_none_imported(
        "commands/builtins.py",
        (
            "rich",
            "prompt_toolkit",
            "wentian.repl",
            "wentian.providers",
            "wentian.agent",
            "wentian.ui",
        ),
    )


def test_commands_no_ui_reverse_dependency():
    """commands/ 包各模块均不得反向依赖 ui 层（防止循环/越界依赖）。

    此项覆盖「commands 不反向依赖补全器（ui/completion.py）」的合规性验证。

    AC94/N36
    """
    for module_rel in (
        "commands/spec.py",
        "commands/parser.py",
        "commands/registry.py",
        "commands/context.py",
        "commands/builtins.py",
    ):
        _assert_none_imported(module_rel, ("wentian.ui",))


# ---------------------------------------------------------------------------
# v0.11 skills/ 包 — 分层 import 断言（AC115/N46）
#
# skills/{base,registry,loader}.py 为纯数据 + 加载叶子：零 rich/prompt_toolkit/
# 后端 SDK/agent/repl/commands/ui 反向依赖（loader 仅 stdlib）。
# tools/skill_tool.py 住 tools 层：经鸭子 activator 操作，不 import repl/agent/skills 具体类。
# ---------------------------------------------------------------------------

_FORBIDDEN_FOR_SKILLS = (
    "rich",
    "prompt_toolkit",
    "wentian.providers",
    "wentian.agent",
    "wentian.repl",
    "wentian.cli",
    "wentian.commands",
    "wentian.ui",
)


def test_skills_base_is_leaf():
    """skills/base.py 为纯叶子：只 stdlib，零 wentian import（AC115/N46）。"""
    imported = _imported_modules(_SRC / "skills" / "base.py")
    wentian_imports = {n for n in imported if n.startswith("wentian")}
    assert wentian_imports == set(), (
        f"skills/base.py must import no wentian modules, got {wentian_imports}"
    )


def test_skills_registry_is_leaf():
    """skills/registry.py 叶子：仅引 skills.base，禁渲染/编排/provider/repl/commands/ui。"""
    _assert_none_imported("skills/registry.py", _FORBIDDEN_FOR_SKILLS)
    imported = _imported_modules(_SRC / "skills" / "registry.py")
    non_self = {
        n
        for n in imported
        if n.startswith("wentian") and not n.startswith("wentian.skills")
    }
    assert non_self == set(), (
        f"skills/registry.py 只应引 wentian.skills.*，got {non_self}"
    )


def test_skills_loader_no_orchestration_imports():
    """skills/loader.py 为加载叶子：仅 stdlib + skills.base/registry，禁反向依赖（AC115/N46）。"""
    _assert_none_imported("skills/loader.py", _FORBIDDEN_FOR_SKILLS)
    imported = _imported_modules(_SRC / "skills" / "loader.py")
    # v0.13 · C109：frontmatter 解析抽成共享 stdlib 叶子 wentian.frontmatter，
    # skills/loader 与 agents/loader 同享之——它是纯叶子、非编排/反向依赖，故放行。
    non_self = {
        n
        for n in imported
        if n.startswith("wentian")
        and not n.startswith("wentian.skills")
        and n != "wentian.frontmatter"
    }
    assert non_self == set(), (
        f"skills/loader.py 只应引 wentian.skills.* 与共享叶子 wentian.frontmatter，got {non_self}"
    )


def test_skill_tool_no_repl_or_agent_or_skills_imports():
    """tools/skill_tool.py 经鸭子 activator 操作，不 import repl/agent/skills 具体类（AC115/N46）。"""
    _assert_none_imported(
        "tools/skill_tool.py",
        (
            "wentian.repl",
            "wentian.agent",
            "wentian.skills",
            "wentian.cli",
            "rich",
            "prompt_toolkit",
        ),
    )


# ---------------------------------------------------------------------------
# v0.12 · C93–C98 · N41 — hooks package is a pure, zero-reverse-dependency pkg;
# textmatch.py is a stdlib-only leaf; no config<->hooks import cycle.
# ---------------------------------------------------------------------------

# Forbidden for the hooks engine/actions: zero reverse-dependency on the
# orchestration / provider / tools / UI-framework layers (mirrors permissions/
# memory pure-package rules). hooks gets event context as a plain dict injected
# by the assembly layer.
_FORBIDDEN_FOR_HOOKS = (
    "wentian.agent",
    "wentian.repl",
    "wentian.cli",
    "wentian.providers",
    "wentian.tools",
    "rich",
    "prompt_toolkit",
    "anthropic",
    "openai",
)


def test_textmatch_is_stdlib_only():
    """textmatch.py is a top-level leaf — imports no wentian modules at all."""
    imported = _imported_modules(_SRC / "textmatch.py")
    wentian_imports = {n for n in imported if n.startswith("wentian")}
    assert wentian_imports == set(), (
        f"textmatch.py must import no wentian modules, got {wentian_imports}"
    )


def test_hooks_engine_no_orchestration_imports():
    _assert_none_imported("hooks/engine.py", _FORBIDDEN_FOR_HOOKS)


def test_hooks_actions_no_orchestration_imports():
    _assert_none_imported("hooks/actions.py", _FORBIDDEN_FOR_HOOKS)


def test_hooks_pkg_no_rich_ptk_or_sdk():
    """Every hooks/*.py module is free of rich / prompt_toolkit / backend SDK."""
    for module in ("spec.py", "conditions.py", "config.py", "actions.py", "engine.py"):
        _assert_none_imported(
            f"hooks/{module}",
            ("rich", "prompt_toolkit", "anthropic", "openai"),
        )


def test_no_config_hooks_import_cycle():
    """hooks/* must NOT import wentian.config (config→hooks is one-way, acyclic)."""
    for module in ("spec.py", "conditions.py", "config.py", "actions.py", "engine.py"):
        imported = _imported_modules(_SRC / "hooks" / module)
        assert "wentian.config" not in imported, (
            f"hooks/{module} imports wentian.config — would create a cycle "
            "(config.py imports hooks.config; the reverse is forbidden)"
        )
