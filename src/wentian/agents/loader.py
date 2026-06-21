"""v0.13 · C110 · F92/F93（任务 T139）— 子 Agent 角色四源加载器。

公开 API：

- ``parse_agent``：从含 ``---`` 围栏 YAML frontmatter 的 Markdown 文本解析出
  ``AgentDef``。缺 name 且无可用 name_hint、或解析失败 ⇒ 返回 None（跳过）。
- ``AgentRegistry``：name → AgentDef 字典封装；``get`` / ``list`` / ``add``。
- ``discover_agents``：扫描 builtin → plugin → user → project 四层（低→高），
  同名高层整体覆盖，返回 ``AgentRegistry``。

分层铁律：纯叶子模块，仅 stdlib（pathlib / importlib.resources）+
``wentian.frontmatter`` + ``wentian.agents.spec``——零 rich / prompt_toolkit /
后端 SDK，也不反向依赖 wentian.agent / wentian.repl / wentian.providers /
wentian.commands / wentian.skills。
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

from wentian.agents.spec import AgentDef
from wentian.frontmatter import parse_frontmatter


# ---------------------------------------------------------------------------
# AgentRegistry
# ---------------------------------------------------------------------------


class AgentRegistry:
    """name → AgentDef 字典；add 覆盖同名低层定义。"""

    def __init__(self) -> None:
        self._store: dict[str, AgentDef] = {}

    def add(self, agent_def: AgentDef) -> None:
        """添加（或覆盖同名）AgentDef。"""
        self._store[agent_def.name] = agent_def

    def get(self, name: str) -> AgentDef | None:
        """按 name 查找；不存在返回 None。"""
        return self._store.get(name)

    def list(self) -> list[AgentDef]:
        """返回所有 AgentDef，按 name 排序。"""
        return sorted(self._store.values(), key=lambda a: a.name)


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


def parse_agent(
    text: str,
    *,
    name_hint: str | None = None,
    source: str = "builtin",
) -> AgentDef | None:
    """从 Markdown 文本解析单个 AgentDef；无法解析返回 None。

    字段映射（frontmatter key → AgentDef field）：
    - ``name``            → name
    - ``description``     → description
    - ``tools``           → tools  (list → tuple | None if absent)
    - ``disallowed-tools``→ disallowed_tools  (list → tuple; default ())
    - ``model``           → model  (str; default "inherit")
    - ``max-turns``       → max_turns  (int; default None)
    - ``permission-mode`` → permission_mode  (str; default None)
    - body (后正文)        → body
    """
    try:
        data, body = parse_frontmatter(text)
    except Exception:
        return None

    # 无围栏时 parse_frontmatter 返回 ({}, 原文)；data 为空 dict 且 body==text。
    # 空文件 / 无 frontmatter 均返回 None。
    if not data and body == text:
        return None

    # name：frontmatter 优先，否则 name_hint
    raw_name = data.get("name")
    name_str = (
        raw_name.strip()
        if isinstance(raw_name, str) and raw_name.strip()
        else name_hint
    )
    if not name_str:
        return None

    # description
    description = data.get("description", "")
    if not isinstance(description, str):
        description = ""

    # tools：list → tuple | None if absent
    tools: tuple[str, ...] | None = None
    raw_tools = data.get("tools")
    if isinstance(raw_tools, (list, tuple)):
        tools = tuple(str(t) for t in raw_tools)

    # disallowed-tools：list → tuple; default ()
    disallowed_tools: tuple[str, ...] = ()
    raw_disallowed = data.get("disallowed-tools")
    if isinstance(raw_disallowed, (list, tuple)):
        disallowed_tools = tuple(str(t) for t in raw_disallowed)

    # model：str; default "inherit"
    model = "inherit"
    raw_model = data.get("model")
    if isinstance(raw_model, str) and raw_model.strip():
        model = raw_model.strip()

    # max-turns：int | None; default None
    max_turns: int | None = None
    raw_max_turns = data.get("max-turns")
    if raw_max_turns is not None:
        if isinstance(raw_max_turns, int):
            max_turns = raw_max_turns
        elif isinstance(raw_max_turns, str):
            try:
                max_turns = int(raw_max_turns.strip())
            except ValueError:
                max_turns = None

    # permission-mode：str | None; default None
    permission_mode: str | None = None
    raw_perm = data.get("permission-mode")
    if isinstance(raw_perm, str) and raw_perm.strip():
        permission_mode = raw_perm.strip()

    return AgentDef(
        name=name_str,
        description=description,
        body=body,
        tools=tools,
        disallowed_tools=disallowed_tools,
        model=model,
        max_turns=max_turns,
        permission_mode=permission_mode,
        source=source,
    )


# ---------------------------------------------------------------------------
# 发现
# ---------------------------------------------------------------------------


def _read_text(path: Path) -> str | None:
    """读文件文本；读失败返回 None（不抛）。"""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _load_layer(registry: AgentRegistry, layer_dir: Path | None, source: str) -> None:
    """扫描单层目录，把发现的 AgentDef 加入 registry（同名覆盖低层）。

    仅扫描 *.md 文件（单文件 agent）；name_hint 取自文件 stem。
    解析失败（parse_agent 返回 None / 读失败）的文件静默跳过，不中断其余发现。
    """
    if layer_dir is None:
        return
    try:
        if not layer_dir.is_dir():
            return
        entries = sorted(layer_dir.iterdir())
    except OSError:
        return

    for entry in entries:
        try:
            is_file = entry.is_file()
        except OSError:
            continue
        if not is_file:
            continue
        if entry.suffix != ".md":
            continue
        text = _read_text(entry)
        if text is None:
            continue
        try:
            agent = parse_agent(text, name_hint=entry.stem, source=source)
        except Exception:
            continue
        if agent is not None:
            registry.add(agent)


def _packaged_builtin_dir() -> Path | None:
    """返回打包内 ``wentian/agents/builtin/`` 目录路径；不存在则 None。"""
    try:
        resource = importlib.resources.files("wentian.agents") / "builtin"
        path = Path(str(resource))
    except (ModuleNotFoundError, FileNotFoundError, TypeError, OSError):
        return None
    try:
        return path if path.is_dir() else None
    except OSError:
        return None


def discover_agents(
    project_dir: Path | None,
    user_dir: Path | None,
    *,
    builtin_dir: Path | None = None,
    plugin_dir: Path | None = None,
) -> AgentRegistry:
    """扫描四层（低→高），同名高层整体覆盖，返回 AgentRegistry。

    优先级 高→低：project ▸ user ▸ plugin ▸ builtin

    - builtin 层：``builtin_dir`` 覆盖（测试用），否则取打包内
      ``wentian/agents/builtin/``；目录不存在则视作空（不崩溃）。
    - plugin 层：``plugin_dir`` 为 None 时跳过（无插件系统时默认传 None）。
    - 单文件 ``*.md`` ⇒ name_hint=文件名 stem。
    - 解析失败（parse_agent 返回 None / 读失败）的文件静默跳过，不中断其余发现。
    """
    registry = AgentRegistry()

    # 加载顺序：低优先级先加载，高优先级后加载（覆盖同名）
    builtin = builtin_dir if builtin_dir is not None else _packaged_builtin_dir()
    _load_layer(registry, builtin, "builtin")
    _load_layer(registry, plugin_dir, "plugin")
    _load_layer(registry, user_dir, "user")
    _load_layer(registry, project_dir, "project")

    return registry
