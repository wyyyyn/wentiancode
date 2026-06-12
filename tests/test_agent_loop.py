"""Tests for agent/loop.py: AgentLoop ReAct 主路径（v0.4 · C17 · F29 · T52）."""
from __future__ import annotations

from conftest import ScriptedProvider, run_to_list

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
from wentian.providers.base import Done, TextDelta, ToolCallEvent, Usage


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
    """记录 execute() 调用并返回成功 outcome（永不抛异常，v0.3 契约）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []

    def execute(self, call_id: str, name: str, arguments):
        self.calls.append((call_id, name, arguments))
        return _Outcome(
            call_id=call_id,
            name=name,
            content=f"ran {name}",
            is_error=False,
        )


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
