"""Tests for agent/collector.py: RoundCollector 双路收集器（v0.4 · C14 · F31 · T49）."""
import pytest
from wentian.agent.collector import RoundCollector
from wentian.agent.events import RoundResult
from wentian.providers.base import Done, TextDelta, ThinkingDelta, ToolCallEvent, Usage


# --- feed()：显示路径透传 ---

class TestFeedPassthrough:
    def test_text_delta_returned_as_is(self):
        """feed(TextDelta) 返回同一事件对象（实时显示路径）。"""
        c = RoundCollector()
        ev = TextDelta("hello")
        assert c.feed(ev) is ev

    def test_text_delta_accumulates_into_text(self):
        """TextDelta.text 片段按序拼接进 result().text。"""
        c = RoundCollector()
        c.feed(TextDelta("hel"))
        c.feed(TextDelta("lo"))
        assert c.result(interrupted=False).text == "hello"

    def test_thinking_delta_returned_as_is(self):
        """feed(ThinkingDelta) 返回同一事件对象（透传）。"""
        c = RoundCollector()
        ev = ThinkingDelta("hmm")
        assert c.feed(ev) is ev

    def test_thinking_delta_not_counted_in_text(self):
        """ThinkingDelta 不计入 text 累积。"""
        c = RoundCollector()
        c.feed(ThinkingDelta("reasoning"))
        c.feed(TextDelta("answer"))
        assert c.result(interrupted=False).text == "answer"


# --- feed()：静默收集路径 ---

class TestFeedSilentCollection:
    def test_tool_call_returns_none(self):
        """feed(ToolCallEvent) 返回 None（流中不渲染工具调用，v0.3 同规）。"""
        c = RoundCollector()
        call = ToolCallEvent(id="c1", name="read", arguments={"path": "/tmp/x"})
        assert c.feed(call) is None

    def test_tool_calls_collected_in_order(self):
        """多个 ToolCallEvent 按到达顺序收集进 result().tool_calls。"""
        c = RoundCollector()
        c1 = ToolCallEvent(id="c1", name="read", arguments={})
        c2 = ToolCallEvent(id="c2", name="bash", arguments=None)
        c.feed(c1)
        c.feed(c2)
        assert c.result(interrupted=False).tool_calls == (c1, c2)

    def test_done_returns_none(self):
        """feed(Done) 返回 None。"""
        c = RoundCollector()
        assert c.feed(Done()) is None

    def test_done_captures_usage_raw_content_and_done_seen(self):
        """Done 的 usage 与 raw_content 被捕获，done_seen=True。"""
        c = RoundCollector()
        u = Usage(input_tokens=3, output_tokens=7)
        raw = [{"type": "text", "text": "hi"}]
        c.feed(Done(usage=u, raw_content=raw))
        rr = c.result(interrupted=False)
        assert rr.usage is u
        assert rr.raw_content is raw
        assert rr.done_seen is True


# --- result() ---

class TestResult:
    def test_returns_round_result_with_all_fields(self):
        """完整一轮后 result() 返回字段正确的 RoundResult。"""
        c = RoundCollector()
        call = ToolCallEvent(id="c1", name="bash", arguments={"cmd": "ls"})
        u = Usage(input_tokens=1, output_tokens=2)
        raw = [{"type": "tool_use", "id": "c1"}]
        c.feed(ThinkingDelta("think"))
        c.feed(TextDelta("a"))
        c.feed(TextDelta("b"))
        c.feed(call)
        c.feed(Done(usage=u, raw_content=raw))
        rr = c.result(interrupted=False)
        assert isinstance(rr, RoundResult)
        assert rr.text == "ab"
        assert rr.tool_calls == (call,)
        assert rr.raw_content is raw
        assert rr.usage is u
        assert rr.done_seen is True

    def test_empty_round_text_is_empty_string(self):
        """无任何 TextDelta 时 text 为 ""（v0.2 约定）。"""
        c = RoundCollector()
        rr = c.result(interrupted=False)
        assert rr.text == ""
        assert rr.tool_calls == ()

    def test_without_done_fields_default(self):
        """未见 Done 时 done_seen=False、usage/raw_content 为 None。"""
        c = RoundCollector()
        c.feed(TextDelta("partial"))
        rr = c.result(interrupted=False)
        assert rr.done_seen is False
        assert rr.usage is None
        assert rr.raw_content is None

    def test_interrupted_forces_done_seen_false(self):
        """interrupted=True 时即使见过 Done，done_seen 也一律为 False（中断优先）。"""
        c = RoundCollector()
        u = Usage(input_tokens=1, output_tokens=1)
        c.feed(Done(usage=u, raw_content=None))
        rr = c.result(interrupted=True)
        assert rr.done_seen is False

    def test_interrupted_is_keyword_only(self):
        """interrupted 是仅限关键字参数。"""
        c = RoundCollector()
        with pytest.raises(TypeError):
            c.result(False)  # noqa: B026 — 故意按位置传参验证签名
