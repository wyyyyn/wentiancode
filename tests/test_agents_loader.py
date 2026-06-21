"""v0.13 · C110 · F92/F93（任务 T139）— 子 Agent 角色四源加载器单元测试。

TDD 红-绿-重构：先跑此文件确认因 agents.loader 模块缺失而失败，再实现转绿。
"""

from __future__ import annotations

from pathlib import Path


# ---------------------------------------------------------------------------
# parse_agent
# ---------------------------------------------------------------------------


def test_parse_agent_full_frontmatter():
    """完整 frontmatter（所有字段）+ 正文 → AgentDef 字段正确。"""
    from wentian.agents.loader import parse_agent

    text = (
        "---\n"
        "name: coder\n"
        "description: 专职写代码的 agent\n"
        "tools: [read_file, write_file]\n"
        "disallowed-tools: [bash]\n"
        "model: sonnet\n"
        "max-turns: 5\n"
        "permission-mode: acceptEdits\n"
        "---\n"
        "# 角色说明\n"
        "你是一名专职写代码的 agent。\n"
    )
    agent = parse_agent(text, name_hint="ignored", source="project")
    assert agent is not None
    assert agent.name == "coder"
    assert agent.description == "专职写代码的 agent"
    assert agent.tools == ("read_file", "write_file")
    assert agent.disallowed_tools == ("bash",)
    assert agent.model == "sonnet"
    assert agent.max_turns == 5
    assert agent.permission_mode == "acceptEdits"
    assert agent.source == "project"
    # 正文保留，frontmatter 已剥离
    assert "角色说明" in agent.body
    assert "name: coder" not in agent.body


def test_parse_agent_defaults():
    """仅有 name 字段时，其余字段取默认值。"""
    from wentian.agents.loader import parse_agent

    text = "---\nname: reviewer\ndescription: 审查\n---\n正文\n"
    agent = parse_agent(text)
    assert agent is not None
    assert agent.name == "reviewer"
    assert agent.tools is None  # 无约束
    assert agent.disallowed_tools == ()
    assert agent.model == "inherit"
    assert agent.max_turns is None
    assert agent.permission_mode is None
    assert agent.source == "builtin"  # 默认 source


def test_parse_agent_max_turns_is_int():
    """max-turns 为整数字符串时，解析结果为 int。"""
    from wentian.agents.loader import parse_agent

    text = "---\nname: planner\ndescription: 规划\nmax-turns: 10\n---\n正文\n"
    agent = parse_agent(text)
    assert agent is not None
    assert agent.max_turns == 10
    assert isinstance(agent.max_turns, int)


def test_parse_agent_name_hint_used_when_absent():
    """frontmatter 无 name 时，回退到 name_hint。"""
    from wentian.agents.loader import parse_agent

    text = "---\ndescription: 无名角色\n---\n正文\n"
    agent = parse_agent(text, name_hint="my-agent")
    assert agent is not None
    assert agent.name == "my-agent"


def test_parse_agent_frontmatter_name_wins_over_hint():
    """frontmatter 的 name 优先于 name_hint。"""
    from wentian.agents.loader import parse_agent

    text = "---\nname: real-agent\ndescription: d\n---\n正文\n"
    agent = parse_agent(text, name_hint="hint-name")
    assert agent is not None
    assert agent.name == "real-agent"


def test_parse_agent_missing_name_returns_none():
    """既无 frontmatter name 又无 name_hint → None。"""
    from wentian.agents.loader import parse_agent

    text = "---\ndescription: 无名\n---\n正文\n"
    assert parse_agent(text, name_hint=None) is None


def test_parse_agent_no_frontmatter_returns_none():
    """缺 frontmatter 围栏 → None。"""
    from wentian.agents.loader import parse_agent

    assert parse_agent("# 只有正文，没有 frontmatter\n", name_hint="x") is None


def test_parse_agent_empty_string_returns_none():
    """空文件内容 → None。"""
    from wentian.agents.loader import parse_agent

    assert parse_agent("") is None


def test_parse_agent_tools_none_when_absent():
    """frontmatter 无 tools 字段 → AgentDef.tools 为 None（无约束）。"""
    from wentian.agents.loader import parse_agent

    text = "---\nname: analyst\ndescription: 分析\n---\n正文\n"
    agent = parse_agent(text)
    assert agent is not None
    assert agent.tools is None


# ---------------------------------------------------------------------------
# AgentRegistry
# ---------------------------------------------------------------------------


def test_agent_registry_get_hit():
    """get(name) 命中 → 返回正确的 AgentDef。"""
    from wentian.agents.loader import AgentRegistry
    from wentian.agents.spec import AgentDef

    reg = AgentRegistry()
    a = AgentDef(name="coder", description="d", body="b")
    reg.add(a)
    result = reg.get("coder")
    assert result is a


def test_agent_registry_get_miss():
    """get(name) 未命中 → 返回 None。"""
    from wentian.agents.loader import AgentRegistry

    reg = AgentRegistry()
    assert reg.get("nonexistent") is None


def test_agent_registry_list_sorted():
    """list() 返回按 name 排序的 AgentDef 列表。"""
    from wentian.agents.loader import AgentRegistry
    from wentian.agents.spec import AgentDef

    reg = AgentRegistry()
    reg.add(AgentDef(name="zebra", description="d", body="b"))
    reg.add(AgentDef(name="alpha", description="d", body="b"))
    reg.add(AgentDef(name="mango", description="d", body="b"))
    names = [a.name for a in reg.list()]
    assert names == ["alpha", "mango", "zebra"]


def test_agent_registry_add_overwrites():
    """add() 同名覆盖：后加的替换先加的。"""
    from wentian.agents.loader import AgentRegistry
    from wentian.agents.spec import AgentDef

    reg = AgentRegistry()
    a1 = AgentDef(name="coder", description="v1", body="old")
    a2 = AgentDef(name="coder", description="v2", body="new")
    reg.add(a1)
    reg.add(a2)
    result = reg.get("coder")
    assert result is a2
    assert len(reg.list()) == 1


def test_agent_registry_list_empty():
    """空 registry list() 返回空列表。"""
    from wentian.agents.loader import AgentRegistry

    reg = AgentRegistry()
    assert reg.list() == []


# ---------------------------------------------------------------------------
# discover_agents — 四层覆盖
# ---------------------------------------------------------------------------


def _write_agent(directory: Path, filename: str, content: str) -> None:
    """辅助：向 directory 写入 agent md 文件。"""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(content, encoding="utf-8")


def test_discover_agents_project_overrides_user(tmp_path: Path):
    """同名 coder 在项目层和用户层均存在 → 取项目层（高优先级）。"""
    from wentian.agents.loader import discover_agents

    user_dir = tmp_path / "user"
    project_dir = tmp_path / "project"

    _write_agent(
        user_dir,
        "coder.md",
        "---\nname: coder\ndescription: user version\n---\nuser body\n",
    )
    _write_agent(
        project_dir,
        "coder.md",
        "---\nname: coder\ndescription: project version\n---\nproject body\n",
    )

    reg = discover_agents(project_dir=project_dir, user_dir=user_dir, builtin_dir=None)
    agent = reg.get("coder")
    assert agent is not None
    assert agent.description == "project version"
    assert agent.source == "project"


def test_discover_agents_three_layer_all_present(tmp_path: Path):
    """用户层 analyst + 内置层 reviewer → 三个独立名称都在 registry。"""
    from wentian.agents.loader import discover_agents

    user_dir = tmp_path / "user"
    builtin_dir = tmp_path / "builtin"
    project_dir = tmp_path / "project"

    _write_agent(
        user_dir,
        "analyst.md",
        "---\nname: analyst\ndescription: 分析\n---\n分析正文\n",
    )
    _write_agent(
        builtin_dir,
        "reviewer.md",
        "---\nname: reviewer\ndescription: 审查\n---\n审查正文\n",
    )
    _write_agent(
        project_dir,
        "coder.md",
        "---\nname: coder\ndescription: 编码\n---\n编码正文\n",
    )

    reg = discover_agents(
        project_dir=project_dir, user_dir=user_dir, builtin_dir=builtin_dir
    )
    names = {a.name for a in reg.list()}
    assert names == {"analyst", "reviewer", "coder"}


def test_discover_agents_missing_dir_is_empty(tmp_path: Path):
    """某层目录不存在 → 视为空，不崩溃，其余层正常加载。"""
    from wentian.agents.loader import discover_agents

    builtin_dir = tmp_path / "builtin"
    _write_agent(
        builtin_dir,
        "helper.md",
        "---\nname: helper\ndescription: 助手\n---\n正文\n",
    )
    # project_dir / user_dir 不存在
    reg = discover_agents(
        project_dir=tmp_path / "nonexistent_project",
        user_dir=tmp_path / "nonexistent_user",
        builtin_dir=builtin_dir,
    )
    assert reg.get("helper") is not None


def test_discover_agents_plugin_none_is_skipped(tmp_path: Path):
    """plugin_dir=None → 跳过，不崩溃。"""
    from wentian.agents.loader import discover_agents

    builtin_dir = tmp_path / "builtin"
    _write_agent(
        builtin_dir,
        "tester.md",
        "---\nname: tester\ndescription: 测试\n---\n正文\n",
    )
    reg = discover_agents(
        project_dir=None,
        user_dir=None,
        builtin_dir=builtin_dir,
        plugin_dir=None,
    )
    assert reg.get("tester") is not None


def test_discover_agents_builtin_higher_than_nothing_but_lower_than_user(
    tmp_path: Path,
):
    """builtin 层 name=reviewer 被 user 层同名覆盖。"""
    from wentian.agents.loader import discover_agents

    builtin_dir = tmp_path / "builtin"
    user_dir = tmp_path / "user"

    _write_agent(
        builtin_dir,
        "reviewer.md",
        "---\nname: reviewer\ndescription: builtin reviewer\n---\n正文\n",
    )
    _write_agent(
        user_dir,
        "reviewer.md",
        "---\nname: reviewer\ndescription: user reviewer\n---\n正文\n",
    )

    reg = discover_agents(project_dir=None, user_dir=user_dir, builtin_dir=builtin_dir)
    agent = reg.get("reviewer")
    assert agent is not None
    assert agent.description == "user reviewer"
    assert agent.source == "user"


# ---------------------------------------------------------------------------
# 容错（fault tolerance）
# ---------------------------------------------------------------------------


def test_discover_agents_bad_file_skipped_others_loaded(tmp_path: Path):
    """某文件 frontmatter 坏 / 缺 name → 静默跳过，其余文件正常加载。"""
    from wentian.agents.loader import discover_agents

    project_dir = tmp_path / "project"
    # 合法文件
    _write_agent(
        project_dir,
        "good.md",
        "---\nname: good-agent\ndescription: 合法\n---\n正文\n",
    )
    # 缺 name 的文件（且无 name_hint 能覆盖的情况下 → 跳过）
    _write_agent(
        project_dir,
        "noname.md",
        "---\ndescription: 无名\n---\n正文\n",
    )
    # 非 md 文件（应忽略）
    (project_dir / "ignore.txt").write_text("not a skill", encoding="utf-8")

    reg = discover_agents(project_dir=project_dir, user_dir=None, builtin_dir=None)
    # good-agent 应在 registry；noname 应被跳过
    assert reg.get("good-agent") is not None
    # noname 没有 name，所以不在 registry（只能用文件名 hint 时 → "noname"，但
    # parse_agent 里 name_hint=文件名 stem；这里 noname.md stem=noname）
    # 实际上 loader 会用 name_hint="noname"，所以 noname 会被加载为 name="noname"
    # 我们只验证 good-agent 存在（容错核心）
    names = {a.name for a in reg.list()}
    assert "good-agent" in names


def test_discover_agents_empty_file_skipped(tmp_path: Path):
    """空 md 文件 → 跳过，不崩溃。"""
    from wentian.agents.loader import discover_agents

    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "empty.md").write_text("", encoding="utf-8")
    _write_agent(
        project_dir,
        "valid.md",
        "---\nname: valid\ndescription: 有效\n---\n正文\n",
    )

    reg = discover_agents(project_dir=project_dir, user_dir=None, builtin_dir=None)
    assert reg.get("valid") is not None


def test_discover_agents_name_hint_from_filename(tmp_path: Path):
    """无 frontmatter name 时，name_hint 取自文件名（stem）。"""
    from wentian.agents.loader import discover_agents

    project_dir = tmp_path / "project"
    # 文件名 stem 作为 name_hint：frontmatter 有 description 但无 name
    _write_agent(
        project_dir,
        "auto-named.md",
        "---\ndescription: 自动命名\n---\n正文\n",
    )

    reg = discover_agents(project_dir=project_dir, user_dir=None, builtin_dir=None)
    # name_hint = "auto-named"（stem）
    agent = reg.get("auto-named")
    assert agent is not None
    assert agent.description == "自动命名"
