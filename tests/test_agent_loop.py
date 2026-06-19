"""Tests for agent/loop.py: AgentLoop ReAct 主路径与停机条件（v0.4 · C17 · F29 · T52/T53）."""

from __future__ import annotations

import threading
from typing import Iterator

from conftest import FakeListener, ScriptedProvider, run_to_list

from wentian.agent.events import (
    AgentDone,
    RoundEnd,
    RoundStart,
    StopReason,
    StreamEnd,
    ToolCallStarted,
    ToolResultReady,
    UsageUpdate,
)
from wentian.agent.loop import AgentLoop
from wentian.providers.base import (
    Done,
    Message,
    Provider,
    StreamEvent,
    TextDelta,
    ToolCallEvent,
    Usage,
)


# --- 测试替身（agent 层鸭子类型契约，复用 test_agent_batch.py 形态） ---


class FakeTool:
    """带 requires_confirmation 属性的假工具。"""

    def __init__(self, requires_confirmation: bool):
        self.requires_confirmation = requires_confirmation


class FakeRegistry:
    """get 按 dict 查的假注册表。"""

    def __init__(self, tools: dict):
        self._tools = tools

    def get(self, name):
        return self._tools.get(name)


class _Outcome:
    """ToolOutcome 鸭子类型最小替身。"""

    def __init__(self, *, call_id, name, content, is_error, denied=False):
        self.call_id = call_id
        self.name = name
        self.content = content
        self.is_error = is_error
        self.denied = denied


class FakeExecutor:
    """记录 execute() 调用并返回 outcome（永不抛异常，v0.3 契约）。

    *error_names* 中的工具名返回错误 outcome（贴近真实 executor 对未知
    工具的应答），其余返回成功 outcome。
    """

    def __init__(self, error_names: frozenset[str] | set[str] = frozenset()) -> None:
        self.calls: list[tuple[str, str, object]] = []
        self._error_names = set(error_names)

    def execute(self, call_id: str, name: str, arguments):
        self.calls.append((call_id, name, arguments))
        if name in self._error_names:
            return _Outcome(
                call_id=call_id,
                name=name,
                content=f"未知工具: {name}",
                is_error=True,
            )
        return _Outcome(
            call_id=call_id,
            name=name,
            content=f"ran {name}",
            is_error=False,
        )


class ScriptThenBlockProvider(Provider):
    """T53 中断用例：前几轮按脚本走；之后的调用 yield 部分事件后永久阻塞
    （模拟挂死的网络读，镜像 conftest.BlockingFakeProvider 的泵线程泄漏
    模式——daemon 线程随进程退出回收）。"""

    name = "script-then-block"

    def __init__(
        self,
        scripts: list[list[StreamEvent]],
        final_partial: list[StreamEvent],
    ) -> None:
        self._scripts = scripts
        self._final_partial = final_partial
        self._block = threading.Event()  # never set
        self.call_count = 0

    def stream(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools=None,
    ) -> Iterator[StreamEvent]:
        idx = self.call_count
        self.call_count += 1
        if idx < len(self._scripts):
            yield from self._scripts[idx]
            return
        yield from self._final_partial
        self._block.wait()  # 永久阻塞——泵线程悬挂（daemon）


class ErrorScriptProvider(Provider):
    """T53 流异常用例：脚本项为异常实例时在流中原地抛出。"""

    name = "error-script"

    def __init__(self, scripts: list[list[object]]) -> None:
        self._scripts = scripts
        self.call_count = 0

    def stream(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools=None,
    ) -> Iterator[StreamEvent]:
        idx = self.call_count
        self.call_count += 1
        for item in self._scripts[idx]:
            if isinstance(item, BaseException):
                raise item
            yield item


READ = FakeTool(requires_confirmation=False)
WRITE = FakeTool(requires_confirmation=True)

#: 文法类事件（实时增量 TextDelta/ThinkingDelta 与 UsageUpdate 除外）。
_GRAMMAR_TYPES = (
    RoundStart,
    StreamEnd,
    ToolCallStarted,
    ToolResultReady,
    RoundEnd,
    AgentDone,
)


def _three_round_setup():
    """T52 标准场景：text+2 tool_calls → text+1 tool_call → 纯文本。"""
    raw = [{"type": "text", "text": "先读两个文件"}]
    provider = ScriptedProvider(
        [
            [
                TextDelta("先读两个文件"),
                ToolCallEvent(id="c1", name="read_file", arguments={"path": "a.txt"}),
                ToolCallEvent(id="c2", name="read_file", arguments={"path": "b.txt"}),
                Done(usage=Usage(10, 5), raw_content=raw),
            ],
            [
                TextDelta("再读一个"),
                ToolCallEvent(id="c3", name="read_file", arguments={"path": "c.txt"}),
                Done(usage=Usage(20, 7)),
            ],
            [TextDelta("完成"), Done(usage=Usage(5, 2))],
        ]
    )
    executor = FakeExecutor()
    registry = FakeRegistry({"read_file": READ})
    loop = AgentLoop(provider, registry=registry, executor=executor)
    return provider, executor, loop, raw


class TestEventGrammar:
    def test_three_round_event_sequence(self):
        """三轮场景的事件序列严格符合文法，AgentDone(COMPLETED, rounds=3)。"""
        _, _, loop, _ = _three_round_setup()
        messages = [{"role": "user", "content": "帮我读文件"}]

        events = run_to_list(loop.run(messages))

        grammar = [ev for ev in events if isinstance(ev, _GRAMMAR_TYPES)]
        assert [type(ev) for ev in grammar] == [
            RoundStart,
            StreamEnd,  # 轮 1 流阶段
            ToolCallStarted,
            ToolResultReady,  # c1
            ToolCallStarted,
            ToolResultReady,  # c2
            RoundEnd,
            RoundStart,
            StreamEnd,  # 轮 2
            ToolCallStarted,
            ToolResultReady,  # c3
            RoundEnd,
            RoundStart,
            StreamEnd,  # 轮 3（纯文本）
            AgentDone,
        ]
        assert grammar[0] == RoundStart(1)
        assert grammar[1] == StreamEnd(1, "先读两个文件", False)
        assert grammar[6] == RoundEnd(1, tool_results=2)
        assert grammar[7] == RoundStart(2)
        assert grammar[8] == StreamEnd(2, "再读一个", False)
        assert grammar[11] == RoundEnd(2, tool_results=1)
        assert grammar[12] == RoundStart(3)
        assert grammar[13] == StreamEnd(3, "完成", False)
        done = grammar[14]
        assert done.stop_reason is StopReason.COMPLETED
        assert done.text == "完成"
        assert done.rounds == 3
        # AgentDone 永远是事件流最后一个事件。
        assert events[-1] is done

    def test_tool_started_carries_call_and_result_carries_outcome(self):
        """ToolCallStarted 携带原 call，ToolResultReady 携带 executor 的 outcome。"""
        _, _, loop, _ = _three_round_setup()
        events = run_to_list(loop.run([{"role": "user", "content": "读"}]))

        started = [ev for ev in events if isinstance(ev, ToolCallStarted)]
        results = [ev for ev in events if isinstance(ev, ToolResultReady)]
        assert [ev.call.id for ev in started] == ["c1", "c2", "c3"]
        assert [ev.outcome.call_id for ev in results] == ["c1", "c2", "c3"]
        assert all(ev.outcome.content == "ran read_file" for ev in results)

    def test_usage_accumulates_across_rounds(self):
        """每轮有 usage 时发 UsageUpdate（含跨轮累计），AgentDone.usage 为总量。"""
        _, _, loop, _ = _three_round_setup()
        events = run_to_list(loop.run([{"role": "user", "content": "读"}]))

        updates = [ev for ev in events if isinstance(ev, UsageUpdate)]
        assert [u.round_usage for u in updates] == [
            Usage(10, 5),
            Usage(20, 7),
            Usage(5, 2),
        ]
        assert updates[-1].total == Usage(35, 14)
        assert events[-1].usage == Usage(35, 14)


class TestMessagesShape:
    def test_history_shape_and_order(self):
        """入史形状：user / assistant(text+tool_calls+raw_content) / tool×2 /
        assistant(+tool_calls) / tool / assistant(纯文本)；tool 按原序对应 id。"""
        provider, executor, loop, raw = _three_round_setup()
        messages = [{"role": "user", "content": "帮我读文件"}]

        run_to_list(loop.run(messages))

        assert messages == [
            {"role": "user", "content": "帮我读文件"},
            {
                "role": "assistant",
                "content": "先读两个文件",
                "tool_calls": [
                    {"id": "c1", "name": "read_file", "arguments": {"path": "a.txt"}},
                    {"id": "c2", "name": "read_file", "arguments": {"path": "b.txt"}},
                ],
                "raw_content": raw,
            },
            {
                "role": "tool",
                "tool_call_id": "c1",
                "content": "ran read_file",
                "is_error": False,
            },
            {
                "role": "tool",
                "tool_call_id": "c2",
                "content": "ran read_file",
                "is_error": False,
            },
            {
                "role": "assistant",
                "content": "再读一个",
                "tool_calls": [
                    {"id": "c3", "name": "read_file", "arguments": {"path": "c.txt"}},
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "c3",
                "content": "ran read_file",
                "is_error": False,
            },
            {"role": "assistant", "content": "完成"},
        ]
        # 第二轮 raw_content 为 None → 不写 raw_content 键。
        assert "raw_content" not in messages[4]
        # executor 按原序收到原始 arguments。
        assert executor.calls == [
            ("c1", "read_file", {"path": "a.txt"}),
            ("c2", "read_file", {"path": "b.txt"}),
            ("c3", "read_file", {"path": "c.txt"}),
        ]

    def test_provider_sees_growing_history_and_passthrough_kwargs(self):
        """每轮 stream() 收到完整增长中的历史，system/tools 原样透传。"""
        provider, _, loop, _ = _three_round_setup()
        messages = [{"role": "user", "content": "帮我读文件"}]

        run_to_list(loop.run(messages, system="你是文天", tools=[]))

        assert [len(c) for c in provider.calls] == [1, 4, 6]
        assert provider.systems_seen == ["你是文天"] * 3
        assert provider.tools_seen == [[]] * 3

    def test_none_arguments_stored_as_empty_dict_executor_gets_none(self):
        """arguments=None 的调用：入史存 {}，executor 收到 None（v0.3 契约）。"""
        provider = ScriptedProvider(
            [
                [
                    TextDelta("跑一下"),
                    ToolCallEvent(id="c1", name="run_thing", arguments=None),
                    Done(),
                ],
                [TextDelta("好了"), Done()],
            ]
        )
        executor = FakeExecutor()
        registry = FakeRegistry({"run_thing": WRITE})
        loop = AgentLoop(provider, registry=registry, executor=executor)
        messages = [{"role": "user", "content": "跑"}]

        run_to_list(loop.run(messages))

        assert executor.calls == [("c1", "run_thing", None)]
        assert messages[1]["tool_calls"] == [
            {"id": "c1", "name": "run_thing", "arguments": {}},
        ]


class TestRealtimePassthrough:
    def test_text_delta_appears_before_its_round_stream_end(self):
        """TextDelta 在对应轮的 StreamEnd 之前出现（F31 双路实时性证据）。"""
        _, _, loop, _ = _three_round_setup()
        events = run_to_list(loop.run([{"role": "user", "content": "读"}]))

        for n, text in ((1, "先读两个文件"), (2, "再读一个"), (3, "完成")):
            start_i = events.index(RoundStart(n))
            end_i = events.index(StreamEnd(n, text, False))
            delta_i = events.index(TextDelta(text))
            assert start_i < delta_i < end_i


class TestSingleTextRound:
    def test_pure_text_round_completes_in_one_round(self):
        """无 tool_calls 的单轮：RoundStart×1 + AgentDone(COMPLETED, rounds=1)，
        入史仅追加 assistant 文本。"""
        provider = ScriptedProvider([[TextDelta("你好"), Done()]])
        loop = AgentLoop(provider)
        messages = [{"role": "user", "content": "嗨"}]

        events = run_to_list(loop.run(messages))

        assert [type(ev) for ev in events] == [
            RoundStart,
            TextDelta,
            StreamEnd,
            AgentDone,
        ]
        assert events[0] == RoundStart(1)
        assert events[2] == StreamEnd(1, "你好", False)
        done = events[3]
        assert done.stop_reason is StopReason.COMPLETED
        assert done.text == "你好"
        assert done.rounds == 1
        assert done.usage is None
        assert messages == [
            {"role": "user", "content": "嗨"},
            {"role": "assistant", "content": "你好"},
        ]


# ===========================================================================
# T53 — 停机条件（F29 边界）
# ===========================================================================


class TestMaxRounds:
    def test_max_rounds_brake_skips_final_tool_batch(self):
        """max_rounds=2、脚本持续要工具 → 第 2 轮不执行工具、该轮 assistant
        只存文本（无 tool_calls 键）、AgentDone(MAX_ROUNDS, rounds=2)。"""
        provider = ScriptedProvider(
            [
                [
                    TextDelta("先读"),
                    ToolCallEvent(id="c1", name="read_file", arguments={"path": "a"}),
                    Done(),
                ],
                [
                    TextDelta("还想读"),
                    ToolCallEvent(id="c2", name="read_file", arguments={"path": "b"}),
                    Done(),
                ],
            ]
        )
        executor = FakeExecutor()
        registry = FakeRegistry({"read_file": READ})
        loop = AgentLoop(provider, registry=registry, executor=executor, max_rounds=2)
        messages = [{"role": "user", "content": "读"}]

        events = run_to_list(loop.run(messages))

        # 第 2 轮的工具没执行：executor 只收到第 1 轮的调用。
        assert executor.calls == [("c1", "read_file", {"path": "a"})]
        started = [ev for ev in events if isinstance(ev, ToolCallStarted)]
        assert [ev.call.id for ev in started] == ["c1"]
        # 第 2 轮 assistant 只存文本——存未执行的 tool_calls 会 400。
        assert messages[-1] == {"role": "assistant", "content": "还想读"}
        done = events[-1]
        assert done.stop_reason is StopReason.MAX_ROUNDS
        assert done.rounds == 2
        assert done.text == "还想读"


class TestUnknownToolLoop:
    def test_two_all_unknown_rounds_stop_the_loop(self):
        """连续两轮全部调用未注册名 → AgentDone(UNKNOWN_TOOL_LOOP)；错误
        结果已按对入史（assistant+tool 成对），历史对下个用户轮保持一致。"""
        provider = ScriptedProvider(
            [
                [
                    TextDelta("试试"),
                    ToolCallEvent(id="c1", name="ghost1", arguments={}),
                    Done(),
                ],
                [
                    TextDelta("再试"),
                    ToolCallEvent(id="c2", name="ghost2", arguments={}),
                    Done(),
                ],
            ]
        )
        executor = FakeExecutor(error_names={"ghost1", "ghost2"})
        registry = FakeRegistry({"read_file": READ})
        loop = AgentLoop(provider, registry=registry, executor=executor)
        messages = [{"role": "user", "content": "q"}]

        events = run_to_list(loop.run(messages))

        done = events[-1]
        assert done.stop_reason is StopReason.UNKNOWN_TOOL_LOOP
        assert done.rounds == 2
        # AgentDone 在第 2 轮 RoundEnd 之后（结果先入史，再判停）。
        assert isinstance(events[-2], RoundEnd)
        assert messages == [
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": "试试",
                "tool_calls": [{"id": "c1", "name": "ghost1", "arguments": {}}],
            },
            {
                "role": "tool",
                "tool_call_id": "c1",
                "content": "未知工具: ghost1",
                "is_error": True,
            },
            {
                "role": "assistant",
                "content": "再试",
                "tool_calls": [{"id": "c2", "name": "ghost2", "arguments": {}}],
            },
            {
                "role": "tool",
                "tool_call_id": "c2",
                "content": "未知工具: ghost2",
                "is_error": True,
            },
        ]

    def test_registered_round_resets_streak(self):
        """unknown 轮之间穿插一次已注册调用 → 连击重置，循环走到 COMPLETED。"""
        provider = ScriptedProvider(
            [
                [
                    TextDelta("r1"),
                    ToolCallEvent(id="c1", name="ghost", arguments={}),
                    Done(),
                ],  # 连击 1
                [
                    TextDelta("r2"),
                    ToolCallEvent(id="c2", name="read_file", arguments={}),
                    Done(),
                ],  # 已注册 → 连击重置 0
                [
                    TextDelta("r3"),
                    ToolCallEvent(id="c3", name="ghost", arguments={}),
                    Done(),
                ],  # 连击 1（未重置的话此处已达 2）
                [TextDelta("完"), Done()],
            ]
        )
        executor = FakeExecutor(error_names={"ghost"})
        registry = FakeRegistry({"read_file": READ})
        loop = AgentLoop(provider, registry=registry, executor=executor)

        events = run_to_list(loop.run([{"role": "user", "content": "q"}]))

        done = events[-1]
        assert done.stop_reason is StopReason.COMPLETED
        assert done.rounds == 4


class TestUserCancelled:
    def test_partial_text_interrupt_stores_text_only(self):
        """第 2 轮有部分文字时中断 → 该轮 assistant 只存文本（丢弃
        tool_calls），第 1 轮工具交互保留，AgentDone(USER_CANCELLED)。

        T22/T23 已验证的计时模式：第 2 轮的部分事件在毫秒级被消费完，
        0.2s 后 Timer 置位中断切断静默等待。"""
        provider = ScriptThenBlockProvider(
            scripts=[
                [
                    TextDelta("先读"),
                    ToolCallEvent(id="c1", name="read_file", arguments={"path": "a"}),
                    Done(),
                ]
            ],
            final_partial=[
                TextDelta("部分"),
                ToolCallEvent(id="c2", name="read_file", arguments={"path": "b"}),
            ],
        )
        round_no = {"n": 0}

        def arm(event: threading.Event) -> None:
            round_no["n"] += 1
            if round_no["n"] == 2:
                threading.Timer(0.2, event.set).start()

        listener = FakeListener(arm=arm)
        executor = FakeExecutor()
        registry = FakeRegistry({"read_file": READ})
        loop = AgentLoop(
            provider,
            registry=registry,
            executor=executor,
            interrupt_listener=listener,
        )
        messages = [{"role": "user", "content": "读"}]

        events = run_to_list(loop.run(messages))

        done = events[-1]
        assert done.stop_reason is StopReason.USER_CANCELLED
        assert done.rounds == 2
        # 第 2 轮的 tool_call 没执行；第 1 轮工具交互完整保留。
        assert executor.calls == [("c1", "read_file", {"path": "a"})]
        assert messages == [
            {"role": "user", "content": "读"},
            {
                "role": "assistant",
                "content": "先读",
                "tool_calls": [
                    {"id": "c1", "name": "read_file", "arguments": {"path": "a"}},
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "c1",
                "content": "ran read_file",
                "is_error": False,
            },
            {"role": "assistant", "content": "部分"},  # 只存文本，无 tool_calls 键
        ]
        # 每次 __enter__ 都有配对的 __exit__。
        assert listener.enter_count == 2
        assert listener.exit_count == 2

    def test_zero_text_interrupt_appends_nothing(self):
        """第 2 轮零文字中断 → 该轮不入史（messages 长度 = 第 1 轮结束时）。"""
        provider = ScriptThenBlockProvider(
            scripts=[
                [
                    TextDelta("先读"),
                    ToolCallEvent(id="c1", name="read_file", arguments={"path": "a"}),
                    Done(),
                ]
            ],
            final_partial=[],  # 第 2 轮一个事件都没出就被打断
        )
        round_no = {"n": 0}

        def arm(event: threading.Event) -> None:
            round_no["n"] += 1
            if round_no["n"] == 2:
                event.set()  # 第 2 轮进场即中断

        listener = FakeListener(arm=arm)
        executor = FakeExecutor()
        registry = FakeRegistry({"read_file": READ})
        loop = AgentLoop(
            provider,
            registry=registry,
            executor=executor,
            interrupt_listener=listener,
        )
        messages = [{"role": "user", "content": "读"}]

        events = run_to_list(loop.run(messages))

        done = events[-1]
        assert done.stop_reason is StopReason.USER_CANCELLED
        assert done.rounds == 2
        # 第 2 轮零入史：user + assistant(R1) + tool(c1) 共 3 条。
        assert len(messages) == 3
        assert messages[-1]["role"] == "tool"


class TestStreamError:
    def test_round_two_error_keeps_round_one_block(self):
        """第 2 轮 stream 抛错 → 第 1 轮成块保留、第 2 轮零入史、
        AgentDone(STREAM_ERROR, error 含异常信息)，异常不逃逸 run()。"""
        provider = ErrorScriptProvider(
            [
                [
                    TextDelta("先读"),
                    ToolCallEvent(id="c1", name="read_file", arguments={"path": "a"}),
                    Done(),
                ],
                [TextDelta("半截"), RuntimeError("连接炸了")],
            ]
        )
        executor = FakeExecutor()
        registry = FakeRegistry({"read_file": READ})
        loop = AgentLoop(provider, registry=registry, executor=executor)
        messages = [{"role": "user", "content": "读"}]

        events = run_to_list(loop.run(messages))

        done = events[-1]
        assert done.stop_reason is StopReason.STREAM_ERROR
        assert "连接炸了" in done.error
        assert done.rounds == 2
        # 第 2 轮整轮丢弃（连部分文字也不入史）；第 1 轮原子块保留。
        assert messages == [
            {"role": "user", "content": "读"},
            {
                "role": "assistant",
                "content": "先读",
                "tool_calls": [
                    {"id": "c1", "name": "read_file", "arguments": {"path": "a"}},
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "c1",
                "content": "ran read_file",
                "is_error": False,
            },
        ]

    def test_first_round_error_leaves_only_user_message(self):
        """首轮即抛错 → messages 仅含 user（调用方据此回滚）。"""
        provider = ErrorScriptProvider([[RuntimeError("启动即炸")]])
        loop = AgentLoop(provider)
        messages = [{"role": "user", "content": "q"}]

        events = run_to_list(loop.run(messages))

        done = events[-1]
        assert done.stop_reason is StopReason.STREAM_ERROR
        assert "启动即炸" in done.error
        assert done.rounds == 1
        assert messages == [{"role": "user", "content": "q"}]


class TestBlockedCalls:
    def test_blocked_call_never_reaches_executor(self):
        """allowed 名单外的调用 → executor 不被调，合成错误结果含
        「计划模式」与可用工具名，错误对正常入史。"""
        provider = ScriptedProvider(
            [
                [
                    TextDelta("想写文件"),
                    ToolCallEvent(id="c1", name="write_file", arguments={"path": "x"}),
                    Done(),
                ],
                [TextDelta("那先算了"), Done()],
            ]
        )
        executor = FakeExecutor()
        registry = FakeRegistry({"read_file": READ, "write_file": WRITE})
        loop = AgentLoop(
            provider,
            registry=registry,
            executor=executor,
            allowed_tools=frozenset({"read_file"}),
        )
        messages = [{"role": "user", "content": "写"}]

        events = run_to_list(loop.run(messages))

        assert executor.calls == []  # blocked 绝不触达 executor
        results = [ev for ev in events if isinstance(ev, ToolResultReady)]
        assert len(results) == 1
        outcome = results[0].outcome
        assert outcome.call_id == "c1"
        assert outcome.is_error is True
        assert outcome.denied is False  # 模式拦截不是用户拒绝
        assert "计划模式" in outcome.content
        assert "read_file" in outcome.content
        # 合成错误按对入史，循环继续走到 COMPLETED。
        assert messages[2] == {
            "role": "tool",
            "tool_call_id": "c1",
            "content": outcome.content,
            "is_error": True,
        }
        assert events[-1].stop_reason is StopReason.COMPLETED

    def test_blocked_rounds_do_not_count_toward_unknown_streak(self):
        """连续两轮全 blocked → 不触发 UNKNOWN_TOOL_LOOP，照常走到 COMPLETED。"""
        provider = ScriptedProvider(
            [
                [
                    ToolCallEvent(id="c1", name="write_file", arguments={}),
                    Done(),
                ],
                [
                    ToolCallEvent(id="c2", name="write_file", arguments={}),
                    Done(),
                ],
                [TextDelta("完"), Done()],
            ]
        )
        executor = FakeExecutor()
        registry = FakeRegistry({"read_file": READ, "write_file": WRITE})
        loop = AgentLoop(
            provider,
            registry=registry,
            executor=executor,
            allowed_tools=frozenset({"read_file"}),
        )

        events = run_to_list(loop.run([{"role": "user", "content": "写"}]))

        done = events[-1]
        assert done.stop_reason is StopReason.COMPLETED
        assert done.rounds == 3
        assert executor.calls == []


# ===========================================================================
# T54 — 用量累计（F34）
# ===========================================================================


def _usage_setup(usages: list[Usage | None]):
    """构造 len(usages) 轮场景：前面各轮都是单 tool_call 轮，最后一轮纯文本；
    每轮 Done 携带 usages 中对应的 Usage（或 None）。"""
    scripts: list[list] = []
    last = len(usages) - 1
    for i, usage in enumerate(usages):
        if i < last:
            scripts.append(
                [
                    TextDelta(f"r{i + 1}"),
                    ToolCallEvent(
                        id=f"c{i + 1}", name="read_file", arguments={"path": f"{i}"}
                    ),
                    Done(usage=usage),
                ]
            )
        else:
            scripts.append([TextDelta("完"), Done(usage=usage)])
    provider = ScriptedProvider(scripts)
    registry = FakeRegistry({"read_file": READ})
    return AgentLoop(provider, registry=registry, executor=FakeExecutor())


class TestUsageAccumulation:
    def test_every_round_reports_usage_totals_increase(self):
        """三轮各报 Usage → 每轮 UsageUpdate(round_usage, total) 数值正确、
        total 逐轮递增，AgentDone.usage 为三轮总和。"""
        loop = _usage_setup([Usage(10, 5), Usage(20, 7), Usage(5, 2)])

        events = run_to_list(loop.run([{"role": "user", "content": "q"}]))

        updates = [ev for ev in events if isinstance(ev, UsageUpdate)]
        assert updates == [
            UsageUpdate(round_usage=Usage(10, 5), total=Usage(10, 5)),
            UsageUpdate(round_usage=Usage(20, 7), total=Usage(30, 12)),
            UsageUpdate(round_usage=Usage(5, 2), total=Usage(35, 14)),
        ]
        done = events[-1]
        assert done.stop_reason is StopReason.COMPLETED
        assert done.usage == Usage(35, 14)

    def test_rounds_without_usage_emit_no_update(self):
        """部分轮 usage=None → 跳过该轮 UsageUpdate（不发零值事件），
        总和只计有报的轮次。"""
        loop = _usage_setup([Usage(10, 5), None, Usage(1, 2)])

        events = run_to_list(loop.run([{"role": "user", "content": "q"}]))

        updates = [ev for ev in events if isinstance(ev, UsageUpdate)]
        assert updates == [
            UsageUpdate(round_usage=Usage(10, 5), total=Usage(10, 5)),
            UsageUpdate(round_usage=Usage(1, 2), total=Usage(11, 7)),
        ]
        assert events[-1].usage == Usage(11, 7)

    def test_no_usage_at_all_means_none(self):
        """全程无 usage → 零 UsageUpdate 事件、AgentDone.usage=None。"""
        loop = _usage_setup([None, None, None])

        events = run_to_list(loop.run([{"role": "user", "content": "q"}]))

        assert [ev for ev in events if isinstance(ev, UsageUpdate)] == []
        done = events[-1]
        assert done.stop_reason is StopReason.COMPLETED
        assert done.rounds == 3
        assert done.usage is None


# ===========================================================================
# T64 — request_decorator 回调 + 跨轮缓存累计（F39/F40 · C25）
# ===========================================================================


class _RecordingDecorator:
    """记录每次调用的 (len(messages), round_index)；
    在 messages 末尾 user 消息的 content 追加一个固定标记 '[DEC]'，
    返回一个新列表（不 mutate 原件）。"""

    MARKER = "[DEC]"

    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []  # (len(messages), round_index)

    def __call__(self, messages: list, round_index: int) -> list:
        self.calls.append((len(messages), round_index))
        # 构造副本：浅拷贝整个列表，最后一条消息的 content 加标记
        result = list(messages)
        if result:
            last = dict(result[-1])
            last["content"] = (last.get("content") or "") + self.MARKER
            result[-1] = last
        return result


class TestRequestDecorator:
    def _two_round_loop(self):
        """两轮脚本：第 1 轮有工具 → 第 2 轮纯文本。"""
        provider = ScriptedProvider(
            [
                [
                    TextDelta("round1"),
                    ToolCallEvent(id="c1", name="read_file", arguments={"path": "a"}),
                    Done(usage=Usage(10, 5)),
                ],
                [TextDelta("done"), Done(usage=Usage(5, 2))],
            ]
        )
        registry = FakeRegistry({"read_file": READ})
        executor = FakeExecutor()
        loop = AgentLoop(provider, registry=registry, executor=executor)
        return provider, loop

    def test_decorator_called_each_round_with_correct_index(self):
        """decorator 每轮被调一次，round_index 从 1 递增。"""
        provider, loop = self._two_round_loop()
        dec = _RecordingDecorator()
        messages = [{"role": "user", "content": "hi"}]

        run_to_list(loop.run(messages, request_decorator=dec))

        # 两轮 → decorator 被调两次，index = 1, 2
        assert [c[1] for c in dec.calls] == [1, 2]

    def test_provider_receives_decorator_output_not_original(self):
        """provider 每轮收到的 messages 末尾含 decorator 注入的标记。"""
        provider, loop = self._two_round_loop()
        dec = _RecordingDecorator()
        messages = [{"role": "user", "content": "hi"}]

        run_to_list(loop.run(messages, request_decorator=dec))

        # 每轮 provider 收到的最后一条消息内容应含标记
        for call_msgs in provider.calls:
            assert _RecordingDecorator.MARKER in call_msgs[-1].get("content", ""), (
                f"provider call missing MARKER: {call_msgs[-1]}"
            )

    def test_original_messages_not_contaminated_after_run(self):
        """循环结束后，messages 原件不含任何 decorator 注入的标记。"""
        provider, loop = self._two_round_loop()
        dec = _RecordingDecorator()
        messages = [{"role": "user", "content": "hi"}]

        run_to_list(loop.run(messages, request_decorator=dec))

        marker = _RecordingDecorator.MARKER
        for msg in messages:
            assert marker not in (msg.get("content") or ""), (
                f"messages contaminated with decorator marker: {msg}"
            )

    def test_decorator_receives_original_each_round_not_snowball(self):
        """第 2 轮 decorator 收到的 messages 不含第 1 轮 decorator 注入的标记
        （每轮从原件重算，不是滚雪球）。"""
        provider, loop = self._two_round_loop()
        # 记录每次 decorator 被调时，其参数里是否有标记
        snapshots: list[list] = []

        def spy_decorator(messages: list, round_index: int) -> list:
            snapshots.append(
                [dict(m) for m in messages]
            )  # deep-ish copy for comparison
            # 依然注入标记，以便 provider 那侧能检测
            result = list(messages)
            if result:
                last = dict(result[-1])
                last["content"] = (
                    last.get("content") or ""
                ) + _RecordingDecorator.MARKER
                result[-1] = last
            return result

        messages = [{"role": "user", "content": "hi"}]
        run_to_list(loop.run(messages, request_decorator=spy_decorator))

        marker = _RecordingDecorator.MARKER
        # 第 2 轮 decorator 入参不应包含第 1 轮注入的标记
        if len(snapshots) >= 2:
            for msg in snapshots[1]:
                assert marker not in (msg.get("content") or ""), (
                    f"Round-2 decorator input contains round-1 injected marker: {msg}"
                )

    def test_decorator_none_behavior_identical_to_no_decorator(self):
        """request_decorator=None 时，provider 收到的就是原件，事件序列与不传时一致。"""
        scripts = [
            [
                TextDelta("r1"),
                ToolCallEvent(id="c1", name="read_file", arguments={"path": "a"}),
                Done(usage=Usage(10, 5)),
            ],
            [TextDelta("done"), Done(usage=Usage(5, 2))],
        ]
        registry = FakeRegistry({"read_file": READ})

        # 不传 decorator
        p1 = ScriptedProvider(scripts)
        loop1 = AgentLoop(p1, registry=registry, executor=FakeExecutor())
        msgs1 = [{"role": "user", "content": "hi"}]
        events1 = run_to_list(loop1.run(msgs1))

        # 传 None
        p2 = ScriptedProvider(scripts)
        loop2 = AgentLoop(p2, registry=registry, executor=FakeExecutor())
        msgs2 = [{"role": "user", "content": "hi"}]
        events2 = run_to_list(loop2.run(msgs2, request_decorator=None))

        assert [type(e) for e in events1] == [type(e) for e in events2]
        # provider 收到的 messages 形状相同
        assert [len(c) for c in p1.calls] == [len(c) for c in p2.calls]


class TestCacheUsageAccumulation:
    def test_cache_tokens_accumulate_across_rounds(self):
        """两轮各带 cache_read_input_tokens 和 cache_creation_input_tokens
        → 最终 total 各字段正确累加。"""
        provider = ScriptedProvider(
            [
                [
                    TextDelta("r1"),
                    ToolCallEvent(id="c1", name="read_file", arguments={"path": "a"}),
                    Done(
                        usage=Usage(
                            input_tokens=10,
                            output_tokens=5,
                            cache_creation_input_tokens=200,
                            cache_read_input_tokens=100,
                        )
                    ),
                ],
                [
                    TextDelta("done"),
                    Done(
                        usage=Usage(
                            input_tokens=8,
                            output_tokens=3,
                            cache_creation_input_tokens=0,
                            cache_read_input_tokens=50,
                        )
                    ),
                ],
            ]
        )
        registry = FakeRegistry({"read_file": READ})
        loop = AgentLoop(provider, registry=registry, executor=FakeExecutor())
        messages = [{"role": "user", "content": "q"}]

        events = run_to_list(loop.run(messages))

        updates = [ev for ev in events if isinstance(ev, UsageUpdate)]
        assert len(updates) == 2
        final_total = updates[-1].total
        assert final_total.input_tokens == 18
        assert final_total.output_tokens == 8
        assert final_total.cache_creation_input_tokens == 200
        assert final_total.cache_read_input_tokens == 150

    def test_cache_tokens_zero_when_not_reported(self):
        """Usage 未携带缓存字段（用默认值 0）→ total 缓存字段也为 0，
        不会出现 None 或负数。"""
        provider = ScriptedProvider(
            [
                [
                    TextDelta("r1"),
                    ToolCallEvent(id="c1", name="read_file", arguments={"path": "a"}),
                    Done(usage=Usage(10, 5)),  # 默认 cache fields = 0
                ],
                [TextDelta("done"), Done(usage=Usage(5, 2))],
            ]
        )
        registry = FakeRegistry({"read_file": READ})
        loop = AgentLoop(provider, registry=registry, executor=FakeExecutor())

        events = run_to_list(loop.run([{"role": "user", "content": "q"}]))

        updates = [ev for ev in events if isinstance(ev, UsageUpdate)]
        final_total = updates[-1].total
        assert final_total.cache_creation_input_tokens == 0
        assert final_total.cache_read_input_tokens == 0


# ===========================================================================
# T76 — permission_gate 判定门接入 + Deny 回灌不中断（v0.6 · C36 · F46/F49）
# ===========================================================================


def _denied_outcome(call: ToolCallEvent) -> _Outcome:
    """假 gate 构造的成形拒绝结果对象（鸭子兼容 ToolOutcome，含 denied=True）。

    模拟装配层（C37 gate 闭包）按来源措辞好的拒绝结果——loop 只负责原样回灌，
    不解释 verdict/source、不自己合成。"""
    return _Outcome(
        call_id=call.id,
        name=call.name,
        content=f"操作 {call.name} 被拒绝（测试门）",
        is_error=True,
        denied=True,
    )


class TestPermissionGate:
    def test_gate_none_behaves_identically_to_v05(self):
        """permission_gate=None → 事件序列与入史与不传门时完全一致（回归 AC56）。"""
        _, _, loop, raw = _three_round_setup()
        msgs_no_gate = [{"role": "user", "content": "帮我读文件"}]
        events_no_gate = run_to_list(loop.run(msgs_no_gate))

        _, exec2, loop2, _ = _three_round_setup()
        msgs_gate_none = [{"role": "user", "content": "帮我读文件"}]
        events_gate_none = run_to_list(
            loop2.run(msgs_gate_none, request_decorator=None)
        )
        # 显式 permission_gate=None 路径：另起一组同脚本对比。
        prov3 = ScriptedProvider(
            [
                [
                    TextDelta("先读两个文件"),
                    ToolCallEvent(
                        id="c1", name="read_file", arguments={"path": "a.txt"}
                    ),
                    ToolCallEvent(
                        id="c2", name="read_file", arguments={"path": "b.txt"}
                    ),
                    Done(
                        usage=Usage(10, 5),
                        raw_content=[{"type": "text", "text": "先读两个文件"}],
                    ),
                ],
                [
                    TextDelta("再读一个"),
                    ToolCallEvent(
                        id="c3", name="read_file", arguments={"path": "c.txt"}
                    ),
                    Done(usage=Usage(20, 7)),
                ],
                [TextDelta("完成"), Done(usage=Usage(5, 2))],
            ]
        )
        exec3 = FakeExecutor()
        loop3 = AgentLoop(
            prov3,
            registry=FakeRegistry({"read_file": READ}),
            executor=exec3,
            permission_gate=None,
        )
        msgs_explicit = [{"role": "user", "content": "帮我读文件"}]
        events_explicit = run_to_list(loop3.run(msgs_explicit))

        # 事件类型序列三者一致。
        types_no_gate = [type(e) for e in events_no_gate]
        assert [type(e) for e in events_gate_none] == types_no_gate
        assert [type(e) for e in events_explicit] == types_no_gate
        # 入史一致（permission_gate=None 不改写任何历史）。
        assert msgs_explicit == msgs_no_gate
        # 三个调用都正常触达 executor。
        assert [c[0] for c in exec3.calls] == ["c1", "c2", "c3"]

    def test_gate_denial_object_returned_verbatim_executor_untouched(self):
        """假 gate 对某调用返回成形拒绝对象 → loop 原样回灌、该调用不触达
        executor；gate 返 None 的调用正常进 executor（F46/F49）。"""
        provider = ScriptedProvider(
            [
                [
                    TextDelta("读再写"),
                    ToolCallEvent(id="c1", name="read_file", arguments={"path": "a"}),
                    ToolCallEvent(id="c2", name="write_file", arguments={"path": "b"}),
                    Done(),
                ],
                [TextDelta("好"), Done()],
            ]
        )
        executor = FakeExecutor()
        registry = FakeRegistry({"read_file": READ, "write_file": WRITE})

        denied_obj_box: dict = {}

        async def gate(call: ToolCallEvent):
            if call.name == "write_file":
                obj = _denied_outcome(call)
                denied_obj_box["obj"] = obj
                return obj  # 非 None = 成形拒绝结果
            return None  # 放行

        loop = AgentLoop(
            provider, registry=registry, executor=executor, permission_gate=gate
        )
        messages = [{"role": "user", "content": "做"}]

        events = run_to_list(loop.run(messages))

        # 被拒的 write_file 绝不触达 executor；只有放行的 read_file 进了 executor。
        assert [c[1] for c in executor.calls] == ["read_file"]
        assert ("c2", "write_file", {"path": "b"}) not in [
            (c[0], c[1], c[2]) for c in executor.calls
        ]
        # loop 原样回灌门返回的同一对象（is 同一性）。
        results = [ev for ev in events if isinstance(ev, ToolResultReady)]
        result_by_id = {ev.outcome.call_id: ev.outcome for ev in results}
        assert result_by_id["c2"] is denied_obj_box["obj"]
        assert result_by_id["c1"].content == "ran read_file"
        # 拒绝结果按对入史，content/is_error 取门对象的字段。
        assert messages[3] == {
            "role": "tool",
            "tool_call_id": "c2",
            "content": denied_obj_box["obj"].content,
            "is_error": True,
        }
        assert events[-1].stop_reason is StopReason.COMPLETED

    def test_denied_and_allowed_pair_by_original_order_and_id(self):
        """单批 [读A, 写B(门拒), 读C] → denied 与 allow 结果按原调用序、原 call.id
        配对入史、互不串位（AC51）。"""
        provider = ScriptedProvider(
            [
                [
                    TextDelta("一批三个"),
                    ToolCallEvent(id="A", name="read_file", arguments={"path": "a"}),
                    ToolCallEvent(id="B", name="write_file", arguments={"path": "b"}),
                    ToolCallEvent(id="C", name="read_file", arguments={"path": "c"}),
                    Done(),
                ],
                [TextDelta("收"), Done()],
            ]
        )
        executor = FakeExecutor()
        registry = FakeRegistry({"read_file": READ, "write_file": WRITE})

        async def gate(call: ToolCallEvent):
            if call.name == "write_file":
                return _denied_outcome(call)
            return None

        loop = AgentLoop(
            provider, registry=registry, executor=executor, permission_gate=gate
        )
        messages = [{"role": "user", "content": "批"}]

        run_to_list(loop.run(messages))

        # 入史顺序：assistant(3 calls) → tool A(allow) → tool B(denied) → tool C(allow)
        tool_msgs = [m for m in messages if m["role"] == "tool"]
        assert [m["tool_call_id"] for m in tool_msgs] == ["A", "B", "C"]
        # A、C 是放行结果（executor 跑出的 content）；B 是门的拒绝结果。
        assert tool_msgs[0]["content"] == "ran read_file"
        assert tool_msgs[0]["is_error"] is False
        assert "被拒绝" in tool_msgs[1]["content"]
        assert tool_msgs[1]["is_error"] is True
        assert tool_msgs[2]["content"] == "ran read_file"
        assert tool_msgs[2]["is_error"] is False
        # executor 只收到放行的 A、C（按原序），写 B 没触达。
        assert [c[0] for c in executor.calls] == ["A", "C"]

    def test_readonly_wave_stays_concurrent_under_gate(self):
        """只读 wave（假 gate 对只读同步返 None=放行）仍并发、不被门串行化
        （AC53）：用阻塞式 executor 计时，证明三只读并行触达。"""
        provider = ScriptedProvider(
            [
                [
                    TextDelta("三连读"),
                    ToolCallEvent(id="r1", name="read_file", arguments={"path": "1"}),
                    ToolCallEvent(id="r2", name="read_file", arguments={"path": "2"}),
                    ToolCallEvent(id="r3", name="read_file", arguments={"path": "3"}),
                    Done(),
                ],
                [TextDelta("完"), Done()],
            ]
        )
        registry = FakeRegistry({"read_file": READ})

        # 阻塞式 executor：每个 execute 卡在屏障上，三个都到齐才放行 → 仅当
        # 三个并行触达时才不会死锁（串行会卡在第一个 wait 上）。
        barrier = threading.Barrier(3, timeout=2.0)

        class BarrierExecutor:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, object]] = []
                self._lock = threading.Lock()

            def execute(self, call_id, name, arguments):
                with self._lock:
                    self.calls.append((call_id, name, arguments))
                barrier.wait()  # 三个并行才能通过；串行会超时抛 BrokenBarrierError
                return _Outcome(
                    call_id=call_id,
                    name=name,
                    content=f"ran {name}",
                    is_error=False,
                )

        executor = BarrierExecutor()
        gate_calls: list[str] = []

        async def gate(call: ToolCallEvent):
            gate_calls.append(call.id)
            return None  # 只读同步放行，不 await UI

        loop = AgentLoop(
            provider, registry=registry, executor=executor, permission_gate=gate
        )

        messages = [{"role": "user", "content": "读"}]
        events = run_to_list(loop.run(messages))

        # 没死锁/超时 → 三只读确实并行触达 executor。
        assert {c[0] for c in executor.calls} == {"r1", "r2", "r3"}
        assert set(gate_calls) == {"r1", "r2", "r3"}
        # 结果仍按原调用序入史。
        tool_msgs = [m for m in messages if m["role"] == "tool"]
        assert [m["tool_call_id"] for m in tool_msgs] == ["r1", "r2", "r3"]
        assert events[-1].stop_reason is StopReason.COMPLETED


# ===========================================================================
# T95 — pre_round_compact 钩子（v0.8 · C51 · F62/N25）
# ===========================================================================


class _RecordingCompact:
    """记录式压缩钩子：每轮记录 (len(messages), last_usage) 并按需原地改写。

    原地改写靠副作用、返回 None——loop 不解释返回值（纯鸭子回调）。
    *mutate* 为 True 时删除 messages 的首条消息（模拟 L2 摘要切割）。
    """

    def __init__(self, *, mutate: bool = False) -> None:
        # (len(messages) at call time, last_round_usage object) per round
        self.calls: list[tuple[int, Usage | None]] = []
        self._mutate = mutate

    def __call__(self, messages: list[Message], last_usage: Usage | None) -> None:
        self.calls.append((len(messages), last_usage))
        if self._mutate and len(messages) > 1:
            del messages[0]
        return None


def _two_round_compact_loop():
    """两轮脚本：第 1 轮有工具（round usage=Usage(10,5)）→ 第 2 轮纯文本。"""
    provider = ScriptedProvider(
        [
            [
                TextDelta("round1"),
                ToolCallEvent(id="c1", name="read_file", arguments={"path": "a"}),
                Done(usage=Usage(10, 5)),
            ],
            [TextDelta("done"), Done(usage=Usage(20, 7))],
        ]
    )
    registry = FakeRegistry({"read_file": READ})
    executor = FakeExecutor()
    loop = AgentLoop(provider, registry=registry, executor=executor)
    return provider, loop


class TestPreRoundCompact:
    def test_hook_called_each_round_with_last_round_usage_sequence(self):
        """钩子每轮被调一次；last_usage 首轮 None、其后为上一轮 round usage（单轮、非累计）。"""
        provider, loop = _two_round_compact_loop()
        hook = _RecordingCompact()
        messages = [{"role": "user", "content": "hi"}]

        run_to_list(loop.run(messages, pre_round_compact=hook))

        # 两轮 → 钩子被调两次。
        assert len(hook.calls) == 2
        # 首轮 last_usage=None。
        assert hook.calls[0][1] is None
        # 第 2 轮 last_usage 是第 1 轮的 round usage（Usage(10,5)），
        # 不是累计 total（若用累计会是 Usage(10,5) 仍同值——故用第 1 轮特意区分的值校验）。
        assert hook.calls[1][1] == Usage(10, 5)

    def test_hook_called_after_round_start_before_stream(self):
        """调用时机：RoundStart 之后、provider.stream 之前——
        钩子被调时 provider 尚未收到该轮请求。"""
        provider, loop = _two_round_compact_loop()
        timeline: list[str] = []

        def hook(messages: list[Message], last_usage: Usage | None) -> None:
            # 钩子运行时刻，provider 已发起的 stream 次数。
            timeline.append(f"hook@{len(provider.calls)}")

        # 包一层 ScriptedProvider 的 stream 记录已在 provider.calls；
        # 钩子在每轮 stream 前调用 → 首次钩子时 provider.calls 为 0、
        # 第二次钩子时 provider.calls 为 1（仅第 1 轮 stream 已发生）。
        run_to_list(
            loop.run([{"role": "user", "content": "hi"}], pre_round_compact=hook)
        )

        assert timeline == ["hook@0", "hook@1"]

    def test_hook_rewrites_messages_in_place_seen_by_provider(self):
        """会改写 messages 的假钩子（删首条）→ provider 收到的 outgoing 基于改写后历史。"""
        provider, loop = _two_round_compact_loop()
        hook = _RecordingCompact(mutate=True)
        messages = [
            {"role": "user", "content": "old-1"},
            {"role": "user", "content": "keep-this"},
        ]

        run_to_list(loop.run(messages, pre_round_compact=hook))

        # 第 1 轮 stream 前钩子删了首条 → provider 第 1 轮收到的首条是 keep-this。
        assert provider.calls[0][0]["content"] == "keep-this"
        # 原件被原地改写（副作用）：old-1 已被删除。
        assert all(m.get("content") != "old-1" for m in messages)

    def test_compact_runs_before_request_decorator(self):
        """顺序：先压缩（钩子改写 messages）、后包 reminder（decorator 基于改写后历史）。"""
        provider, loop = _two_round_compact_loop()

        # 压缩钩子：第 1 轮删首条消息。
        def compact(messages: list[Message], last_usage: Usage | None) -> None:
            if len(messages) > 1:
                del messages[0]

        # decorator：基于钩子改写后的 messages 计算 outgoing。
        # 若 decorator 先于压缩跑，它会看到（并基于）未删的首条。
        dec_first_seen: list[str] = []

        def decorator(messages: list[Message], round_index: int) -> list[Message]:
            dec_first_seen.append(messages[0]["content"])
            return messages

        messages = [
            {"role": "user", "content": "old-1"},
            {"role": "user", "content": "keep-this"},
        ]
        run_to_list(
            loop.run(
                messages,
                pre_round_compact=compact,
                request_decorator=decorator,
            )
        )

        # 第 1 轮 decorator 收到的首条已是压缩后的 keep-this（压缩在前）。
        assert dec_first_seen[0] == "keep-this"

    def test_hook_none_behavior_identical(self):
        """钩子为 None（默认）→ 与不传时事件序列、provider 收到形状字节级等价（N25 回归）。"""
        scripts = [
            [
                TextDelta("r1"),
                ToolCallEvent(id="c1", name="read_file", arguments={"path": "a"}),
                Done(usage=Usage(10, 5)),
            ],
            [TextDelta("done"), Done(usage=Usage(5, 2))],
        ]
        registry = FakeRegistry({"read_file": READ})

        # 不传钩子。
        p1 = ScriptedProvider(scripts)
        loop1 = AgentLoop(p1, registry=registry, executor=FakeExecutor())
        msgs1 = [{"role": "user", "content": "hi"}]
        events1 = run_to_list(loop1.run(msgs1))

        # 传 None。
        p2 = ScriptedProvider(scripts)
        loop2 = AgentLoop(p2, registry=registry, executor=FakeExecutor())
        msgs2 = [{"role": "user", "content": "hi"}]
        events2 = run_to_list(loop2.run(msgs2, pre_round_compact=None))

        assert [type(e) for e in events1] == [type(e) for e in events2]
        assert [len(c) for c in p1.calls] == [len(c) for c in p2.calls]
        # messages 终态一致（入史不变）。
        assert msgs1 == msgs2
