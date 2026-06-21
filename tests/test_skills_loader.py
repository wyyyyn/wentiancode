"""v0.11 · C101 · F73（任务 T127）— Skill 加载器（发现/解析/渲染）单元测试。

TDD 红-绿-重构：先跑此文件确认因 skills.loader 模块缺失而失败，再实现转绿。
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# parse_skill
# ---------------------------------------------------------------------------


def test_parse_skill_full_frontmatter():
    """完整 frontmatter（name/description/mode/allowed_tools/history）+ 正文 → 字段正确。"""
    from wentian.skills.base import SkillMode
    from wentian.skills.loader import parse_skill

    text = (
        "---\n"
        "name: commit\n"
        "description: 生成提交信息\n"
        "mode: isolated\n"
        "allowed_tools: [read_file]\n"
        "history: 2\n"
        "model: opus\n"
        "---\n"
        "# SOP\n"
        "干活 $ARGUMENTS\n"
    )
    skill = parse_skill(text, name_hint="ignored", source="project")
    assert skill is not None
    assert skill.name == "commit"
    assert skill.description == "生成提交信息"
    assert skill.mode is SkillMode.ISOLATED
    assert skill.allowed_tools == ("read_file",)
    assert skill.history == 2
    assert skill.model == "opus"
    assert skill.source == "project"
    # 正文保留占位符，frontmatter 已剥离
    assert "$ARGUMENTS" in skill.body
    assert "name: commit" not in skill.body
    assert skill.body.startswith("# SOP")


def test_parse_skill_name_hint_used_when_absent():
    """frontmatter 无 name 时，回退到 name_hint。"""
    from wentian.skills.loader import parse_skill

    text = "---\ndescription: 无名\n---\n正文\n"
    skill = parse_skill(text, name_hint="my-skill")
    assert skill is not None
    assert skill.name == "my-skill"


def test_parse_skill_frontmatter_name_wins():
    """frontmatter 的 name 优先于 name_hint。"""
    from wentian.skills.loader import parse_skill

    text = "---\nname: real\ndescription: d\n---\n正文\n"
    skill = parse_skill(text, name_hint="hint")
    assert skill is not None
    assert skill.name == "real"


def test_parse_skill_missing_name_returns_none():
    """既无 frontmatter name 又无 name_hint → None。"""
    from wentian.skills.loader import parse_skill

    text = "---\ndescription: 无名\n---\n正文\n"
    assert parse_skill(text, name_hint=None) is None


def test_parse_skill_no_frontmatter_returns_none():
    """缺 frontmatter 围栏 → None。"""
    from wentian.skills.loader import parse_skill

    assert parse_skill("# 只有正文，没有 frontmatter\n", name_hint="x") is None


def test_parse_skill_malformed_frontmatter_returns_none():
    """frontmatter 围栏未闭合（malformed）→ None。"""
    from wentian.skills.loader import parse_skill

    text = "---\nname: broken\ndescription: 没有闭合围栏\n正文紧跟\n"
    assert parse_skill(text, name_hint="x") is None


def test_parse_skill_defaults():
    """只给 name，其余走默认值。"""
    from wentian.skills.base import SkillMode
    from wentian.skills.loader import parse_skill

    text = "---\nname: bare\n---\n正文\n"
    skill = parse_skill(text)
    assert skill is not None
    assert skill.name == "bare"
    assert skill.description == ""
    assert skill.mode is SkillMode.SHARED
    assert skill.allowed_tools is None
    assert skill.history == 0
    assert skill.model is None


def test_parse_skill_block_list_allowed_tools():
    """allowed_tools 块状列表也解析成 tuple。"""
    from wentian.skills.loader import parse_skill

    text = "---\nname: t\nallowed_tools:\n  - read_file\n  - write_file\n---\n正文\n"
    skill = parse_skill(text)
    assert skill is not None
    assert skill.allowed_tools == ("read_file", "write_file")


# ---------------------------------------------------------------------------
# render_body
# ---------------------------------------------------------------------------


def test_render_body_arguments_and_positional():
    """$ARGUMENTS 取整串，$1/$2 取位置参数。"""
    from wentian.skills.loader import render_body

    assert render_body("a $ARGUMENTS b $1 $2", "fix typo") == "a fix typo b fix typo"


def test_render_body_missing_positional_empty():
    """无对应位置参数的占位符 → 空串。"""
    from wentian.skills.loader import render_body

    assert render_body("[$1][$2][$3]", "only") == "[only][][]"


def test_render_body_empty_args():
    """空 args：$ARGUMENTS 与所有位置参数都为空串。"""
    from wentian.skills.loader import render_body

    assert render_body("x$ARGUMENTSy$1z", "") == "xyz"


def test_render_body_multidigit_not_broken_by_single():
    """$10 不被 $1 替换破坏（有 10 个位置参数时 $10 取第 10 个）。"""
    from wentian.skills.loader import render_body

    args = "a b c d e f g h i j"
    # $1 -> a, $10 -> j
    assert render_body("$1 $10", args) == "a j"


def test_render_body_multidigit_missing_empty():
    """位置参数不足时多位占位符 → 空串，且不被单位替换误伤。"""
    from wentian.skills.loader import render_body

    # 只有 1 个参数：$1 -> only，$10 -> ""（不是 "only0"）
    assert render_body("$1|$10", "only") == "only|"


# ---------------------------------------------------------------------------
# discover_skills
# ---------------------------------------------------------------------------


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _skill_text(name, desc="d"):
    return f"---\nname: {name}\ndescription: {desc}\n---\n# {name} 正文\n"


def test_discover_empty_builtin_absent(tmp_path):
    """builtin_dir 指向不存在路径不崩溃，返回空 registry（项目/用户也空）。"""
    from wentian.skills.loader import discover_skills

    reg = discover_skills(
        project_dir=None,
        user_dir=None,
        builtin_dir=tmp_path / "no-such-builtin",
    )
    assert reg.list() == []


def test_discover_project_overrides_user(tmp_path):
    """项目层与用户层同名 → 项目层覆盖（高层胜）。"""
    from wentian.skills.loader import discover_skills

    user_dir = tmp_path / "user"
    project_dir = tmp_path / "project"
    _write(user_dir / "dup.md", _skill_text("dup", "用户版"))
    _write(project_dir / "dup.md", _skill_text("dup", "项目版"))

    reg = discover_skills(
        project_dir=project_dir,
        user_dir=user_dir,
        builtin_dir=tmp_path / "absent",
    )
    skill = reg.get("dup")
    assert skill is not None
    assert skill.description == "项目版"
    assert skill.source == "project"


def test_discover_bad_file_skipped_good_kept(tmp_path):
    """一层里坏文件被静默跳过，好文件正常发现。"""
    from wentian.skills.loader import discover_skills

    project_dir = tmp_path / "project"
    _write(project_dir / "good.md", _skill_text("good"))
    # 坏文件：没有 frontmatter 围栏
    _write(project_dir / "bad.md", "# 没有 frontmatter\n随便写\n")

    reg = discover_skills(
        project_dir=project_dir,
        user_dir=None,
        builtin_dir=tmp_path / "absent",
    )
    names = [s.name for s in reg.list()]
    assert names == ["good"]


def test_discover_single_file_and_directory_skill(tmp_path):
    """单文件 x.md 与目录 y/SKILL.md 都被发现；目录名作 name_hint。"""
    from wentian.skills.loader import discover_skills

    project_dir = tmp_path / "project"
    # 单文件
    _write(project_dir / "x.md", _skill_text("x"))
    # 目录技能：SKILL.md 无 name，回退到目录名 y
    _write(project_dir / "y" / "SKILL.md", "---\ndescription: 目录技能\n---\n正文\n")

    reg = discover_skills(
        project_dir=project_dir,
        user_dir=None,
        builtin_dir=tmp_path / "absent",
    )
    names = sorted(s.name for s in reg.list())
    assert names == ["x", "y"]


def test_discover_directory_with_tools_subdir_ok(tmp_path):
    """目录技能含 tools/ 子目录不崩溃；tools 内文件不被当 Skill 加载。"""
    from wentian.skills.loader import discover_skills

    project_dir = tmp_path / "project"
    _write(project_dir / "z" / "SKILL.md", _skill_text("z"))
    # tools/ 子目录里放一个 .md（不应被加载为独立 skill）
    _write(project_dir / "z" / "tools" / "helper.md", _skill_text("helper"))

    reg = discover_skills(
        project_dir=project_dir,
        user_dir=None,
        builtin_dir=tmp_path / "absent",
    )
    names = sorted(s.name for s in reg.list())
    assert names == ["z"]


def test_discover_returns_registry(tmp_path):
    """返回值是 SkillRegistry 实例。"""
    from wentian.skills.loader import discover_skills
    from wentian.skills.registry import SkillRegistry

    reg = discover_skills(
        project_dir=None,
        user_dir=None,
        builtin_dir=tmp_path / "absent",
    )
    assert isinstance(reg, SkillRegistry)
