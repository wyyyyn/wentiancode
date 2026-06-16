"""Tests for agent/events.py: StopReason、AgentEvent 联合、RoundResult（v0.4 · C14 · F30 · T47）."""

import dataclasses
import enum

import pytest
from wentian.agent.events import (
    AgentDone,
    AgentEvent,
    RoundEnd,
    RoundResult,
    RoundStart,
    StopReason,
    StreamEnd,
    ToolCallStarted,
    ToolResultReady,
    UsageUpdate,
)
from wentian.providers.base import TextDelta, ThinkingDelta, ToolCallEvent, Usage


# --- StopReason ---


class TestStopReason:
    def test_is_enum(self):
        """StopReason 是 enum.Enum 的子类。"""
        assert issubclass(StopReason, enum.Enum)

    def test_exactly_five_members(self):
        """StopReason 恰有五个成员。"""
        assert len(StopReason) == 5

    def test_member_values(self):
        """五个成员的值与 spec 一致。"""
        assert StopReason.COMPLETED.value == "completed"
        assert StopReason.MAX_ROUNDS.value == "max_rounds"
        assert StopReason.USER_CANCELLED.value == "user_cancelled"
        assert StopReason.UNKNOWN_TOOL_LOOP.value == "unknown_tool_loop"
        assert StopReason.STREAM_ERROR.value == "stream_error"


# --- 事件可构造且 frozen ---


class TestEventConstructionAndFrozen:
    def test_round_start(self):
        """RoundStart(index) 可构造；赋值抛 FrozenInstanceError。"""
        ev = RoundStart(index=1)
        assert ev.index == 1
        with pytest.raises(dataclasses.FrozenInstanceError):
            ev.index = 2

    def test_usage_update(self):
        """UsageUpdate(round_usage, total) 可构造、frozen。"""
        ru = Usage(input_tokens=1, output_tokens=2)
        total = Usage(input_tokens=10, output_tokens=20)
        ev = UsageUpdate(round_usage=ru, total=total)
        assert ev.round_usage is ru
        assert ev.total is total
        with pytest.raises(dataclasses.FrozenInstanceError):
            ev.total = ru

    def test_stream_end(self):
        """StreamEnd(index, text, interrupted) 可构造、frozen。"""
        ev = StreamEnd(index=1, text="hi", interrupted=False)
        assert ev.index == 1
        assert ev.text == "hi"
        assert ev.interrupted is False
        with pytest.raises(dataclasses.FrozenInstanceError):
            ev.text = "bye"

    def test_tool_call_started(self):
        """ToolCallStarted.call 接受 providers.base.ToolCallEvent；frozen。"""
        call = ToolCallEvent(id="call_1", name="read", arguments={"path": "/tmp/x"})
        ev = ToolCallStarted(call=call)
        assert ev.call is call
        assert ev.call.name == "read"
        with pytest.raises(dataclasses.FrozenInstanceError):
            ev.call = call

    def test_tool_result_ready(self):
        """ToolResultReady.outcome 鸭子类型（object），可装任意结果对象；frozen。"""
        outcome = object()
        ev = ToolResultReady(outcome=outcome)
        assert ev.outcome is outcome
        with pytest.raises(dataclasses.FrozenInstanceError):
            ev.outcome = None

    def test_round_end(self):
        """RoundEnd(index, tool_results) 可构造、frozen。"""
        ev = RoundEnd(index=2, tool_results=3)
        assert ev.index == 2
        assert ev.tool_results == 3
        with pytest.raises(dataclasses.FrozenInstanceError):
            ev.index = 9

    def test_agent_done(self):
        """AgentDone 可构造、frozen。"""
        u = Usage(input_tokens=5, output_tokens=7)
        ev = AgentDone(
            stop_reason=StopReason.COMPLETED,
            text="done",
            rounds=2,
            usage=u,
            error="boom",
        )
        assert ev.stop_reason is StopReason.COMPLETED
        assert ev.text == "done"
        assert ev.rounds == 2
        assert ev.usage is u
        assert ev.error == "boom"
        with pytest.raises(dataclasses.FrozenInstanceError):
            ev.text = "x"


# --- AgentDone 默认值 / RoundResult ---


class TestAgentDoneDefaultsAndRoundResult:
    def test_agent_done_error_defaults_to_none(self):
        """AgentDone 不传 error 时默认为 None。"""
        ev = AgentDone(
            stop_reason=StopReason.MAX_ROUNDS,
            text="",
            rounds=8,
            usage=None,
        )
        assert ev.error is None

    def test_round_result_construct(self):
        """RoundResult(text, tool_calls, raw_content, usage, done_seen) 可构造。"""
        call = ToolCallEvent(id="c1", name="bash", arguments={})
        u = Usage(input_tokens=1, output_tokens=1)
        rr = RoundResult(
            text="hello",
            tool_calls=(call,),
            raw_content=[{"type": "text", "text": "hello"}],
            usage=u,
            done_seen=True,
        )
        assert rr.text == "hello"
        assert rr.tool_calls == (call,)
        assert rr.raw_content == [{"type": "text", "text": "hello"}]
        assert rr.usage is u
        assert rr.done_seen is True

    def test_round_result_is_frozen(self):
        """RoundResult 是 frozen：赋值抛 FrozenInstanceError。"""
        rr = RoundResult(
            text="",
            tool_calls=(),
            raw_content=None,
            usage=None,
            done_seen=False,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            rr.text = "x"


# --- 事件联合可 isinstance 分发 ---


class TestAgentEventUnionDispatch:
    def _sample_events(self):
        call = ToolCallEvent(id="c1", name="read", arguments=None)
        u = Usage(input_tokens=1, output_tokens=1)
        return [
            RoundStart(index=1),
            ThinkingDelta("t"),
            TextDelta("x"),
            UsageUpdate(round_usage=u, total=u),
            StreamEnd(index=1, text="x", interrupted=False),
            ToolCallStarted(call=call),
            ToolResultReady(outcome=object()),
            RoundEnd(index=1, tool_results=1),
            AgentDone(stop_reason=StopReason.COMPLETED, text="x", rounds=1, usage=u),
        ]

    def test_all_events_match_union(self):
        """每个事件实例都能通过 isinstance(ev, AgentEvent) 检查（PEP 604 联合）。"""
        for ev in self._sample_events():
            assert isinstance(ev, AgentEvent), type(ev).__name__

    def test_if_chain_dispatch_covers_all_types(self):
        """一条 if/elif 链可以覆盖联合中的全部事件类型，无遗漏。"""
        seen: list[str] = []
        for ev in self._sample_events():
            if isinstance(ev, RoundStart):
                seen.append("round_start")
            elif isinstance(ev, ThinkingDelta):
                seen.append("thinking_delta")
            elif isinstance(ev, TextDelta):
                seen.append("text_delta")
            elif isinstance(ev, UsageUpdate):
                seen.append("usage_update")
            elif isinstance(ev, StreamEnd):
                seen.append("stream_end")
            elif isinstance(ev, ToolCallStarted):
                seen.append("tool_call_started")
            elif isinstance(ev, ToolResultReady):
                seen.append("tool_result_ready")
            elif isinstance(ev, RoundEnd):
                seen.append("round_end")
            elif isinstance(ev, AgentDone):
                seen.append("agent_done")
            else:  # pragma: no cover - 联合应已覆盖全部类型
                pytest.fail(f"unhandled event type: {type(ev).__name__}")
        assert seen == [
            "round_start",
            "thinking_delta",
            "text_delta",
            "usage_update",
            "stream_end",
            "tool_call_started",
            "tool_result_ready",
            "round_end",
            "agent_done",
        ]
