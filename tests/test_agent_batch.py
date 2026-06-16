"""Tests for agent/batch.py: classify、partition_waves、run_wave（v0.4 · C16 · F32 · T51）."""

import asyncio
import dataclasses
import time

import pytest
from wentian.agent.batch import Wave, classify, partition_waves, run_wave
from wentian.providers.base import ToolCallEvent


# --- 测试替身（agent 层鸭子类型契约：registry.get(name) -> tool|None） ---


class FakeTool:
    """带 requires_confirmation 属性的假工具。"""

    def __init__(self, requires_confirmation: bool):
        self.requires_confirmation = requires_confirmation


class FakeToolNoAttr:
    """缺失 requires_confirmation 属性的假工具（fail-safe 用例）。"""


class FakeRegistry:
    """get 按 dict 查的假注册表。"""

    def __init__(self, tools: dict):
        self._tools = tools

    def get(self, name):
        return self._tools.get(name)


def _call(name: str, cid: str = "c1") -> ToolCallEvent:
    return ToolCallEvent(id=cid, name=name, arguments={})


READ = FakeTool(requires_confirmation=False)
WRITE = FakeTool(requires_confirmation=True)


# --- classify 四态 ---


class TestClassify:
    def test_registered_read_only(self):
        """已注册且 requires_confirmation=False → read_only。"""
        reg = FakeRegistry({"read": READ})
        assert classify(_call("read"), reg, None) == "read_only"

    def test_requires_confirmation_true_is_side_effect(self):
        """requires_confirmation=True → side_effect。"""
        reg = FakeRegistry({"write": WRITE})
        assert classify(_call("write"), reg, None) == "side_effect"

    def test_unregistered_is_unknown(self):
        """未注册的工具名 → unknown。"""
        reg = FakeRegistry({"read": READ})
        assert classify(_call("ghost"), reg, None) == "unknown"

    def test_not_in_allowed_is_blocked(self):
        """已注册但在 allowed 名单外 → blocked（plan 模式拦截）。"""
        reg = FakeRegistry({"write": WRITE})
        assert classify(_call("write"), reg, frozenset({"read"})) == "blocked"

    def test_unknown_takes_priority_over_blocked(self):
        """未注册且名单外 → unknown（优先级 unknown > blocked）。"""
        reg = FakeRegistry({})
        assert classify(_call("ghost"), reg, frozenset({"read"})) == "unknown"

    def test_blocked_takes_priority_over_read_write(self):
        """名单外的只读工具仍是 blocked（优先级 blocked > 读写判定）。"""
        reg = FakeRegistry({"read": READ})
        assert classify(_call("read"), reg, frozenset({"other"})) == "blocked"

    def test_in_allowed_passes_through_to_read_write(self):
        """allowed 名单内的工具正常走读写判定。"""
        reg = FakeRegistry({"read": READ})
        assert classify(_call("read"), reg, frozenset({"read"})) == "read_only"

    def test_missing_requires_confirmation_is_side_effect(self):
        """requires_confirmation 属性缺失 → side_effect（fail-safe）。"""
        reg = FakeRegistry({"mystery": FakeToolNoAttr()})
        assert classify(_call("mystery"), reg, None) == "side_effect"


# --- partition_waves 分波 ---


class TestPartitionWaves:
    def test_read_read_write_read(self):
        """[读,读,写,读] → [并发(读,读), 串行(写), 串行(读)]。"""
        reg = FakeRegistry({"read": READ, "write": WRITE})
        calls = [
            _call("read", "c1"),
            _call("read", "c2"),
            _call("write", "c3"),
            _call("read", "c4"),
        ]
        waves = partition_waves(calls, reg, None)
        assert len(waves) == 3
        assert waves[0] == Wave(calls=(calls[0], calls[1]), concurrent=True)
        assert waves[1] == Wave(calls=(calls[2],), concurrent=False)
        assert waves[2] == Wave(calls=(calls[3],), concurrent=False)

    def test_all_reads_single_concurrent_wave(self):
        """全只读 → 单个并发 Wave。"""
        reg = FakeRegistry({"read": READ})
        calls = [_call("read", "c1"), _call("read", "c2"), _call("read", "c3")]
        waves = partition_waves(calls, reg, None)
        assert waves == [Wave(calls=tuple(calls), concurrent=True)]

    def test_unknown_gets_own_serial_wave_in_position(self):
        """含 unknown → 独立串行 Wave 且保持原位置（单读段也退化为串行）。"""
        reg = FakeRegistry({"read": READ, "read2": READ})
        calls = [
            _call("read", "c1"),
            _call("read2", "c2"),
            _call("ghost", "c3"),
            _call("read", "c4"),
        ]
        waves = partition_waves(calls, reg, None)
        assert len(waves) == 3
        assert waves[0] == Wave(calls=(calls[0], calls[1]), concurrent=True)
        assert waves[1] == Wave(calls=(calls[2],), concurrent=False)
        assert waves[2] == Wave(calls=(calls[3],), concurrent=False)

    def test_empty_calls_no_waves(self):
        """空调用列表 → 空 Wave 列表。"""
        reg = FakeRegistry({})
        assert partition_waves([], reg, None) == []

    def test_wave_is_frozen(self):
        """Wave 是 frozen dataclass：赋值抛 FrozenInstanceError。"""
        w = Wave(calls=(), concurrent=False)
        with pytest.raises(dataclasses.FrozenInstanceError):
            w.concurrent = True


# --- run_wave 执行 ---


async def _collect(wave, run_call):
    out = []
    async for item in run_wave(wave, run_call):
        out.append(item)
    return out


class TestRunWave:
    def test_concurrent_wave_runs_in_parallel_and_preserves_order(self):
        """两个 sleep(0.2) 只读调用并发执行：总墙钟 < 0.35s，结果按原调用顺序。"""
        calls = (_call("alpha", "c1"), _call("beta", "c2"))
        wave = Wave(calls=calls, concurrent=True)

        async def run_call(c):
            await asyncio.sleep(0.2)
            return c.name

        start = time.monotonic()
        items = asyncio.run(_collect(wave, run_call))
        elapsed = time.monotonic() - start

        assert elapsed < 0.35
        results = [it for it in items if it[0] == "result"]
        assert [it[1] for it in results] == list(calls)
        assert [it[2] for it in results] == ["alpha", "beta"]

    def test_concurrent_wave_event_shape(self):
        """并发 Wave：每个调用按原顺序产出 started→result 对，started 的 outcome 为 None。"""
        calls = (_call("a", "c1"), _call("b", "c2"))
        wave = Wave(calls=calls, concurrent=True)

        async def run_call(c):
            return c.name.upper()

        items = asyncio.run(_collect(wave, run_call))
        assert [(k, c.id) for k, c, _ in items] == [
            ("started", "c1"),
            ("result", "c1"),
            ("started", "c2"),
            ("result", "c2"),
        ]
        assert items[0][2] is None
        assert items[1][2] == "A"
        assert items[3][2] == "B"

    def test_serial_wave_started_before_execution(self):
        """串行 Wave：started 在 run_call 执行之前产出（⏺ 行先于确认提示）。"""
        call = _call("write", "c1")
        wave = Wave(calls=(call,), concurrent=False)
        executed = []

        async def run_call(c):
            executed.append(c.id)
            return "done"

        async def main():
            gen = run_wave(wave, run_call)
            first = await gen.__anext__()
            assert first == ("started", call, None)
            assert executed == []  # started 时还没执行
            second = await gen.__anext__()
            assert second == ("result", call, "done")
            assert executed == ["c1"]
            with pytest.raises(StopAsyncIteration):
                await gen.__anext__()

        asyncio.run(main())
