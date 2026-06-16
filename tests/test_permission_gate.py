"""Tests for build_permission_gate (v0.6 · C37 · F48 — Task T77).

The assembly-layer closure that wires the pure permission pipeline + tool
registry + human-in-the-loop ask callback into the duck-typed
``permission_gate(call) -> ToolOutcome | None`` the AgentLoop expects.

Uses fake pipeline / registry / ask so the four verdict mappings, the three
Ask branches, the unregistered-tool safe default, and the read-only-never-asks
invariant can all be asserted offline.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from wentian.permissions.decision import Category, Decision, Source, Verdict
from wentian.ui.confirm import Choice


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

@dataclass
class _Call:
    id: str
    name: str
    arguments: dict | None


class _FakeTool:
    def __init__(self, category, friendly_name, *, command_arg=None, path_args=()):
        self.category = category
        self.friendly_name = friendly_name
        self.command_arg = command_arg
        self.path_args = path_args


class _FakeRegistry:
    def __init__(self, tools: dict):
        self._tools = tools

    def get(self, name):
        return self._tools.get(name)


class _FakePipeline:
    """Records the kwargs decide() was called with; returns a scripted Decision."""

    def __init__(self, decision: Decision):
        self._decision = decision
        self.calls: list[dict] = []

    def decide(self, **kwargs) -> Decision:
        self.calls.append(kwargs)
        return self._decision


def _run(coro):
    return asyncio.run(coro)


def _bash_tool():
    return _FakeTool(Category.COMMAND_EXEC, "Bash", command_arg="command")


def _write_tool():
    return _FakeTool(Category.FILE_WRITE, "Write", path_args=("path",))


def _read_tool():
    return _FakeTool(Category.READ_ONLY, "Read", path_args=("path",))


def _build(pipeline, registry, ask, mode_value="default"):
    from wentian.permission_gate import build_permission_gate
    from wentian.permissions.decision import Mode

    return build_permission_gate(
        pipeline=pipeline,
        registry=registry,
        ask=ask,
        get_mode=lambda: Mode(mode_value),
    )


# ===========================================================================
# 1. ALLOW verdict → gate returns None (pass through to executor)
# ===========================================================================

class TestAllow:
    def test_allow_returns_none(self):
        pipeline = _FakePipeline(Decision(Verdict.ALLOW, Source.RULE))
        registry = _FakeRegistry({"run_command": _bash_tool()})

        async def ask(call, decision):  # never called
            raise AssertionError("ask must not be called on ALLOW")

        gate = _build(pipeline, registry, ask)
        out = _run(gate(_Call("c1", "run_command", {"command": "ls"})))
        assert out is None
        # command extracted from arguments
        assert pipeline.calls[0]["command"] == "ls"
        assert pipeline.calls[0]["friendly"] == "Bash"
        assert pipeline.calls[0]["category"] is Category.COMMAND_EXEC


# ===========================================================================
# 2. DENY verdict (blacklist/sandbox/rule) → formed ToolOutcome, denied=False
# ===========================================================================

class TestDeny:
    def test_deny_returns_outcome_not_denied(self):
        pipeline = _FakePipeline(
            Decision(Verdict.DENY, Source.BLACKLIST, reason="危险命令")
        )
        registry = _FakeRegistry({"run_command": _bash_tool()})

        async def ask(call, decision):
            raise AssertionError("ask must not be called on DENY")

        gate = _build(pipeline, registry, ask)
        out = _run(gate(_Call("c1", "run_command", {"command": "rm -rf /"})))
        assert out is not None
        assert out.call_id == "c1"
        assert out.name == "run_command"
        assert out.is_error is True
        assert out.denied is False  # pipeline deny, not human deny
        assert "危险命令" in out.content


# ===========================================================================
# 3. ASK verdict → ask() called; three branches
# ===========================================================================

class TestAskBranches:
    def _ask_pipeline_registry(self):
        pipeline = _FakePipeline(
            Decision(Verdict.ASK, Source.MODE, reason="需要确认")
        )
        registry = _FakeRegistry({"run_command": _bash_tool()})
        return pipeline, registry

    def test_ask_allow_once_returns_none(self):
        pipeline, registry = self._ask_pipeline_registry()
        seen = []

        async def ask(call, decision):
            seen.append((call, decision))
            return Choice.ALLOW_ONCE

        gate = _build(pipeline, registry, ask)
        out = _run(gate(_Call("c1", "run_command", {"command": "ls"})))
        assert out is None
        assert len(seen) == 1

    def test_ask_deny_returns_outcome_denied_true(self):
        pipeline, registry = self._ask_pipeline_registry()

        async def ask(call, decision):
            return Choice.DENY

        gate = _build(pipeline, registry, ask)
        out = _run(gate(_Call("c1", "run_command", {"command": "ls"})))
        assert out is not None
        assert out.is_error is True
        assert out.denied is True  # human-in-the-loop refusal
        assert out.call_id == "c1"

    def test_ask_allow_always_persists_and_returns_none(self):
        pipeline, registry = self._ask_pipeline_registry()
        persisted = []

        async def ask(call, decision):
            return Choice.ALLOW_ALWAYS

        from wentian.permission_gate import build_permission_gate
        from wentian.permissions.decision import Mode

        gate = build_permission_gate(
            pipeline=pipeline,
            registry=registry,
            ask=ask,
            get_mode=lambda: Mode.DEFAULT,
            on_allow_always=lambda friendly, target, is_path: persisted.append(
                (friendly, target, is_path)
            ),
        )
        out = _run(gate(_Call("c1", "run_command", {"command": "ls -la"})))
        assert out is None
        # exact rule recorded: command string, not a path
        assert persisted == [("Bash", "ls -la", False)]


# ===========================================================================
# 4. Unregistered tool → safe default (command-exec, never silently allow)
# ===========================================================================

class TestUnregisteredSafeDefault:
    def test_unknown_tool_treated_as_command_exec(self):
        # ALLOW pipeline would normally pass, but unknown tools must still be
        # routed through the pipeline as command_exec (never silently None).
        pipeline = _FakePipeline(Decision(Verdict.ASK, Source.MODE, reason="x"))
        registry = _FakeRegistry({})  # nothing registered

        asked = []

        async def ask(call, decision):
            asked.append(call)
            return Choice.DENY

        gate = _build(pipeline, registry, ask)
        out = _run(gate(_Call("c1", "mystery", {"foo": "bar"})))
        # routed as command_exec → pipeline got command_exec category
        assert pipeline.calls[0]["category"] is Category.COMMAND_EXEC
        # ask was reached and DENY returned a denied outcome
        assert out is not None and out.denied is True

    def test_unknown_tool_never_returns_none_without_pipeline_allow(self):
        # If pipeline somehow returns ALLOW we honor it, but the point is the
        # category must be command_exec (most strict), proven above. Here we
        # assert it does NOT bypass the pipeline entirely.
        pipeline = _FakePipeline(Decision(Verdict.DENY, Source.MODE, reason="no"))
        registry = _FakeRegistry({})

        async def ask(call, decision):
            raise AssertionError("ask not called on DENY")

        gate = _build(pipeline, registry, ask)
        out = _run(gate(_Call("c1", "mystery", None)))
        assert out is not None and out.is_error is True


# ===========================================================================
# 5. Read-only tool never reaches ask
# ===========================================================================

class TestReadOnlyNeverAsks:
    def test_readonly_allow_never_asks(self):
        pipeline = _FakePipeline(Decision(Verdict.ALLOW, Source.MODE))
        registry = _FakeRegistry({"read_file": _read_tool()})

        async def ask(call, decision):
            raise AssertionError("read-only must never reach ask")

        gate = _build(pipeline, registry, ask)
        out = _run(gate(_Call("c1", "read_file", {"path": "a.txt"})))
        assert out is None
        # paths extracted for file tool
        assert pipeline.calls[0]["paths"] == ("a.txt",)
        assert pipeline.calls[0]["command"] is None


# ===========================================================================
# 6. File tool path extraction (multiple path args)
# ===========================================================================

class TestPathExtraction:
    def test_write_tool_extracts_path(self):
        pipeline = _FakePipeline(Decision(Verdict.ALLOW, Source.RULE))
        registry = _FakeRegistry({"write_file": _write_tool()})

        async def ask(call, decision):
            raise AssertionError

        gate = _build(pipeline, registry, ask)
        _run(gate(_Call("c1", "write_file", {"path": "src/x.py", "content": "h"})))
        assert pipeline.calls[0]["paths"] == ("src/x.py",)
        assert pipeline.calls[0]["friendly"] == "Write"
