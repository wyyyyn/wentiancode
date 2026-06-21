"""Tests for dynamic reminder injection — EnvInfo / render_env_reminder /
render_switch_reminder / build_request_decorator.

v0.5 · C22（任务 T60）

动态提醒（环境信息 + 会话开关）在每次发请求时注入消息流，
以 <system-reminder> 标签包裹、永不持久化。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wentian.prompt.reminders import (
    EnvInfo,
    build_request_decorator,
    render_env_reminder,
    render_switch_reminder,
)
from wentian.providers.base import Message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_env() -> EnvInfo:
    return EnvInfo(
        cwd=Path("/home/user/project"),
        os="Darwin 25.0",
        date="2026-06-15",
        git_branch="main",
    )


def _user(content: str) -> Message:
    return {"role": "user", "content": content}


def _assistant(content: str) -> Message:
    return {"role": "assistant", "content": content}


def _tool(content: str) -> Message:
    return {"role": "tool", "content": content, "tool_call_id": "id1"}


# ---------------------------------------------------------------------------
# 1. render_env_reminder — 标签与四项内容
# ---------------------------------------------------------------------------


class TestRenderEnvReminder:
    def test_has_system_reminder_tags(self):
        env = _make_env()
        result = render_env_reminder(env)
        assert "<system-reminder>" in result
        assert "</system-reminder>" in result

    def test_contains_cwd(self):
        env = _make_env()
        result = render_env_reminder(env)
        assert "/home/user/project" in result

    def test_contains_os(self):
        env = _make_env()
        result = render_env_reminder(env)
        assert "Darwin 25.0" in result

    def test_contains_date(self):
        env = _make_env()
        result = render_env_reminder(env)
        assert "2026-06-15" in result

    def test_contains_git_branch(self):
        env = _make_env()
        result = render_env_reminder(env)
        assert "main" in result

    def test_git_branch_none_shows_fallback(self):
        env = EnvInfo(cwd=Path("/tmp"), os="Linux", date="2026-06-15", git_branch=None)
        result = render_env_reminder(env)
        # Should indicate non-git repo, not "None"
        assert "None" not in result
        assert result  # non-empty


# ---------------------------------------------------------------------------
# 2. render_switch_reminder — cadence 规则
# ---------------------------------------------------------------------------

# 完整提醒的专属子串（必须在完整提醒中出现，精简提醒中不出现）
_FULL_MARKER = "分步执行计划"


class TestRenderSwitchReminder:
    def test_plan_mode_false_returns_none(self):
        assert render_switch_reminder(plan_mode=False, round_index=1) is None
        assert render_switch_reminder(plan_mode=False, round_index=3) is None

    def test_round_1_returns_full(self):
        result = render_switch_reminder(plan_mode=True, round_index=1)
        assert result is not None
        assert _FULL_MARKER in result

    def test_round_6_returns_full(self):
        # round_index=6: (6-1) % 5 == 0 → 完整
        result = render_switch_reminder(plan_mode=True, round_index=6)
        assert result is not None
        assert _FULL_MARKER in result

    def test_round_11_returns_full(self):
        # round_index=11: (11-1) % 5 == 0 → 完整
        result = render_switch_reminder(plan_mode=True, round_index=11)
        assert result is not None
        assert _FULL_MARKER in result

    @pytest.mark.parametrize("round_index", [2, 3, 4, 5])
    def test_middle_rounds_return_brief(self, round_index):
        result = render_switch_reminder(plan_mode=True, round_index=round_index)
        assert result is not None
        # 精简提醒：含 <system-reminder> 标签
        assert "<system-reminder>" in result
        assert "</system-reminder>" in result
        # 不含完整提醒专属子串
        assert _FULL_MARKER not in result

    def test_full_reminder_has_tags(self):
        result = render_switch_reminder(plan_mode=True, round_index=1)
        assert result is not None
        assert "<system-reminder>" in result
        assert "</system-reminder>" in result

    def test_custom_repeat_every(self):
        # repeat_every=3：round_index=4 → (4-1)%3==0 → 完整
        result = render_switch_reminder(plan_mode=True, round_index=4, repeat_every=3)
        assert result is not None
        assert _FULL_MARKER in result

        # round_index=2 → 精简
        result2 = render_switch_reminder(plan_mode=True, round_index=2, repeat_every=3)
        assert result2 is not None
        assert _FULL_MARKER not in result2


# ---------------------------------------------------------------------------
# 3. build_request_decorator — 注入逻辑
# ---------------------------------------------------------------------------


class TestBuildRequestDecorator:
    def test_single_user_message_gets_env_prefix_and_switch_suffix(self):
        env = _make_env()
        decorator = build_request_decorator(env=env, plan_mode=True)
        msgs: list[Message] = [_user("hi")]
        result = decorator(msgs, 1)

        content = result[0]["content"]
        # env 提醒在最前
        assert content.startswith("<system-reminder>")
        # 原内容居中
        assert "hi" in content
        # switch 提醒在最后（round_index=1 → 完整）
        assert content.endswith("</system-reminder>")
        # 完整提醒标记
        assert _FULL_MARKER in content

    def test_does_not_mutate_original_messages(self):
        env = _make_env()
        decorator = build_request_decorator(env=env, plan_mode=True)
        original_content = "original content"
        msgs: list[Message] = [_user(original_content)]
        original_list_id = id(msgs)

        decorator(msgs, 1)

        # 原列表对象不变
        assert id(msgs) == original_list_id
        # 原 dict 的 content 不变
        assert msgs[0]["content"] == original_content
        assert len(msgs) == 1

    def test_plan_mode_false_no_switch_in_result(self):
        env = _make_env()
        decorator = build_request_decorator(env=env, plan_mode=False)
        msgs: list[Message] = [_user("hello")]
        result = decorator(msgs, 1)

        content = result[0]["content"]
        # env 提醒存在
        assert "<system-reminder>" in content
        # 计划模式标记不存在
        assert _FULL_MARKER not in content
        assert "计划模式" not in content

    def test_multi_turn_env_on_first_user_switch_on_last_user(self):
        env = _make_env()
        decorator = build_request_decorator(env=env, plan_mode=True)
        msgs: list[Message] = [
            _user("first user"),
            _assistant("assistant reply"),
            _tool("tool result"),
            _user("last user"),
        ]
        result = decorator(msgs, 1)

        first_user_content = result[0]["content"]
        last_user_content = result[3]["content"]
        assistant_content = result[1]["content"]
        tool_content = result[2]["content"]

        # env 前置到第一条 user
        assert first_user_content.startswith("<system-reminder>")
        assert "first user" in first_user_content

        # switch 追加到最后一条 user
        assert "last user" in last_user_content
        assert _FULL_MARKER in last_user_content

        # 中间消息不变
        assert assistant_content == "assistant reply"
        assert tool_content == "tool result"

    def test_multi_turn_no_mutate(self):
        env = _make_env()
        decorator = build_request_decorator(env=env, plan_mode=True)
        msgs: list[Message] = [
            _user("u1"),
            _assistant("a1"),
            _user("u2"),
        ]
        decorator(msgs, 1)
        assert msgs[0]["content"] == "u1"
        assert msgs[1]["content"] == "a1"
        assert msgs[2]["content"] == "u2"

    def test_no_user_messages_does_not_raise(self):
        env = _make_env()
        decorator = build_request_decorator(env=env, plan_mode=True)
        msgs: list[Message] = [_assistant("x")]
        result = decorator(msgs, 1)  # must not raise
        assert result[0]["content"] == "x"

    def test_returns_new_list_object(self):
        env = _make_env()
        decorator = build_request_decorator(env=env, plan_mode=False)
        msgs: list[Message] = [_user("test")]
        result = decorator(msgs, 1)
        assert result is not msgs

    def test_single_user_both_env_and_switch_injected_correctly(self):
        """单轮：第一条 user == 最后一条 user，两者同时注入同一条消息。"""
        env = _make_env()
        decorator = build_request_decorator(env=env, plan_mode=True)
        msgs: list[Message] = [_user("middle")]
        result = decorator(msgs, 1)
        content = result[0]["content"]
        # 顺序：env 提醒 → 原 content → switch 提醒
        env_reminder_end = content.find("</system-reminder>")
        middle_pos = content.find("middle")
        switch_marker_pos = content.find(_FULL_MARKER)
        assert env_reminder_end < middle_pos < switch_marker_pos


# ---------------------------------------------------------------------------
# 4. build_request_decorator — active_skill_bodies 注入（v0.11 · C106 / T131）
# ---------------------------------------------------------------------------


class TestActiveSkillBodiesInjection:
    def test_skill_bodies_appended_to_last_user_message(self):
        """两个已激活 skill 的正文都以 <system-reminder> 块追加到最后一条 user。"""
        env = _make_env()
        decorator = build_request_decorator(
            env=env,
            plan_mode=False,
            active_skill_bodies=lambda: [("commit", "正文A"), ("review", "正文B")],
        )
        msgs: list[Message] = [_user("hi")]
        result = decorator(msgs, 1)
        content = result[0]["content"]

        # 两个 skill 的标题 + 正文都在最后一条 user 里
        assert "# 已激活 Skill: commit" in content
        assert "正文A" in content
        assert "# 已激活 Skill: review" in content
        assert "正文B" in content
        # 以 <system-reminder> 标签包裹
        assert "<system-reminder>" in content
        assert content.endswith("</system-reminder>")
        # 原内容仍在
        assert "hi" in content
        # 顺序：commit 块在 review 块之前
        assert content.find("# 已激活 Skill: commit") < content.find(
            "# 已激活 Skill: review"
        )

    def test_skill_bodies_do_not_mutate_input(self):
        """注入 skill 正文不得 mutate 入参列表或其中任何 dict。"""
        import copy as _copy

        env = _make_env()
        decorator = build_request_decorator(
            env=env,
            plan_mode=True,
            active_skill_bodies=lambda: [("commit", "BODY_C"), ("review", "BODY_R")],
        )
        msgs: list[Message] = [
            _user("u1"),
            _assistant("a1"),
            _user("u2"),
        ]
        snapshot = _copy.deepcopy(msgs)
        original_list_id = id(msgs)

        decorator(msgs, 1)

        # 列表对象不变、逐条 deep-equal
        assert id(msgs) == original_list_id
        assert msgs == snapshot

    def test_skill_bodies_appended_to_last_user_in_multi_turn(self):
        """多轮：skill 正文追加到最后一条 user，中间消息不受影响。"""
        env = _make_env()
        decorator = build_request_decorator(
            env=env,
            plan_mode=False,
            active_skill_bodies=lambda: [("x", "BODY_X")],
        )
        msgs: list[Message] = [
            _user("first user"),
            _assistant("assistant reply"),
            _user("last user"),
        ]
        result = decorator(msgs, 1)

        assert "BODY_X" not in result[0]["content"]  # 不在第一条 user
        assert result[1]["content"] == "assistant reply"  # 中间不变
        assert "# 已激活 Skill: x" in result[2]["content"]
        assert "BODY_X" in result[2]["content"]

    def test_skill_bodies_read_live_not_snapshot(self):
        """LIVE 读取：可变源列表，构建后追加的 skill 下一次 apply 才出现。"""
        env = _make_env()
        srcs: list[tuple[str, str]] = []
        decorator = build_request_decorator(
            env=env,
            plan_mode=False,
            active_skill_bodies=lambda: srcs,
        )
        msgs: list[Message] = [_user("hello")]

        # 第一次：源为空 → 无 skill reminder
        result1 = decorator(msgs, 1)
        assert "# 已激活 Skill" not in result1[0]["content"]
        assert "BODY" not in result1[0]["content"]

        # 中途激活
        srcs.append(("x", "BODY"))

        # 第二次：BODY 已注入
        result2 = decorator(msgs, 2)
        assert "# 已激活 Skill: x" in result2[0]["content"]
        assert "BODY" in result2[0]["content"]

    def test_active_skill_bodies_none_identical_to_baseline(self):
        """active_skill_bodies=None → 与不传该参数行为一致（无 skill 残渣）。"""
        env = _make_env()
        msgs: list[Message] = [_user("hi")]

        baseline = build_request_decorator(env=env, plan_mode=True)
        with_none = build_request_decorator(
            env=env, plan_mode=True, active_skill_bodies=None
        )

        assert baseline(msgs, 1)[0]["content"] == with_none(msgs, 1)[0]["content"]
        assert "# 已激活 Skill" not in with_none(msgs, 1)[0]["content"]

    def test_active_skill_bodies_empty_list_no_injection(self):
        """active_skill_bodies 返回 [] → 不注入、不改变 content。"""
        env = _make_env()
        msgs: list[Message] = [_user("hi")]

        baseline = build_request_decorator(env=env, plan_mode=False)
        with_empty = build_request_decorator(
            env=env, plan_mode=False, active_skill_bodies=lambda: []
        )

        assert baseline(msgs, 1)[0]["content"] == with_empty(msgs, 1)[0]["content"]
        assert "# 已激活 Skill" not in with_empty(msgs, 1)[0]["content"]

    def test_no_user_message_with_skill_bodies_does_not_raise(self):
        """无 user 消息时即便有 skill 正文也不抛错。"""
        env = _make_env()
        decorator = build_request_decorator(
            env=env,
            plan_mode=False,
            active_skill_bodies=lambda: [("x", "BODY")],
        )
        msgs: list[Message] = [_assistant("x")]
        result = decorator(msgs, 1)  # must not raise
        assert result[0]["content"] == "x"
