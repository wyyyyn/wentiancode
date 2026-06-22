"""v0.13 · C112 · F94/F95/F96/F100（任务 T141）— run_subagent 执行器测试。

全程**假 provider**，绝不联网。覆盖 brief #1–#7：

1. definition 端到端 COMPLETED → text（固定文本、无 tool_calls）。
2. model 覆盖：``model="haiku"`` → factory 收到 ``cfg.model="claude-haiku-4-5"``；
   ``model="inherit"`` → cfg.model 不变（取父 model）。
3. 别名不可解析 → ``SubAgentResult(stop_reason="ERROR")``，不空起 AgentLoop
   （factory 一次都没被调）。
4. fork 式继承历史：子 loop 起始消息 = ``parent_messages + [user prompt]``；
   子允许集不含 ``"Agent"``。
5. 错误停机转结构化：注入 ``max_turns=1`` + 每轮返 tool_call → MAX_ROUNDS
   结构化结果，不崩主流程。
6. 状态隔离：串行两次调用、各用独立假 provider 实例 → 结果互不干扰。

镜像 ``tests/test_skill_activator.py`` 的 RecordingProvider / loop_factory 风格。
"""

from __future__ import annotations

import threading
from typing import Iterator

from wentian.agents.runner import SubAgentResult, run_subagent
from wentian.agents.spec import AgentDef
from wentian.config import ProviderConfig
from wentian.providers.base import (
    Done,
    Message,
    Provider,
    StreamEvent,
    TextDelta,
    ToolCallEvent,
    ToolSpec,
    Usage,
)

# 与 config._default_model_aliases() 同构（哨兵 __inherit__）。
_ALIASES = {
    "haiku": "claude-haiku-4-5",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-8",
    "inherit": "__inherit__",
}


# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------


class RecordingProvider(Provider):
    """记录每次 stream() 收到的 messages / system / tools，并 yield 固定助手文本。"""

    name = "recording"

    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list[list[Message]] = []
        self.systems_seen: list[str | None] = []
        self.tools_seen: list[object] = []

    def stream(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
    ) -> Iterator[StreamEvent]:
        self.calls.append([dict(m) for m in messages])
        self.systems_seen.append(system)
        self.tools_seen.append(tools)
        yield TextDelta(self._text)
        yield Done(Usage(input_tokens=11, output_tokens=7))


class ToolLoopProvider(Provider):
    """每轮都请求一个工具（永不收束）——用于逼出 MAX_ROUNDS。"""

    name = "tool-loop"

    def __init__(self) -> None:
        self.calls: list[list[Message]] = []

    def stream(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
    ) -> Iterator[StreamEvent]:
        self.calls.append([dict(m) for m in messages])
        yield TextDelta("继续调用工具…")
        yield ToolCallEvent(id="c1", name="read_file", arguments={"path": "x"})
        yield Done(None)


class _FakeProviderFactory:
    """记录每次构造收到的 cfg，并返回预置的 provider 实例（按调用次序）。"""

    def __init__(self, *providers: Provider) -> None:
        self._providers = list(providers)
        self.cfgs: list[ProviderConfig] = []
        self.call_count = 0

    def __call__(self, cfg: ProviderConfig) -> Provider:
        self.cfgs.append(cfg)
        prov = self._providers[self.call_count]
        self.call_count += 1
        return prov


def _base_cfg(model: str = "claude-opus-4-8") -> ProviderConfig:
    return ProviderConfig(
        name="main",
        protocol="anthropic",
        model=model,
        api_key="sk-test",
    )


def _agent(
    *,
    name: str = "analyst",
    model: str = "inherit",
    tools: tuple[str, ...] | None = None,
    disallowed_tools: tuple[str, ...] = (),
    max_turns: int | None = None,
) -> AgentDef:
    # 注：派发类型（definition / fork）由 run_subagent 的 parent_messages 携带，
    # 不是 AgentDef 字段——fork 测试通过传 parent_messages 表达。
    return AgentDef(
        name=name,
        description="分析师",
        body="你是分析师，请认真分析。",
        tools=tools,
        disallowed_tools=disallowed_tools,
        model=model,
        max_turns=max_turns,
    )


# loop_factory 测试夹具：捕获 AgentLoop 装配参数，并用注入的 provider 驱动真 loop。
class _LoopSpy:
    """记录 run_subagent 传给 loop_factory 的 (provider, kwargs)。"""

    def __init__(self) -> None:
        self.provider = None
        self.kwargs: dict = {}
        self.messages_seen: list[Message] | None = None

    def __call__(self, provider, **kwargs):
        from wentian.agent.loop import AgentLoop

        self.provider = provider
        self.kwargs = kwargs
        loop = AgentLoop(provider, **kwargs)
        _spy = self

        class _Wrap:
            def run(self, messages, **rk):
                _spy.messages_seen = [dict(m) for m in messages]
                return loop.run(messages, **rk)

        return _Wrap()


# ---------------------------------------------------------------------------
# #1 definition 端到端 COMPLETED → text
# ---------------------------------------------------------------------------


def test_definition_end_to_end_completed_returns_text() -> None:
    provider = RecordingProvider("分析完毕")
    factory = _FakeProviderFactory(provider)
    agent = _agent(model="inherit")

    result = run_subagent(
        agent,
        "帮我分析",
        base_provider_cfg=_base_cfg(),
        provider_factory=factory,
        registry=None,
        executor=None,
        pipeline=None,
        settings=None,
        model_aliases=_ALIASES,
        depth=0,
    )

    assert isinstance(result, SubAgentResult)
    assert result.text == "分析完毕"
    assert result.stop_reason == "COMPLETED"
    # usage 转 dict 累计。
    assert result.usage.get("input_tokens") == 11
    assert result.usage.get("output_tokens") == 7
    # definition：起始消息 = 单条 user prompt（不带父历史）。
    seed = provider.calls[0]
    assert seed == [{"role": "user", "content": "帮我分析"}]
    # 子 system 含 AgentDef.body。
    assert provider.systems_seen[0] is not None
    assert "你是分析师" in provider.systems_seen[0]


# ---------------------------------------------------------------------------
# #2 model 覆盖
# ---------------------------------------------------------------------------


def test_model_alias_override_applied_to_cfg() -> None:
    provider = RecordingProvider("ok")
    factory = _FakeProviderFactory(provider)
    agent = _agent(model="haiku")

    run_subagent(
        agent,
        "go",
        base_provider_cfg=_base_cfg(model="claude-opus-4-8"),
        provider_factory=factory,
        registry=None,
        executor=None,
        pipeline=None,
        settings=None,
        model_aliases=_ALIASES,
    )

    # factory 收到的 cfg.model 已被替换为别名映射目标。
    assert factory.cfgs[0].model == "claude-haiku-4-5"
    # 其余字段经 dataclasses.replace 保留。
    assert factory.cfgs[0].name == "main"
    assert factory.cfgs[0].api_key == "sk-test"


def test_model_inherit_keeps_base_model() -> None:
    provider = RecordingProvider("ok")
    factory = _FakeProviderFactory(provider)
    agent = _agent(model="inherit")

    run_subagent(
        agent,
        "go",
        base_provider_cfg=_base_cfg(model="claude-opus-4-8"),
        provider_factory=factory,
        registry=None,
        executor=None,
        pipeline=None,
        settings=None,
        model_aliases=_ALIASES,
    )

    # inherit（哨兵 __inherit__）→ 父 model 不变。
    assert factory.cfgs[0].model == "claude-opus-4-8"


# ---------------------------------------------------------------------------
# #3 别名不可解析 → ERROR，不空起 AgentLoop
# ---------------------------------------------------------------------------


def test_unknown_alias_returns_error_without_spawning() -> None:
    factory = _FakeProviderFactory()  # 空：一旦被调用就 IndexError
    agent = _agent(model="unknown-alias")

    result = run_subagent(
        agent,
        "go",
        base_provider_cfg=_base_cfg(),
        provider_factory=factory,
        registry=None,
        executor=None,
        pipeline=None,
        settings=None,
        model_aliases=_ALIASES,
    )

    assert result.stop_reason == "ERROR"
    assert "unknown-alias" in result.text
    assert result.usage == {}
    # 提前返回：factory 一次都没被调（绝不空起 loop）。
    assert factory.call_count == 0


# ---------------------------------------------------------------------------
# #4 fork 式继承历史 + 允许集不含 Agent
# ---------------------------------------------------------------------------


def test_fork_inherits_parent_messages_and_excludes_agent_tool() -> None:
    provider = RecordingProvider("fork 完成")
    factory = _FakeProviderFactory(provider)
    spy = _LoopSpy()
    agent = _agent(model="inherit")  # fork 由下方 parent_messages 表达
    parent_messages: list[Message] = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好，我在。"},
    ]

    result = run_subagent(
        agent,
        "接着分析",
        base_provider_cfg=_base_cfg(),
        provider_factory=factory,
        registry=None,
        executor=None,
        pipeline=None,
        settings=None,
        model_aliases=_ALIASES,
        parent_messages=parent_messages,
        loop_factory=spy,
    )

    assert result.text == "fork 完成"
    # 子 loop 起始消息 = 父历史 + 触发 user prompt。
    assert spy.messages_seen is not None
    assert spy.messages_seen[0] == {"role": "user", "content": "你好"}
    assert spy.messages_seen[1] == {"role": "assistant", "content": "你好，我在。"}
    assert spy.messages_seen[-1] == {"role": "user", "content": "接着分析"}
    # 允许集（传给 AgentLoop 的 allowed_tools）绝不含 "Agent"（嵌套防护）。
    allowed = spy.kwargs.get("allowed_tools")
    assert allowed is not None
    assert "Agent" not in allowed

    # 父历史绝不被原地改动（隔离）。
    assert parent_messages == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好，我在。"},
    ]


def test_allowed_tools_strips_globally_forbidden_agent() -> None:
    """即便角色白名单显式列出 Agent，全局禁也会剥掉它。"""
    provider = RecordingProvider("ok")
    factory = _FakeProviderFactory(provider)
    spy = _LoopSpy()
    agent = _agent(tools=("read_file", "Agent", "search_text"))

    run_subagent(
        agent,
        "go",
        base_provider_cfg=_base_cfg(),
        provider_factory=factory,
        registry=_FakeRegistry({"read_file", "Agent", "search_text", "shell"}),
        executor=None,
        pipeline=None,
        settings=None,
        model_aliases=_ALIASES,
        loop_factory=spy,
    )

    allowed = spy.kwargs.get("allowed_tools")
    assert "Agent" not in allowed
    assert "read_file" in allowed
    assert "search_text" in allowed


class _FakeRegistry:
    """鸭子型 registry：提供 names()（all_tools 来源）与 get()。"""

    def __init__(self, names: set[str]) -> None:
        self._names = names

    def names(self) -> list[str]:
        return sorted(self._names)

    def get(self, name: str):
        return None


# ---------------------------------------------------------------------------
# #5 错误停机转结构化（MAX_ROUNDS）
# ---------------------------------------------------------------------------


def test_max_rounds_softened_to_structured_result() -> None:
    provider = ToolLoopProvider()
    factory = _FakeProviderFactory(provider)
    # max_turns=1 + 每轮返 tool_call → 首轮即 MAX_ROUNDS 刹车。
    agent = _agent(max_turns=1)

    result = run_subagent(
        agent,
        "go",
        base_provider_cfg=_base_cfg(),
        provider_factory=factory,
        registry=None,
        executor=None,
        pipeline=None,
        settings=None,
        model_aliases=_ALIASES,
    )

    assert result.stop_reason == "MAX_ROUNDS"
    assert "MAX_ROUNDS" in result.text
    # 没有异常逃逸——拿到的是结构化结果。
    assert isinstance(result, SubAgentResult)


# ---------------------------------------------------------------------------
# #6 状态隔离：串行两次，各用独立 provider 实例
# ---------------------------------------------------------------------------


def test_two_serial_calls_are_isolated() -> None:
    p1 = RecordingProvider("结果一")
    p2 = RecordingProvider("结果二")
    f1 = _FakeProviderFactory(p1)
    f2 = _FakeProviderFactory(p2)

    r1 = run_subagent(
        _agent(name="a1"),
        "任务一",
        base_provider_cfg=_base_cfg(),
        provider_factory=f1,
        registry=None,
        executor=None,
        pipeline=None,
        settings=None,
        model_aliases=_ALIASES,
    )
    r2 = run_subagent(
        _agent(name="a2"),
        "任务二",
        base_provider_cfg=_base_cfg(),
        provider_factory=f2,
        registry=None,
        executor=None,
        pipeline=None,
        settings=None,
        model_aliases=_ALIASES,
    )

    assert r1.text == "结果一"
    assert r2.text == "结果二"
    # 各自只调了自己的 provider（无共享）。
    assert len(p1.calls) >= 1
    assert len(p2.calls) >= 1
    # p1 没看到 p2 的 prompt，反之亦然。
    assert p1.calls[0][-1]["content"] == "任务一"
    assert p2.calls[0][-1]["content"] == "任务二"


# ---------------------------------------------------------------------------
# 深度防护 + worker 线程干净收束（额外加固，覆盖 N53/depth guard）
# ---------------------------------------------------------------------------


def test_depth_guard_refuses_to_spawn() -> None:
    factory = _FakeProviderFactory()  # 空：被调用即 IndexError
    agent = _agent()

    result = run_subagent(
        agent,
        "go",
        base_provider_cfg=_base_cfg(),
        provider_factory=factory,
        registry=None,
        executor=None,
        pipeline=None,
        settings=None,
        model_aliases=_ALIASES,
        depth=1,
    )

    assert result.stop_reason == "ERROR"
    assert factory.call_count == 0


def test_worker_thread_joined_no_leak() -> None:
    provider = RecordingProvider("ok")
    factory = _FakeProviderFactory(provider)
    baseline = threading.active_count()

    run_subagent(
        _agent(),
        "go",
        base_provider_cfg=_base_cfg(),
        provider_factory=factory,
        registry=None,
        executor=None,
        pipeline=None,
        settings=None,
        model_aliases=_ALIASES,
    )

    # 同步返回时 worker 已 join：active_count 回到基线。
    assert threading.active_count() == baseline


# ---------------------------------------------------------------------------
# FixA: Critical #1 — worker thread must be daemon
# ---------------------------------------------------------------------------


def test_worker_thread_is_daemon() -> None:
    """Worker thread must be created with daemon=True (Critical #1).

    A non-daemon thread orphaned by a tool-timeout would delay process exit.
    We capture the Thread constructor call via monkeypatching and look for the
    subagent-named worker thread specifically (AgentLoop also spawns its own
    internal StreamBridge thread, so there may be more than one Thread created).
    """
    import wentian.agents.runner as _runner_mod

    captured: list[dict] = []
    _orig_Thread = threading.Thread

    class _RecordingThread(threading.Thread):
        def __init__(self, *args, **kwargs):
            captured.append(
                {"daemon": kwargs.get("daemon"), "name": kwargs.get("name", "")}
            )
            super().__init__(*args, **kwargs)

    provider = RecordingProvider("ok")
    factory = _FakeProviderFactory(provider)

    # Patch threading.Thread in the runner module's namespace only.
    _runner_mod.threading.Thread = _RecordingThread  # type: ignore[attr-defined]
    try:
        run_subagent(
            _agent(name="analyst"),
            "go",
            base_provider_cfg=_base_cfg(),
            provider_factory=factory,
            registry=None,
            executor=None,
            pipeline=None,
            settings=None,
            model_aliases=_ALIASES,
        )
    finally:
        _runner_mod.threading.Thread = _orig_Thread  # type: ignore[attr-defined]

    # The subagent worker thread is named "subagent-<name>" and must be daemon=True.
    worker_threads = [t for t in captured if t["name"].startswith("subagent-")]
    assert len(worker_threads) == 1, (
        f"Expected exactly one 'subagent-*' thread, got: {captured}"
    )
    assert worker_threads[0]["daemon"] is True, (
        "Worker thread must be created with daemon=True to prevent orphaned "
        "threads from delaying process exit on tool-timeout abandonment."
    )


# ---------------------------------------------------------------------------
# FixA: Important #4 — F97 layer-3 (background filter) wired through
# ---------------------------------------------------------------------------


class _FakeRegistryWithCategories:
    """Fake registry returning Tool-like objects with a .category attribute."""

    def __init__(self, tool_categories: dict) -> None:
        self._tool_categories = tool_categories

    def names(self) -> list[str]:
        return list(self._tool_categories.keys())

    def get(self, name: str):
        cat = self._tool_categories.get(name)
        if cat is None:
            return None

        # Return a duck-typed object with .category
        class _FakeTool:
            category = cat

        return _FakeTool()


def test_background_true_filters_non_readonly_tools() -> None:
    """background=True must apply F97 layer-3: non-READ_ONLY tools are stripped.

    The background safety filter keeps a tool only if:
    - it is in background_allow, OR
    - its category is Category.READ_ONLY.
    """
    from wentian.permissions.decision import Category

    registry = _FakeRegistryWithCategories(
        {
            "read_file": Category.READ_ONLY,
            "write_file": Category.FILE_WRITE,
        }
    )
    spy = _LoopSpy()
    provider = RecordingProvider("ok")
    factory = _FakeProviderFactory(provider)
    # tools=None → no role whitelist (all tools pass layers 1+2)
    agent = _agent(model="inherit", tools=None)

    run_subagent(
        agent,
        "go",
        base_provider_cfg=_base_cfg(),
        provider_factory=factory,
        registry=registry,
        executor=None,
        pipeline=None,
        settings=None,
        model_aliases=_ALIASES,
        background=True,
        loop_factory=spy,
    )

    allowed = spy.kwargs.get("allowed_tools")
    assert allowed is not None
    assert "read_file" in allowed, "READ_ONLY tool must survive background filter"
    assert "write_file" not in allowed, (
        "FILE_WRITE tool must be stripped by background filter"
    )


def test_background_allow_exempts_non_readonly_tool() -> None:
    """background_allow=('write_file',) keeps write_file even when background=True."""
    from wentian.permissions.decision import Category

    registry = _FakeRegistryWithCategories(
        {
            "read_file": Category.READ_ONLY,
            "write_file": Category.FILE_WRITE,
        }
    )
    spy = _LoopSpy()
    provider = RecordingProvider("ok")
    factory = _FakeProviderFactory(provider)
    agent = _agent(model="inherit", tools=None)

    run_subagent(
        agent,
        "go",
        base_provider_cfg=_base_cfg(),
        provider_factory=factory,
        registry=registry,
        executor=None,
        pipeline=None,
        settings=None,
        model_aliases=_ALIASES,
        background=True,
        background_allow=("write_file",),
        loop_factory=spy,
    )

    allowed = spy.kwargs.get("allowed_tools")
    assert allowed is not None
    assert "read_file" in allowed
    assert "write_file" in allowed, (
        "background_allow must exempt write_file from the background filter"
    )


def test_background_false_keeps_non_readonly_tools() -> None:
    """background=False (default) must NOT apply layer-3; non-READ_ONLY tools stay."""
    from wentian.permissions.decision import Category

    registry = _FakeRegistryWithCategories(
        {
            "read_file": Category.READ_ONLY,
            "write_file": Category.FILE_WRITE,
        }
    )
    spy = _LoopSpy()
    provider = RecordingProvider("ok")
    factory = _FakeProviderFactory(provider)
    agent = _agent(model="inherit", tools=None)

    run_subagent(
        agent,
        "go",
        base_provider_cfg=_base_cfg(),
        provider_factory=factory,
        registry=registry,
        executor=None,
        pipeline=None,
        settings=None,
        model_aliases=_ALIASES,
        # background defaults to False — do NOT pass it
        loop_factory=spy,
    )

    allowed = spy.kwargs.get("allowed_tools")
    assert allowed is not None
    assert "read_file" in allowed
    assert "write_file" in allowed, (
        "background=False must not filter non-READ_ONLY tools (layers 1+2 only)"
    )
