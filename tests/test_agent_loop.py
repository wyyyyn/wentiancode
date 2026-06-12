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
    RoundStart, StreamEnd, ToolCallStarted, ToolResultReady, RoundEnd, AgentDone,
)


def _three_round_setup():
    """T52 标准场景：text+2 tool_calls → text+1 tool_call → 纯文本。"""
    raw = [{"type": "text", "text": "先读两个文件"}]
    provider = ScriptedProvider([
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
    ])
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
            RoundStart, StreamEnd,                     # 轮 1 流阶段
            ToolCallStarted, ToolResultReady,          # c1
            ToolCallStarted, ToolResultReady,          # c2
            RoundEnd,
            RoundStart, StreamEnd,                     # 轮 2
            ToolCallStarted, ToolResultReady,          # c3
            RoundEnd,
            RoundStart, StreamEnd,                     # 轮 3（纯文本）
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
            Usage(10, 5), Usage(20, 7), Usage(5, 2),
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
            {"role": "tool", "tool_call_id": "c1",
             "content": "ran read_file", "is_error": False},
            {"role": "tool", "tool_call_id": "c2",
             "content": "ran read_file", "is_error": False},
            {
                "role": "assistant",
                "content": "再读一个",
                "tool_calls": [
                    {"id": "c3", "name": "read_file", "arguments": {"path": "c.txt"}},
                ],
            },
            {"role": "tool", "tool_call_id": "c3",
             "content": "ran read_file", "is_error": False},
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
        provider = ScriptedProvider([
            [
                TextDelta("跑一下"),
                ToolCallEvent(id="c1", name="run_thing", arguments=None),
                Done(),
            ],
            [TextDelta("好了"), Done()],
        ])
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
            RoundStart, TextDelta, StreamEnd, AgentDone,
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
        provider = ScriptedProvider([
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
        ])
        executor = FakeExecutor()
        registry = FakeRegistry({"read_file": READ})
        loop = AgentLoop(
            provider, registry=registry, executor=executor, max_rounds=2
        )
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
        provider = ScriptedProvider([
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
        ])
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
            {"role": "tool", "tool_call_id": "c1",
             "content": "未知工具: ghost1", "is_error": True},
            {
                "role": "assistant",
                "content": "再试",
                "tool_calls": [{"id": "c2", "name": "ghost2", "arguments": {}}],
            },
            {"role": "tool", "tool_call_id": "c2",
             "content": "未知工具: ghost2", "is_error": True},
        ]

    def test_registered_round_resets_streak(self):
        """unknown 轮之间穿插一次已注册调用 → 连击重置，循环走到 COMPLETED。"""
        provider = ScriptedProvider([
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
        ])
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
            scripts=[[
                TextDelta("先读"),
                ToolCallEvent(id="c1", name="read_file", arguments={"path": "a"}),
                Done(),
            ]],
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
            provider, registry=registry, executor=executor,
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
            {"role": "tool", "tool_call_id": "c1",
             "content": "ran read_file", "is_error": False},
            {"role": "assistant", "content": "部分"},  # 只存文本，无 tool_calls 键
        ]
        # 每次 __enter__ 都有配对的 __exit__。
        assert listener.enter_count == 2
        assert listener.exit_count == 2

    def test_zero_text_interrupt_appends_nothing(self):
        """第 2 轮零文字中断 → 该轮不入史（messages 长度 = 第 1 轮结束时）。"""
        provider = ScriptThenBlockProvider(
            scripts=[[
                TextDelta("先读"),
                ToolCallEvent(id="c1", name="read_file", arguments={"path": "a"}),
                Done(),
            ]],
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
            provider, registry=registry, executor=executor,
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
        provider = ErrorScriptProvider([
            [
                TextDelta("先读"),
                ToolCallEvent(id="c1", name="read_file", arguments={"path": "a"}),
                Done(),
            ],
            [TextDelta("半截"), RuntimeError("连接炸了")],
        ])
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
            {"role": "tool", "tool_call_id": "c1",
             "content": "ran read_file", "is_error": False},
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
        provider = ScriptedProvider([
            [
                TextDelta("想写文件"),
                ToolCallEvent(id="c1", name="write_file", arguments={"path": "x"}),
                Done(),
            ],
            [TextDelta("那先算了"), Done()],
        ])
        executor = FakeExecutor()
        registry = FakeRegistry({"read_file": READ, "write_file": WRITE})
        loop = AgentLoop(
            provider, registry=registry, executor=executor,
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
            "role": "tool", "tool_call_id": "c1",
            "content": outcome.content, "is_error": True,
        }
        assert events[-1].stop_reason is StopReason.COMPLETED

    def test_blocked_rounds_do_not_count_toward_unknown_streak(self):
        """连续两轮全 blocked → 不触发 UNKNOWN_TOOL_LOOP，照常走到 COMPLETED。"""
        provider = ScriptedProvider([
            [
                ToolCallEvent(id="c1", name="write_file", arguments={}),
                Done(),
            ],
            [
                ToolCallEvent(id="c2", name="write_file", arguments={}),
                Done(),
            ],
            [TextDelta("完"), Done()],
        ])
        executor = FakeExecutor()
        registry = FakeRegistry({"read_file": READ, "write_file": WRITE})
        loop = AgentLoop(
            provider, registry=registry, executor=executor,
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
            scripts.append([
                TextDelta(f"r{i + 1}"),
                ToolCallEvent(
                    id=f"c{i + 1}", name="read_file", arguments={"path": f"{i}"}
                ),
                Done(usage=usage),
            ])
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
