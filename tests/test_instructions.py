"""v0.9 · C53 · F63（任务 T98）— 项目指令三层加载 + @include 测试。

覆盖：
- 三层 WENTIAN.md 按优先级高在前拼接、段间清晰分隔
- 缺层静默跳过；三层全缺 → 返回 ""
- @include 独占一行内联展开、相对包含文件目录解析、行内混文不触发
- 限深（默认 5）超限停止 + 告警、不抛
- visited 集合防环（a→b→a 跳过 + 告警、不无限递归）
- 越界拦截（../ 逃逸 / 绝对路径 / 软链接指向项目外，先 realpath 再前缀比对）
- 拼接后总体积超上限按上限截断 + 告警

全部用 tmp_path 真实造三层目录 + include 链 + os.symlink，绝不读写真实 home。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from wentian.prompt.instructions import (
    DEFAULT_INCLUDE_DEPTH,
    DEFAULT_MAX_BYTES,
    load_project_instructions,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _local_override(cwd: Path) -> Path:
    p = cwd / ".wentian" / "WENTIAN.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _project_root_file(cwd: Path) -> Path:
    return cwd / "WENTIAN.md"


def _user_global(user_home: Path) -> Path:
    p = user_home / ".config" / "wentian" / "WENTIAN.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# 三层加载与优先级
# ---------------------------------------------------------------------------


def test_three_layers_priority_high_first(tmp_path: Path) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    user_home = tmp_path / "home"
    user_home.mkdir()

    _local_override(cwd).write_text("LOCAL_OVERRIDE", encoding="utf-8")
    _project_root_file(cwd).write_text("PROJECT_ROOT", encoding="utf-8")
    _user_global(user_home).write_text("USER_GLOBAL", encoding="utf-8")

    out = load_project_instructions(cwd, user_home=user_home)

    # 三段都在
    assert "LOCAL_OVERRIDE" in out
    assert "PROJECT_ROOT" in out
    assert "USER_GLOBAL" in out
    # 高优先级在前：local < project < user 的位置严格递增
    assert (
        out.index("LOCAL_OVERRIDE")
        < out.index("PROJECT_ROOT")
        < out.index("USER_GLOBAL")
    )
    # 段间有清晰分隔（空行）
    assert "\n\n" in out


def test_missing_layers_skipped_silently(tmp_path: Path) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    user_home = tmp_path / "home"
    user_home.mkdir()

    # 只放项目根一层
    _project_root_file(cwd).write_text("ONLY_PROJECT", encoding="utf-8")

    out = load_project_instructions(cwd, user_home=user_home)
    assert out == "ONLY_PROJECT"


def test_all_missing_returns_empty(tmp_path: Path) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    user_home = tmp_path / "home"
    user_home.mkdir()

    out = load_project_instructions(cwd, user_home=user_home)
    assert out == ""


def test_user_home_defaults_to_path_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # user_home 缺省走 Path.home()；用 monkeypatch 把 home 指向 tmp，绝不碰真实 home
    cwd = tmp_path / "proj"
    cwd.mkdir()
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    _user_global(fake_home).write_text("FROM_HOME", encoding="utf-8")

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    out = load_project_instructions(cwd)
    assert "FROM_HOME" in out


# ---------------------------------------------------------------------------
# @include 内联展开
# ---------------------------------------------------------------------------


def test_include_inlines_target_content(tmp_path: Path) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    user_home = tmp_path / "home"
    user_home.mkdir()

    (cwd / "snippet.md").write_text("INCLUDED_BODY", encoding="utf-8")
    _project_root_file(cwd).write_text(
        "HEAD\n@include snippet.md\nTAIL", encoding="utf-8"
    )

    out = load_project_instructions(cwd, user_home=user_home)
    assert "HEAD" in out
    assert "INCLUDED_BODY" in out
    assert "TAIL" in out
    # @include 行本身被替换，不残留指令文本
    assert "@include snippet.md" not in out


def test_include_resolves_relative_to_including_file_dir(tmp_path: Path) -> None:
    # 被包含文件中的 @include 相对它自己所在目录解析
    cwd = tmp_path / "proj"
    cwd.mkdir()
    user_home = tmp_path / "home"
    user_home.mkdir()

    sub = cwd / "docs"
    sub.mkdir()
    (sub / "a.md").write_text("@include b.md", encoding="utf-8")
    (sub / "b.md").write_text("DEEP_NESTED", encoding="utf-8")
    _project_root_file(cwd).write_text("@include docs/a.md", encoding="utf-8")

    out = load_project_instructions(cwd, user_home=user_home)
    assert "DEEP_NESTED" in out


def test_inline_include_with_extra_text_not_triggered(tmp_path: Path) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    user_home = tmp_path / "home"
    user_home.mkdir()

    (cwd / "snippet.md").write_text("SHOULD_NOT_APPEAR", encoding="utf-8")
    # 行内混有其它文字 → 不触发
    _project_root_file(cwd).write_text(
        "see @include snippet.md for details", encoding="utf-8"
    )

    out = load_project_instructions(cwd, user_home=user_home)
    assert "SHOULD_NOT_APPEAR" not in out
    assert "see @include snippet.md for details" in out


# ---------------------------------------------------------------------------
# 限深
# ---------------------------------------------------------------------------


def test_depth_limit_stops_and_warns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    user_home = tmp_path / "home"
    user_home.mkdir()

    # 造一条长链 root -> f0 -> f1 -> ... 超过默认深度 5
    n = DEFAULT_INCLUDE_DEPTH + 3
    for i in range(n):
        nxt = f"@include f{i + 1}.md" if i + 1 < n else "CHAIN_END"
        (cwd / f"f{i}.md").write_text(nxt, encoding="utf-8")
    _project_root_file(cwd).write_text("@include f0.md", encoding="utf-8")

    out = load_project_instructions(cwd, user_home=user_home)
    # 没有无限递归、函数正常返回
    # 链尾未被展开（超深停止）
    assert "CHAIN_END" not in out
    err = capsys.readouterr().err
    assert err  # 有 stderr 告警


# ---------------------------------------------------------------------------
# visited 防环
# ---------------------------------------------------------------------------


def test_cycle_detection_no_infinite_recursion(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    user_home = tmp_path / "home"
    user_home.mkdir()

    # a -> b -> a 环
    (cwd / "a.md").write_text("A_BODY\n@include b.md", encoding="utf-8")
    (cwd / "b.md").write_text("B_BODY\n@include a.md", encoding="utf-8")
    _project_root_file(cwd).write_text("@include a.md", encoding="utf-8")

    out = load_project_instructions(cwd, user_home=user_home)
    # 不无限递归（能正常返回即证明）；两段内容各出现
    assert "A_BODY" in out
    assert "B_BODY" in out
    err = capsys.readouterr().err
    assert err  # 环被检测并告警


# ---------------------------------------------------------------------------
# 越界拦截
# ---------------------------------------------------------------------------


def test_include_outside_root_via_relative_blocked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    user_home = tmp_path / "home"
    user_home.mkdir()

    # 项目根外的敏感文件
    (tmp_path / "secret.md").write_text("SECRET_OUTSIDE", encoding="utf-8")
    _project_root_file(cwd).write_text("@include ../secret.md", encoding="utf-8")

    out = load_project_instructions(cwd, user_home=user_home)
    assert "SECRET_OUTSIDE" not in out
    err = capsys.readouterr().err
    assert err  # 越界被拦截并告警


def test_include_absolute_path_outside_root_blocked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    user_home = tmp_path / "home"
    user_home.mkdir()

    secret = tmp_path / "abs_secret.md"
    secret.write_text("ABS_SECRET", encoding="utf-8")
    _project_root_file(cwd).write_text(f"@include {secret}", encoding="utf-8")

    out = load_project_instructions(cwd, user_home=user_home)
    assert "ABS_SECRET" not in out
    assert capsys.readouterr().err


def test_include_symlink_escape_blocked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # 先 realpath 解析符号链接、再前缀比对：软链指向项目外被拦
    cwd = tmp_path / "proj"
    cwd.mkdir()
    user_home = tmp_path / "home"
    user_home.mkdir()

    outside = tmp_path / "outside.md"
    outside.write_text("LINKED_OUTSIDE", encoding="utf-8")

    link = cwd / "link.md"
    os.symlink(outside, link)

    _project_root_file(cwd).write_text("@include link.md", encoding="utf-8")

    out = load_project_instructions(cwd, user_home=user_home)
    assert "LINKED_OUTSIDE" not in out
    assert capsys.readouterr().err


def test_include_within_root_allowed(tmp_path: Path) -> None:
    # 控制组：项目根内的 include 正常展开（确认拦截不误伤）
    cwd = tmp_path / "proj"
    cwd.mkdir()
    user_home = tmp_path / "home"
    user_home.mkdir()

    inner = cwd / "sub"
    inner.mkdir()
    (inner / "ok.md").write_text("INSIDE_OK", encoding="utf-8")
    _project_root_file(cwd).write_text("@include sub/ok.md", encoding="utf-8")

    out = load_project_instructions(cwd, user_home=user_home)
    assert "INSIDE_OK" in out


# ---------------------------------------------------------------------------
# 体积上限
# ---------------------------------------------------------------------------


def test_total_volume_truncated_at_limit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cwd = tmp_path / "proj"
    cwd.mkdir()
    user_home = tmp_path / "home"
    user_home.mkdir()

    big = "X" * 5000
    _project_root_file(cwd).write_text(big, encoding="utf-8")

    out = load_project_instructions(cwd, user_home=user_home, max_bytes=1000)
    assert len(out.encode("utf-8")) <= 1000
    assert capsys.readouterr().err  # 截断告警


def test_default_max_bytes_is_positive() -> None:
    assert isinstance(DEFAULT_MAX_BYTES, int)
    assert DEFAULT_MAX_BYTES > 0


def test_read_failure_is_silent_warning_not_raise(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # @include 指向不存在的项目内文件 → 告警不抛，其余内容仍返回
    cwd = tmp_path / "proj"
    cwd.mkdir()
    user_home = tmp_path / "home"
    user_home.mkdir()

    _project_root_file(cwd).write_text(
        "BEFORE\n@include nope.md\nAFTER", encoding="utf-8"
    )

    out = load_project_instructions(cwd, user_home=user_home)
    assert "BEFORE" in out
    assert "AFTER" in out
    assert capsys.readouterr().err
