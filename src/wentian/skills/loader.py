"""v0.11 · C101 · F73（任务 T127）— Skill 加载器：发现 / 解析 / 渲染（叶子模块）。

三个纯函数：

- ``parse_skill``：从一段含 ``---`` 围栏 YAML frontmatter 的 Markdown 文本解析出
  ``Skill``。frontmatter 仅手写解析（不引第三方 YAML 库）；缺 name 又无 name_hint、
  或围栏 malformed ⇒ 返回 None（跳过）。
- ``render_body``：把正文里的 ``$ARGUMENTS`` / ``$1`` … 占位符替换为实际入参。
- ``discover_skills``：扫描 builtin → user → project 三层（低→高），同名高层覆盖，
  返回 ``SkillRegistry``。

分层铁律：纯叶子模块，仅 stdlib（pathlib / re / importlib.resources）+ 同包
``wentian.skills.base`` / ``wentian.skills.registry`` import——零 rich /
prompt_toolkit / 后端 SDK，也不反向依赖 wentian.agent / wentian.repl /
wentian.providers / wentian.commands。
"""

from __future__ import annotations

import importlib.resources
import re
from pathlib import Path

from wentian.frontmatter import parse_frontmatter
from wentian.skills.base import Skill, SkillMode
from wentian.skills.registry import SkillRegistry

# 占位符正则：$ARGUMENTS 整体 或 $<digits>（多位数字一并捕获，避免 $1 误伤 $10）。
_PLACEHOLDER_RE = re.compile(r"\$ARGUMENTS|\$(\d+)")


def _has_frontmatter_fence(text: str) -> bool:
    """快速检测文本是否以有效 ``---`` 围栏开头（首行为 ``---`` 且后续存在闭合围栏）。

    这是 parse_skill 的前置守卫：用来区分「根本没有 frontmatter 围栏」（→ None）
    与「有围栏但 data 可能空/缺字段」两种情况，以保留 parse_skill 原有行为。
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return False
    return any(lines[i].strip() == "---" for i in range(1, len(lines)))


def parse_skill(
    text: str,
    *,
    name_hint: str | None = None,
    source: str = "builtin",
) -> Skill | None:
    """解析单个 Skill 文本 → Skill；无法解析（缺 name / malformed）返回 None。"""
    # 保留原有行为：无 frontmatter 围栏 → None（区别于"有围栏但缺字段"）。
    if not _has_frontmatter_fence(text):
        return None

    try:
        data, body = parse_frontmatter(text)
    except Exception:
        return None

    # name：frontmatter 优先，否则 name_hint
    name = data.get("name")
    name_str = name.strip() if isinstance(name, str) and name.strip() else name_hint
    if not name_str:
        return None

    description = data.get("description", "")
    if not isinstance(description, str):
        description = ""

    # mode
    mode = SkillMode.SHARED
    raw_mode = data.get("mode")
    if isinstance(raw_mode, str):
        try:
            mode = SkillMode(raw_mode.strip().lower())
        except ValueError:
            mode = SkillMode.SHARED

    # allowed_tools：tuple 或 None
    # parse_frontmatter 返回 list；兼容旧 tuple（如有）。
    allowed_tools: tuple[str, ...] | None = None
    raw_tools = data.get("allowed_tools")
    if isinstance(raw_tools, (list, tuple)):
        allowed_tools = tuple(str(t) for t in raw_tools)

    # history：int，默认 0
    history = 0
    raw_history = data.get("history")
    if isinstance(raw_history, str):
        try:
            history = int(raw_history.strip())
        except ValueError:
            history = 0

    # model：str|None
    model: str | None = None
    raw_model = data.get("model")
    if isinstance(raw_model, str) and raw_model.strip():
        model = raw_model.strip()

    return Skill(
        name=name_str,
        description=description,
        body=body,
        mode=mode,
        allowed_tools=allowed_tools,
        history=history,
        model=model,
        source=source,
    )


# ---------------------------------------------------------------------------
# 正文渲染
# ---------------------------------------------------------------------------


def render_body(body: str, args: str) -> str:
    """替换正文占位符：``$ARGUMENTS`` → 整串 args；``$N`` → 第 N 个位置参数。

    位置参数按空白切分；无对应参数的占位符替换为空串。单次正则 + 回调，
    确保 ``$10`` 不被 ``$1`` 破坏。
    """
    positionals = args.split()

    def _sub(match: re.Match[str]) -> str:
        digits = match.group(1)
        if digits is None:
            # $ARGUMENTS
            return args
        idx = int(digits)
        if 1 <= idx <= len(positionals):
            return positionals[idx - 1]
        return ""

    return _PLACEHOLDER_RE.sub(_sub, body)


# ---------------------------------------------------------------------------
# 发现
# ---------------------------------------------------------------------------


def _read_text(path: Path) -> str | None:
    """读文件文本；读失败返回 None（不抛）。"""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _load_layer(registry: SkillRegistry, layer_dir: Path | None, source: str) -> None:
    """扫描单层目录，把发现的 Skill 加入 registry（同名覆盖低层）。"""
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
            is_dir = entry.is_dir()
        except OSError:
            continue
        if is_dir:
            # 目录技能：仅当含 SKILL.md 时加载；tools/ 子目录被识别但不加载。
            skill_md = entry / "SKILL.md"
            try:
                has_skill = skill_md.is_file()
            except OSError:
                has_skill = False
            if not has_skill:
                continue
            text = _read_text(skill_md)
            if text is None:
                continue
            skill = parse_skill(text, name_hint=entry.name, source=source)
            if skill is not None:
                registry.add(skill)
        else:
            # 单文件技能：*.md
            if entry.suffix != ".md":
                continue
            text = _read_text(entry)
            if text is None:
                continue
            skill = parse_skill(text, name_hint=entry.stem, source=source)
            if skill is not None:
                registry.add(skill)


def _packaged_builtin_dir() -> Path | None:
    """返回打包内 ``wentian/skills/builtin/`` 目录路径；不存在则 None。"""
    try:
        resource = importlib.resources.files("wentian.skills") / "builtin"
        path = Path(str(resource))
    except (ModuleNotFoundError, FileNotFoundError, TypeError, OSError):
        return None
    try:
        return path if path.is_dir() else None
    except OSError:
        return None


def discover_skills(
    project_dir: Path | None,
    user_dir: Path | None,
    *,
    builtin_dir: Path | None = None,
) -> SkillRegistry:
    """扫描 builtin → user → project 三层（低→高），同名高层覆盖，返回 SkillRegistry。

    - builtin 层：``builtin_dir`` 覆盖（测试用），否则取打包内
      ``wentian/skills/builtin/``；目录不存在则视作空（不崩溃）。
    - 单文件 ``*.md`` ⇒ name_hint=文件名干；含 ``SKILL.md`` 的子目录 ⇒ 目录技能
      （name_hint=目录名，``tools/`` 子目录被识别但不加载/执行）。
    - 解析失败（parse_skill 返回 None / 读失败）的文件静默跳过，不中断其余发现。
    """
    registry = SkillRegistry()

    builtin = builtin_dir if builtin_dir is not None else _packaged_builtin_dir()
    _load_layer(registry, builtin, "builtin")
    _load_layer(registry, user_dir, "user")
    _load_layer(registry, project_dir, "project")

    return registry
