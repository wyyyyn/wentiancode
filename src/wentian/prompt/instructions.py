"""v0.9 · C53 · F63（任务 T98）— 项目指令三层加载 + @include 内联展开。

叶子模块：仅依赖 stdlib（``pathlib`` / ``os`` / ``sys`` / ``re``）。
绝不 import ``prompt.system`` / provider / agent / registry / permissions。

职责
----
启动时从三处读取手写 Markdown 指令文件，按优先级**高在前**拼接，注入系统提示的
「项目/自定义指令」模块：

1. ``<cwd>/.wentian/WENTIAN.md``           （项目本地覆盖，最高、放最前）
2. ``<cwd>/WENTIAN.md``                     （项目根、团队共享，次之）
3. ``<user_home>/.config/wentian/WENTIAN.md``（用户全局，最低、放最后）

每层可选、缺失静默跳过；三层全缺 ⇒ 返回 ``""``。

独占一行的 ``@include <相对路径>`` 触发把目标文件内容内联展开（相对「包含它的
文件所在目录」解析）。三道护栏：

- **限深**：嵌套深度超 :data:`DEFAULT_INCLUDE_DEPTH`（默认 5）停止展开 + 告警。
- **visited 防环**：同一文件在一条 include 链上重复出现即跳过 + 告警，不无限递归。
- **越界拦截**：先 ``os.path.realpath`` 解析符号链接、再前缀比对项目根（``cwd``）；
  落在项目根外或绝对路径越界 ⇒ 拒绝该 include + 告警、不读取（与 v0.6 N11 沙箱同规、
  防软链逃逸）。

拼接后总体积超 :data:`DEFAULT_MAX_BYTES` ⇒ 按上限截断 + 告警。

所有异常情形（缺失 / 越界 / 超限 / 读失败）只告警 stderr、绝不抛。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

__all__ = [
    "DEFAULT_INCLUDE_DEPTH",
    "DEFAULT_MAX_BYTES",
    "load_project_instructions",
    "expand_includes",
]

# 模块级默认常量（本任务不读 config.py；cfg 形参留作未来注入）。
DEFAULT_INCLUDE_DEPTH = 5
DEFAULT_MAX_BYTES = 64 * 1024  # 64 KiB，防撑爆上下文

# 独占一行的 ``@include <相对路径>``：允许首尾空白，路径段不含空白。
_INCLUDE_RE = re.compile(r"^[ \t]*@include[ \t]+(\S+)[ \t]*$")


def _warn(msg: str) -> None:
    """统一的 stderr 告警（沿用 session.py 的 ``[wentian] Warning:`` 风格）。"""
    print(f"[wentian] Warning: {msg}", file=sys.stderr)


def _within_root(resolved: Path, project_root: Path) -> bool:
    """先解析符号链接（已由调用方 resolve），再做前缀比对。

    ``resolved`` 须等于 ``project_root`` 或为其后代。与 v0.6 N11 沙箱同规。
    """
    if resolved == project_root:
        return True
    return project_root in resolved.parents


def expand_includes(
    text: str,
    base_dir: Path,
    project_root: Path,
    *,
    depth: int = 0,
    visited: frozenset[Path] = frozenset(),
    max_depth: int = DEFAULT_INCLUDE_DEPTH,
) -> str:
    """把独占一行的 ``@include <rel>`` 内联展开。

    Parameters
    ----------
    text:
        待展开文本。
    base_dir:
        ``@include`` 相对路径的解析基准（即包含它的文件所在目录）。
    project_root:
        项目根（越界判定前缀）。
    depth:
        当前递归深度。
    visited:
        当前 include 链上已访问的真实路径集合（防环）。
    max_depth:
        最大嵌套深度，超过即停止展开并告警。

    Returns
    -------
    str
        展开后的文本（行尾换行结构保持原样）。
    """
    out_lines: list[str] = []
    for line in text.splitlines():
        m = _INCLUDE_RE.match(line)
        if m is None:
            out_lines.append(line)
            continue

        rel = m.group(1)
        # 先解析符号链接（realpath）再前缀比对，防软链逃逸。
        target = Path(os.path.realpath(base_dir / rel))

        if not _within_root(target, project_root):
            _warn(f"@include '{rel}' resolves outside project root; refusing to read.")
            continue

        if target in visited:
            _warn(f"@include cycle detected at '{rel}'; skipping.")
            continue

        if depth >= max_depth:
            _warn(
                f"@include depth limit ({max_depth}) exceeded at '{rel}'; "
                "stopping expansion."
            )
            continue

        try:
            included = target.read_text(encoding="utf-8")
        except OSError as exc:
            _warn(f"@include '{rel}' could not be read: {exc}")
            continue

        out_lines.append(
            expand_includes(
                included,
                target.parent,
                project_root,
                depth=depth + 1,
                visited=visited | {target},
                max_depth=max_depth,
            )
        )

    return "\n".join(out_lines)


def _read_layer(path: Path, project_root: Path, max_depth: int) -> str | None:
    """读取一层指令文件并展开其 ``@include``；缺失/读失败 ⇒ None（静默跳过）。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return expand_includes(raw, path.parent, project_root, max_depth=max_depth)


def load_project_instructions(
    cwd: Path,
    *,
    user_home: Path | None = None,
    cfg=None,  # noqa: ANN001 — 未来注入位（波次四接 MemoryConfig），本任务走默认
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_depth: int = DEFAULT_INCLUDE_DEPTH,
) -> str:
    """读三层 ``WENTIAN.md``、按优先级高在前拼接、内联 ``@include``、体积上限截断。

    Parameters
    ----------
    cwd:
        当前工作目录（兼项目根，``@include`` 越界判定前缀）。
    user_home:
        用户主目录；缺省走 :meth:`Path.home`。
    cfg:
        未来注入位（保留），本任务走模块级默认常量。
    max_bytes:
        拼接后总体积上限（字节）。超限按上限截断并告警。
    max_depth:
        ``@include`` 最大嵌套深度。

    Returns
    -------
    str
        拼装好的项目指令文本；三层全缺 ⇒ ``""``。
    """
    cwd = Path(cwd)
    project_root = Path(os.path.realpath(cwd))
    if user_home is None:
        user_home = Path.home()

    layers = (
        cwd / ".wentian" / "WENTIAN.md",  # 项目本地覆盖（最高）
        cwd / "WENTIAN.md",  # 项目根
        user_home / ".config" / "wentian" / "WENTIAN.md",  # 用户全局（最低）
    )

    parts: list[str] = []
    for path in layers:
        text = _read_layer(path, project_root, max_depth)
        if text is not None and text != "":
            parts.append(text)

    joined = "\n\n".join(parts)

    encoded = joined.encode("utf-8")
    if len(encoded) > max_bytes:
        _warn(f"project instructions exceed {max_bytes} bytes; truncating.")
        # 按字节截断后回退到合法的 UTF-8 边界。
        joined = encoded[:max_bytes].decode("utf-8", errors="ignore")

    return joined
