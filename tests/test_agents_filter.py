"""v0.13 · C111 · F97/N52 — 三层过滤 + 嵌套防护测试。

Tests for resolve_allowed_tools in wentian.agents.filter.
"""

from __future__ import annotations

from wentian.permissions.decision import Category
from wentian.agents.filter import resolve_allowed_tools


# ---------------------------------------------------------------------------
# 1. 全局禁止（globally_forbidden 默认为 {"Agent"}）
# ---------------------------------------------------------------------------


class TestGlobalForbid:
    def test_agent_removed_by_default(self):
        """Agent 被默认全局禁止，其余工具保留。"""
        result = resolve_allowed_tools(
            {"Read", "Write", "Bash", "Agent"},
            role_allow=None,
            role_deny=(),
            background=False,
        )
        assert "Agent" not in result
        assert {"Read", "Write", "Bash"} <= result

    def test_globally_forbidden_default_only_agent(self):
        """仅 Agent 被全局禁止（无 role 过滤）。"""
        result = resolve_allowed_tools(
            {"Read", "Agent"},
            role_allow=None,
            role_deny=(),
        )
        assert result == frozenset({"Read"})


# ---------------------------------------------------------------------------
# 2. 角色白名单（role_allow）与黑名单（role_deny）
# ---------------------------------------------------------------------------


class TestRoleAllowDeny:
    def test_role_allow_narrows_to_whitelist(self):
        """role_allow 收窄到白名单内的工具。"""
        result = resolve_allowed_tools(
            {"Read", "Write", "Bash", "Agent"},
            role_allow=("Read", "Write"),
            role_deny=(),
        )
        assert result == frozenset({"Read", "Write"})

    def test_role_allow_none_no_narrowing(self):
        """role_allow=None 不收窄（全集保留，除全局禁止项外）。"""
        result = resolve_allowed_tools(
            {"Read", "Write", "Bash"},
            role_allow=None,
            role_deny=(),
        )
        assert result == frozenset({"Read", "Write", "Bash"})

    def test_role_deny_removes_tool(self):
        """role_deny 从结果中减去指定工具。"""
        result = resolve_allowed_tools(
            {"Read", "Write", "Bash"},
            role_allow=None,
            role_deny=("Bash",),
        )
        assert "Bash" not in result
        assert "Read" in result
        assert "Write" in result

    def test_role_deny_and_allow_combined(self):
        """白名单 + 黑名单组合：白名单收窄后黑名单再减。"""
        result = resolve_allowed_tools(
            {"Read", "Write", "Bash", "Agent"},
            role_allow=("Read", "Write", "Bash"),
            role_deny=("Bash",),
        )
        assert result == frozenset({"Read", "Write"})


# ---------------------------------------------------------------------------
# 3. 后台过滤（background=True）
# ---------------------------------------------------------------------------


class TestBackgroundFilter:
    def test_background_filters_file_write_and_command_exec(self):
        """后台模式过滤 FILE_WRITE 和 COMMAND_EXEC 类工具；仅保留 READ_ONLY 或显式白名单。"""
        tool_categories = {
            "Read": Category.READ_ONLY,
            "Write": Category.FILE_WRITE,
            "Bash": Category.COMMAND_EXEC,
        }
        result = resolve_allowed_tools(
            {"Read", "Write", "Bash"},
            role_allow=None,
            role_deny=(),
            background=True,
            background_allow=(),
            tool_categories=tool_categories,
        )
        assert "Bash" not in result
        assert "Write" not in result
        assert "Read" in result

    def test_background_allow_overrides_category_filter(self):
        """background_allow 中的工具即使非 READ_ONLY 也被允许。"""
        tool_categories = {
            "Read": Category.READ_ONLY,
            "Write": Category.FILE_WRITE,
            "Bash": Category.COMMAND_EXEC,
        }
        result = resolve_allowed_tools(
            {"Read", "Write", "Bash"},
            role_allow=None,
            role_deny=(),
            background=True,
            background_allow=("Bash",),
            tool_categories=tool_categories,
        )
        assert "Bash" in result  # 显式白名单覆盖
        assert "Write" not in result
        assert "Read" in result

    def test_background_unknown_category_filtered_out(self):
        """tool_categories 中未登记 + 未在 background_allow 的工具被过滤（安全默认）。"""
        tool_categories = {
            "Read": Category.READ_ONLY,
            # "Mystery" 未登记
        }
        result = resolve_allowed_tools(
            {"Read", "Mystery"},
            role_allow=None,
            role_deny=(),
            background=True,
            background_allow=(),
            tool_categories=tool_categories,
        )
        assert "Mystery" not in result  # 未知类别 → 过滤
        assert "Read" in result

    def test_background_no_tool_categories_filters_all_non_allowlisted(self):
        """tool_categories=None 时后台模式下非 background_allow 工具全部过滤。"""
        result = resolve_allowed_tools(
            {"Read", "Write", "Bash"},
            role_allow=None,
            role_deny=(),
            background=True,
            background_allow=("Read",),
            tool_categories=None,
        )
        assert result == frozenset({"Read"})


# ---------------------------------------------------------------------------
# 4. 顺序不变性：全局禁止优先，角色黑名单在其后生效
# ---------------------------------------------------------------------------


class TestOrderInvariants:
    def test_deny_after_global_forbid_agent_still_absent(self):
        """角色黑名单不含 Agent，但 Agent 仍被全局禁止过滤。"""
        result = resolve_allowed_tools(
            {"Read", "Write", "Bash", "Agent"},
            role_allow=None,
            role_deny=("Write",),  # 不含 Agent
        )
        assert "Agent" not in result  # 全局禁止生效
        assert "Write" not in result  # 黑名单生效
        assert "Read" in result

    def test_whitelist_cannot_rescue_globally_forbidden(self):
        """白名单包含 Agent，但 Agent 仍不可被救回（全局禁止优先）。"""
        result = resolve_allowed_tools(
            {"Read", "Agent"},
            role_allow=("Agent", "Read"),  # 白名单含 Agent
            role_deny=(),
        )
        assert "Agent" not in result  # 全局禁止 > 白名单
        assert "Read" in result


# ---------------------------------------------------------------------------
# 5. 嵌套防护：多工具 globally_forbidden
# ---------------------------------------------------------------------------


class TestNestedGuard:
    def test_multiple_globally_forbidden(self):
        """globally_forbidden 中多个工具均被过滤。"""
        result = resolve_allowed_tools(
            {"Read", "Agent", "AnotherTool", "Bash"},
            role_allow=None,
            role_deny=(),
            globally_forbidden=frozenset({"Agent", "AnotherTool"}),
        )
        assert "Agent" not in result
        assert "AnotherTool" not in result
        assert "Read" in result
        assert "Bash" in result

    def test_custom_globally_forbidden_replaces_default(self):
        """自定义 globally_forbidden 时，默认的 Agent 可能不再被禁止（若未列入）。"""
        result = resolve_allowed_tools(
            {"Read", "Agent", "Dangerous"},
            role_allow=None,
            role_deny=(),
            globally_forbidden=frozenset({"Dangerous"}),
        )
        assert "Dangerous" not in result
        assert "Agent" in result  # 自定义 globally_forbidden 不含 Agent → 不过滤

    def test_globally_forbidden_empty_frozenset(self):
        """globally_forbidden=frozenset() 时没有任何工具被全局禁止。"""
        result = resolve_allowed_tools(
            {"Read", "Agent"},
            role_allow=None,
            role_deny=(),
            globally_forbidden=frozenset(),
        )
        assert result == frozenset({"Read", "Agent"})

    def test_globally_forbidden_with_role_allow_still_blocks(self):
        """多个全局禁止工具 + role_allow 白名单：禁止工具仍不出现。"""
        result = resolve_allowed_tools(
            {"Read", "Agent", "AnotherTool", "Bash"},
            role_allow=("Agent", "AnotherTool", "Read"),
            role_deny=(),
            globally_forbidden=frozenset({"Agent", "AnotherTool"}),
        )
        assert "Agent" not in result
        assert "AnotherTool" not in result
        assert "Read" in result

    def test_frozenset_input_works(self):
        """all_tools 接受 frozenset 输入。"""
        result = resolve_allowed_tools(
            frozenset({"Read", "Write", "Agent"}),
            role_allow=None,
            role_deny=(),
        )
        assert "Agent" not in result
        assert "Read" in result
        assert "Write" in result
