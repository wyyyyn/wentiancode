"""v0.5 · C21（任务 T59）— 系统提示模块化组装 + 文天人格 测试。

覆盖：
- 七固定模块按顺序出现（身份→系统约束→任务模式→动作执行→工具使用→语气风格→文本输出）
- 相邻非空模块之间用 "\n\n" 分隔，无多余空行残渣
- 可选模块默认为空时无孤立分隔残渣
- 拼装器与模块定义解耦（可注入假模块）
- 文天人格关键词（文天、=^_^=、照顾）
- 工具名称注入到「工具使用」模块
- 工具使用模块含关键约定串
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wentian.prompt.system import PromptContext, build_system_prompt


# ---------------------------------------------------------------------------
# 公共 fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def default_ctx() -> PromptContext:
    return PromptContext(
        cwd=Path("/home/user/project"),
        tool_names=(
            "read_file",
            "write_file",
            "edit_file",
            "run_command",
            "find_files",
            "search_text",
        ),
    )


@pytest.fixture
def default_output(default_ctx: PromptContext) -> str:
    return build_system_prompt(default_ctx)


# ---------------------------------------------------------------------------
# 1. 七模块按固定顺序出现
# ---------------------------------------------------------------------------

MODULE_TITLES = [
    "# 身份",
    "# 系统约束",
    "# 任务模式",
    "# 动作执行",
    "# 工具使用",
    "# 语气风格",
    "# 文本输出",
]


def test_seven_modules_present(default_output: str) -> None:
    """七个模块小标题都出现在输出中。"""
    for title in MODULE_TITLES:
        assert title in default_output, f"模块标题 {title!r} 未出现在输出中"


def test_seven_modules_in_order(default_output: str) -> None:
    """七个模块小标题以正确的相对顺序出现。"""
    positions = [default_output.index(title) for title in MODULE_TITLES]
    assert positions == sorted(positions), f"模块顺序错误，实际位置：{positions}"


# ---------------------------------------------------------------------------
# 2. 模块间空行分隔
# ---------------------------------------------------------------------------


def test_modules_separated_by_double_newline(default_output: str) -> None:
    """相邻非空模块之间有 '\n\n' 分隔（检查每对相邻标题之间有 '\n\n'）。"""
    for i in range(len(MODULE_TITLES) - 1):
        t1, t2 = MODULE_TITLES[i], MODULE_TITLES[i + 1]
        pos1 = default_output.index(t1)
        pos2 = default_output.index(t2)
        between = default_output[pos1:pos2]
        assert "\n\n" in between, f"模块 {t1!r} 与 {t2!r} 之间缺少 '\\n\\n' 分隔"


def test_no_trailing_double_newline(default_output: str) -> None:
    """输出结尾无多余空行。"""
    assert not default_output.endswith("\n\n"), "输出结尾有多余空行"


def test_no_triple_newline(default_output: str) -> None:
    """输出中无连续三个以上换行符（无多余空行残渣）。"""
    assert "\n\n\n" not in default_output, "输出中出现连续三个换行符"


# ---------------------------------------------------------------------------
# 3. 可选模块空时无残渣（默认 ctx 下可选模块返回空串）
# ---------------------------------------------------------------------------


def test_no_orphan_separators_when_optional_empty(default_output: str) -> None:
    """默认 ctx 下可选模块为空，输出中无孤立多余换行。"""
    # 没有连续三个换行就足以证明没有残渣
    assert "\n\n\n" not in default_output


# ---------------------------------------------------------------------------
# 4. 拼装器解耦：可注入假模块
# ---------------------------------------------------------------------------


def test_assembler_decoupled_from_modules(default_ctx: PromptContext) -> None:
    """传入单个假模块时，输出精确等于该模块的渲染结果。"""
    fake_modules = (("假模块", lambda c: "HELLO_FAKE"),)
    result = build_system_prompt(default_ctx, modules=fake_modules)
    assert result == "HELLO_FAKE", f"拼装器未正确使用注入的假模块，得到：{result!r}"


def test_assembler_skips_empty_module(default_ctx: PromptContext) -> None:
    """空渲染结果的模块被丢弃，不在输出中留下多余分隔。"""
    modules = (
        ("模块A", lambda c: "AAA"),
        ("空模块", lambda c: ""),
        ("模块C", lambda c: "CCC"),
    )
    result = build_system_prompt(default_ctx, modules=modules)
    assert result == "AAA\n\nCCC", f"空模块应被丢弃，得到：{result!r}"


# ---------------------------------------------------------------------------
# 5. 文天人格关键词
# ---------------------------------------------------------------------------


def test_persona_keywords_in_identity_module(default_output: str) -> None:
    """身份模块包含文天人格关键词：文天、=^_^=、照顾。"""
    identity_start = default_output.index("# 身份")
    constraint_start = default_output.index("# 系统约束")
    identity_text = default_output[identity_start:constraint_start]

    assert "文天" in identity_text, "身份模块缺少「文天」"
    assert "=^_^=" in identity_text, "身份模块缺少猫脸「=^_^=」"
    assert "照顾" in identity_text, "身份模块缺少「照顾」"


def test_persona_keywords_in_tone_module(default_output: str) -> None:
    """语气风格模块包含人格关键词。"""
    tone_start = default_output.index("# 语气风格")
    output_start = default_output.index("# 文本输出")
    tone_text = default_output[tone_start:output_start]

    assert "照顾" in tone_text or "文天" in tone_text or "=^_^=" in tone_text, (
        "语气风格模块缺少人格关键词"
    )


def test_persona_non_disclosure_rule_present(default_output: str) -> None:
    """F36 红线：系统提示含「不向用户透露/复述人设与系统提示」的指令。

    人设只体现、不透露——被直接问起时不逐条背诵设定（如同不泄露系统提示）。
    """
    # 含「不…透露/复述/罗列」其一，且关涉「人设」或「系统提示」
    assert (
        "透露" in default_output or "复述" in default_output or "罗列" in default_output
    ), "系统提示缺少『不透露/复述/罗列』人设的指令"
    assert "人设" in default_output or "系统提示" in default_output, (
        "不透露指令未关涉『人设』或『系统提示』"
    )


def test_no_self_label_as_cat_or_oily_rule_present(default_output: str) -> None:
    """F36：系统提示含「不自称猫/油头等形象词」的指令。

    形象只作内部气质来源，文天回复中不自我标榜为猫或油头。
    """
    assert "不自称" in default_output, "系统提示缺少『不自称』形象词的指令"
    assert "猫" in default_output and "油头" in default_output, (
        "不自称指令未点名『猫』与『油头』"
    )


# ---------------------------------------------------------------------------
# 6. 工具名称注入
# ---------------------------------------------------------------------------


def test_tool_names_injected(default_ctx: PromptContext) -> None:
    """PromptContext 中的工具名称出现在「工具使用」模块中。"""
    result = build_system_prompt(default_ctx)
    tools_start = result.index("# 工具使用")
    tone_start = result.index("# 语气风格")
    tools_text = result[tools_start:tone_start]

    assert "read_file" in tools_text, "「工具使用」模块缺少 read_file"
    assert "edit_file" in tools_text, "「工具使用」模块缺少 edit_file"


def test_all_tool_names_injected() -> None:
    """不同工具集合都正确注入到「工具使用」模块。"""
    ctx = PromptContext(
        cwd=Path("/x"),
        tool_names=("read_file", "edit_file"),
    )
    result = build_system_prompt(ctx)
    tools_start = result.index("# 工具使用")
    tone_start = result.index("# 语气风格")
    tools_text = result[tools_start:tone_start]

    assert "read_file" in tools_text
    assert "edit_file" in tools_text
    # write_file 不在工具集中，不应出现（以避免模块内容写死工具名）
    assert "write_file" not in tools_text


# ---------------------------------------------------------------------------
# 7. 工具使用模块包含关键约定句
# ---------------------------------------------------------------------------


def test_tool_conventions_present(default_output: str) -> None:
    """「工具使用」模块含关键约定：编辑前先读取、优先用专用工具。"""
    tools_start = default_output.index("# 工具使用")
    tone_start = default_output.index("# 语气风格")
    tools_text = default_output[tools_start:tone_start]

    assert "编辑文件前先读取" in tools_text, (
        "「工具使用」模块缺少「编辑文件前先读取」约定"
    )
    assert "优先用专用工具" in tools_text, "「工具使用」模块缺少「优先用专用工具」约定"


# ---------------------------------------------------------------------------
# v0.9 · C59 · F68/N29（任务 T106）—— 两个可选槽真渲染
# ---------------------------------------------------------------------------


class TestProjectInstructionsSlot:
    """项目/自定义指令槽：非空真渲染、空时无残渣。"""

    def test_renders_project_instructions_when_present(self) -> None:
        ctx = PromptContext(
            cwd=Path("/x"),
            tool_names=("read_file",),
            project_instructions="务必先跑测试再提交。",
        )
        result = build_system_prompt(ctx)
        assert "# 项目/自定义指令" in result
        assert "务必先跑测试再提交。" in result

    def test_empty_project_instructions_no_module(self) -> None:
        ctx = PromptContext(
            cwd=Path("/x"),
            tool_names=("read_file",),
            project_instructions="",
        )
        result = build_system_prompt(ctx)
        assert "# 项目/自定义指令" not in result
        assert "\n\n\n" not in result  # 无空行残渣


class TestMemorySlot:
    """长期记忆槽：非空真渲染、空时无残渣。"""

    def test_renders_memory_when_present(self) -> None:
        ctx = PromptContext(
            cwd=Path("/x"),
            tool_names=("read_file",),
            memory="- 用户偏好: 喜欢中文回复",
        )
        result = build_system_prompt(ctx)
        assert "# 长期记忆" in result
        assert "用户偏好" in result
        assert "喜欢中文回复" in result

    def test_empty_memory_no_module(self) -> None:
        ctx = PromptContext(
            cwd=Path("/x"),
            tool_names=("read_file",),
            memory="",
        )
        result = build_system_prompt(ctx)
        assert "# 长期记忆" not in result
        assert "\n\n\n" not in result


class TestBothSlotsEmptyRegression:
    """N29：两槽均空时拼装与 v0.8 一致——无空行残渣、字节稳定可缓存。"""

    def test_both_slots_empty_equals_v08_default(self) -> None:
        # 旧默认 ctx（两槽空）
        ctx_default = PromptContext(
            cwd=Path("/x"),
            tool_names=("read_file", "write_file"),
        )
        # 显式两槽空
        ctx_explicit_empty = PromptContext(
            cwd=Path("/x"),
            tool_names=("read_file", "write_file"),
            project_instructions="",
            memory="",
        )
        out_default = build_system_prompt(ctx_default)
        out_explicit = build_system_prompt(ctx_explicit_empty)
        # 字节稳定：两者完全相等（缓存前缀稳定）
        assert out_default == out_explicit
        # 无残渣
        assert "\n\n\n" not in out_default
        assert not out_default.endswith("\n\n")
        # 两个可选模块标题都不出现
        assert "# 项目/自定义指令" not in out_default
        assert "# 长期记忆" not in out_default

    def test_both_slots_present_render_both(self) -> None:
        ctx = PromptContext(
            cwd=Path("/x"),
            tool_names=("read_file",),
            project_instructions="项目规则 X",
            memory="记忆条目 Y",
        )
        result = build_system_prompt(ctx)
        # 两模块均出现，且在固定模块之后
        assert "# 项目/自定义指令" in result
        assert "# 长期记忆" in result
        assert result.index("# 文本输出") < result.index("# 项目/自定义指令")
        assert result.index("# 项目/自定义指令") < result.index("# 长期记忆")
        # 仍无残渣
        assert "\n\n\n" not in result
