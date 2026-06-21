# 模型记忆 + 可切换主题 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户启动时选过的 provider 下次自动沿用（不再弹选择器），并把硬编码配色收敛成可切换主题（朱砂默认 / 墨青 / 素白），config 选 + 运行时 `/theme` 切换，均持久化。

**Architecture:** 新增统一 `state.py` 持久化层（`$XDG_STATE_HOME/wentian/state.yaml`，与 config 分离）持有 `last_provider` + `theme`，二者均覆盖各自 config 默认。新增 `ui/theme.py` 定义 `Theme` 语义色 + 三套内置主题。`build_app` 纯函数新增注入参数承接 remembered/persist/force_pick + 解析出的 `Theme`；I/O 边界在 typer `main()` 读写 state。颜色由 DI 把 `Theme` 穿进各 UI 组件，模块级 cinnabar 常量保留作兜底。

**Tech Stack:** Python 3.12+、dataclasses、PyYAML、Rich、prompt_toolkit、typer、pytest。

## Global Constraints

- **不进大版本**：折进 v0.11 线，完成时打 patch（如 `0.11.1`），不重跑四件套大 spec。
- **TDD 红-绿-重构**：没有先失败的测试 → 不写生产代码（铁律，补丁不豁免）。
- **完成前验证**：全套测试绿 + 真实终端截图（三主题品味闸）+ PTY 冒烟，缺一不可。
- **默认零回归**：默认主题 `cinnabar` == 今日色值；所有新注入参数带 None/False 默认 → 缺省即字节级 v0.11 行为。
- **安全降级**：state / 未知主题 / config 可选块解析失败 → 回退默认，**绝不抛异常崩溃**（沿用代码库 `_parse_block` 模式）。
- **原子写**：state 落盘用 temp + `os.replace`（沿用 memory INDEX 写法）。
- **提交语言**：中文 commit message；**NEVER** 加 `Co-Authored-By`。
- **唯一事实来源**：`spec/patches/2026-06-21-model-persistence-and-themes.md`。

---

### Task 1: `state.py` — 统一运行时状态持久化层

**Files:**
- Create: `src/wentian/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: nothing（叶子模块，不依赖项目内其它模块）。
- Produces:
  - `@dataclass(frozen=True) RuntimeState(last_provider: str | None = None, theme: str | None = None)`
  - `default_state_path() -> Path`
  - `load_state(path: Path | None = None) -> RuntimeState`
  - `save_state(state: RuntimeState, path: Path | None = None) -> None`
  - `save_last_provider(name: str, path: Path | None = None) -> None`
  - `save_theme(name: str, path: Path | None = None) -> None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_state.py
import os
from pathlib import Path

import pytest

from wentian.state import (
    RuntimeState,
    default_state_path,
    load_state,
    save_last_provider,
    save_state,
    save_theme,
)


def test_load_missing_file_returns_empty(tmp_path):
    assert load_state(tmp_path / "nope.yaml") == RuntimeState()


def test_load_corrupt_file_returns_empty(tmp_path):
    p = tmp_path / "state.yaml"
    p.write_text("{[ this : is : not : valid", encoding="utf-8")
    assert load_state(p) == RuntimeState()


def test_load_non_mapping_returns_empty(tmp_path):
    p = tmp_path / "state.yaml"
    p.write_text("- just\n- a\n- list\n", encoding="utf-8")
    assert load_state(p) == RuntimeState()


def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "sub" / "state.yaml"
    save_state(RuntimeState(last_provider="deepseek", theme="ink"), p)
    assert load_state(p) == RuntimeState(last_provider="deepseek", theme="ink")


def test_save_last_provider_preserves_theme(tmp_path):
    p = tmp_path / "state.yaml"
    save_state(RuntimeState(last_provider="a", theme="ink"), p)
    save_last_provider("b", p)
    assert load_state(p) == RuntimeState(last_provider="b", theme="ink")


def test_save_theme_preserves_provider(tmp_path):
    p = tmp_path / "state.yaml"
    save_state(RuntimeState(last_provider="a", theme="ink"), p)
    save_theme("plain", p)
    assert load_state(p) == RuntimeState(last_provider="a", theme="plain")


def test_unknown_fields_ignored(tmp_path):
    p = tmp_path / "state.yaml"
    p.write_text("last_provider: x\nbogus: 1\n", encoding="utf-8")
    assert load_state(p) == RuntimeState(last_provider="x")


def test_default_path_prefers_xdg_state_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert default_state_path() == tmp_path / "wentian" / "state.yaml"


def test_default_path_falls_back_to_home(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert default_state_path() == tmp_path / ".local" / "state" / "wentian" / "state.yaml"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'wentian.state'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/wentian/state.py
"""Patch（2026-06-21）— 统一运行时状态持久化层。

与 ``config.yaml`` 分离的小状态文件，记住跨启动该沿用的运行期选择：
``last_provider``（上次用的 provider）与 ``theme``（上次切的主题）。读失败一律
安全降级为空状态，写失败 best-effort（绝不阻断启动/运行）。路径沿用
``ui/input.py`` 的 ``$XDG_STATE_HOME`` 模式。
"""

from __future__ import annotations

import os
import warnings
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import yaml

__all__ = [
    "RuntimeState",
    "default_state_path",
    "load_state",
    "save_state",
    "save_last_provider",
    "save_theme",
]


@dataclass(frozen=True)
class RuntimeState:
    """跨启动持久化的运行期选择（全部可选，缺省 None）。"""

    last_provider: str | None = None
    theme: str | None = None


def default_state_path() -> Path:
    """``$XDG_STATE_HOME/wentian/state.yaml``，回退 ``~/.local/state/wentian/state.yaml``。"""
    xdg = os.environ.get("XDG_STATE_HOME", "")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "wentian" / "state.yaml"


def _resolve(path: Path | None) -> Path:
    return path if path is not None else default_state_path()


def load_state(path: Path | None = None) -> RuntimeState:
    """读取状态文件；缺失/损坏/非映射 → 空 :class:`RuntimeState`，绝不抛异常。"""
    p = _resolve(path)
    try:
        if not p.exists():
            return RuntimeState()
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 状态文件损坏不该崩，安全降级
        return RuntimeState()
    if not isinstance(raw, dict):
        return RuntimeState()
    lp = raw.get("last_provider")
    th = raw.get("theme")
    return RuntimeState(
        last_provider=lp if isinstance(lp, str) else None,
        theme=th if isinstance(th, str) else None,
    )


def save_state(state: RuntimeState, path: Path | None = None) -> None:
    """原子写整份状态（temp + ``os.replace``）。写失败仅警告、不抛。"""
    p = _resolve(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(
            yaml.safe_dump(asdict(state), allow_unicode=True, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp, p)
    except Exception as exc:  # noqa: BLE001 — best-effort，绝不阻断
        warnings.warn(f"无法写入状态文件 {p}: {exc}", stacklevel=2)


def save_last_provider(name: str, path: Path | None = None) -> None:
    """读-改-写：只更新 ``last_provider``，不丢 ``theme``。"""
    save_state(replace(load_state(path), last_provider=name), path)


def save_theme(name: str, path: Path | None = None) -> None:
    """读-改-写：只更新 ``theme``，不丢 ``last_provider``。"""
    save_state(replace(load_state(path), theme=name), path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_state.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Lint + commit**

```bash
uvx ruff check src/wentian/state.py tests/test_state.py
uvx ruff format src/wentian/state.py tests/test_state.py
git add src/wentian/state.py tests/test_state.py
git commit -m "[patch] state.py — 统一运行时状态持久化层（last_provider + theme，XDG_STATE，原子写、安全降级）"
```

---

### Task 2: `ui/theme.py` — Theme 语义色 + 三套内置主题

**Files:**
- Create: `src/wentian/ui/theme.py`
- Test: `tests/test_theme.py`

**Interfaces:**
- Consumes: nothing（叶子模块）。
- Produces:
  - `@dataclass(frozen=True) Theme(name, accent, success, error, warning, muted)` + `@property face_style -> str`
  - `THEMES: dict[str, Theme]`（键含 `"cinnabar"`, `"ink"`, `"plain"`）
  - `DEFAULT_THEME_NAME = "cinnabar"`
  - `get_theme(name: str | None) -> Theme`

**Note:** `ink` / `plain` 的精确色值是占位（见下方代码注释），**实现期由 Task 8 的真实终端截图品味闸敲定**，但默认 `cinnabar` 必须 == 今日色值 `#C84B31` + green/red/yellow。

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_theme.py
import warnings

import pytest

from wentian.ui.theme import (
    DEFAULT_THEME_NAME,
    THEMES,
    Theme,
    get_theme,
)


def test_three_builtin_themes_present():
    assert set(THEMES) >= {"cinnabar", "ink", "plain"}


def test_cinnabar_is_default_and_preserves_today_colors():
    assert DEFAULT_THEME_NAME == "cinnabar"
    c = THEMES["cinnabar"]
    assert c.accent == "#C84B31"
    assert c.success == "green"
    assert c.error == "red"
    assert c.warning == "yellow"


def test_face_style_derived_from_accent():
    assert THEMES["cinnabar"].face_style == "bold #C84B31"


def test_get_theme_known_returns_that_theme():
    assert get_theme("ink") is THEMES["ink"]


def test_get_theme_unknown_returns_default_with_warning():
    with pytest.warns(UserWarning):
        assert get_theme("does-not-exist") is THEMES["cinnabar"]


def test_get_theme_none_returns_default():
    assert get_theme(None) is THEMES["cinnabar"]


def test_theme_is_frozen():
    with pytest.raises(Exception):
        THEMES["cinnabar"].accent = "x"  # type: ignore[misc]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_theme.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'wentian.ui.theme'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/wentian/ui/theme.py
"""Patch（2026-06-21）— 可切换主题。

把散落的内联色收敛成语义化 :class:`Theme`。内置三套「水墨东方调」：
``cinnabar`` 朱砂（默认，== 今日色值，零视觉变化基线）/ ``ink`` 墨青（冷青绿）/
``plain`` 素白（克制灰阶）。未知名 → 安全降级回默认 + 警告。

ink / plain 的精确色值经真实终端截图品味闸敲定（见 plan Task 8）。
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

__all__ = ["Theme", "THEMES", "DEFAULT_THEME_NAME", "get_theme"]


@dataclass(frozen=True)
class Theme:
    """一套终端配色的语义槽位（Rich 样式字符串）。"""

    name: str
    accent: str  # 品牌强调：mascot 脸 / ❯ 提示符 / ⏺ 工具标记 / 命令名 / 标题
    success: str  # 工具结果「成功」
    error: str  # 工具结果「失败」（多配 bold）
    warning: str  # 工具结果「已拒绝」/ 会话提示
    muted: str  # 次要信息（多数处仍用 "dim" 修饰；此槽供需要具体色处）

    @property
    def face_style(self) -> str:
        """mascot 脸样式 = ``bold <accent>``（沿用 mascot.FACE_STYLE 形态）。"""
        return f"bold {self.accent}"


DEFAULT_THEME_NAME = "cinnabar"

THEMES: dict[str, Theme] = {
    # 朱砂——默认，逐字节等于今日色值，零视觉变化、零回归基线。
    "cinnabar": Theme(
        name="cinnabar",
        accent="#C84B31",
        success="green",
        error="red",
        warning="yellow",
        muted="grey58",
    ),
    # 墨青——冷青绿东方调。色值占位，截图品味闸敲定。
    "ink": Theme(
        name="ink",
        accent="#3A8A7D",
        success="#5E8B7E",
        error="#A8443B",
        warning="#C8975A",
        muted="grey58",
    ),
    # 素白——克制灰阶/近无彩，低色终端友好。色值占位，截图品味闸敲定。
    "plain": Theme(
        name="plain",
        accent="grey85",
        success="grey70",
        error="#A8443B",
        warning="grey62",
        muted="grey50",
    ),
}


def get_theme(name: str | None) -> Theme:
    """按名取主题；未知/None → 默认 ``cinnabar``（未知名额外 ``warn``）。"""
    if name is None:
        return THEMES[DEFAULT_THEME_NAME]
    theme = THEMES.get(name)
    if theme is None:
        warnings.warn(
            f"未知主题 '{name}'，回退默认 '{DEFAULT_THEME_NAME}'。"
            f"可选：{sorted(THEMES)}",
            stacklevel=2,
        )
        return THEMES[DEFAULT_THEME_NAME]
    return theme
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_theme.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Lint + commit**

```bash
uvx ruff check src/wentian/ui/theme.py tests/test_theme.py
uvx ruff format src/wentian/ui/theme.py tests/test_theme.py
git add src/wentian/ui/theme.py tests/test_theme.py
git commit -m "[patch] ui/theme.py — Theme 语义色 + 三套内置主题（朱砂默认零变化 / 墨青 / 素白）"
```

---

### Task 3: config.py — 可选 `theme:` 顶层键

**Files:**
- Modify: `src/wentian/config.py:192-208`（`Config` dataclass）、`src/wentian/config.py:453-462`（`_build_config_from_raw` 返回块）
- Test: `tests/test_config_theme.py`

**Interfaces:**
- Consumes: nothing。
- Produces: `Config.theme: str`（默认 `"cinnabar"`）。

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config_theme.py
from wentian.config import _build_config_from_raw

_BASE = {
    "providers": {"a": {"protocol": "anthropic", "model": "m", "api_key": "k"}},
    "default": "a",
}


def test_missing_theme_defaults_to_cinnabar():
    cfg = _build_config_from_raw(dict(_BASE))
    assert cfg.theme == "cinnabar"


def test_explicit_theme_passes_through():
    cfg = _build_config_from_raw({**_BASE, "theme": "ink"})
    assert cfg.theme == "ink"


def test_unknown_theme_not_rejected_at_config_layer():
    # config 层不校验主题名（留给 get_theme 安全降级）
    cfg = _build_config_from_raw({**_BASE, "theme": "bogus"})
    assert cfg.theme == "bogus"


def test_non_string_theme_falls_back_to_cinnabar():
    cfg = _build_config_from_raw({**_BASE, "theme": 123})
    assert cfg.theme == "cinnabar"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config_theme.py -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'theme'`

- [ ] **Step 3: Write minimal implementation**

In `src/wentian/config.py`, add the field to the `Config` dataclass (after the `hooks` field at line 208):

```python
    # Patch（2026-06-21）—— 可切换主题名；缺失/非字符串 → "cinnabar"。
    theme: str = "cinnabar"
```

In `_build_config_from_raw`, change the `return Config(...)` block to pass `theme`:

```python
    raw_theme = raw.get("theme")
    theme = raw_theme if isinstance(raw_theme, str) else "cinnabar"

    return Config(
        providers=providers,
        default=raw["default"],
        mcp_servers=mcp_servers,
        context=context,
        memory=memory,
        sessions=sessions,
        skills=skills,
        hooks=hooks,
        theme=theme,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config_theme.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Regression + commit**

```bash
uv run pytest tests/test_config.py -q
uvx ruff check src/wentian/config.py tests/test_config_theme.py
git add src/wentian/config.py tests/test_config_theme.py
git commit -m "[patch] config — 可选 theme: 顶层键（默认 cinnabar，非串安全降级，config 层不校验名）"
```

---

### Task 4: `build_app` — provider 解析顺序 + remembered/persist/force_pick

**Files:**
- Modify: `src/wentian/cli.py:290-306`（`build_app` 签名）、`src/wentian/cli.py:376-386`（provider 解析块）
- Test: `tests/test_build_app_provider_memory.py`

**Interfaces:**
- Consumes: `Config`（Task 3 的 `theme` 不在本任务用）。
- Produces: `build_app(..., remembered_provider: str | None = None, persist_provider: Callable[[str], None] | None = None, force_pick: bool = False)` 的解析行为。

**解析顺序**（覆盖现有 376-383 块）：
1. `provider_name`（来自 `-p`）非 None → 用它，**不 persist**。
2. 否则 `remembered_provider` 且 ∈ `config.providers` 且非 `force_pick` → 用它，**跳过选择器**。
3. 否则 `len(providers) > 1` 且 `provider_selector` 非 None → 弹选择器（default 取 remembered-or-`config.default`）→ 选中后 `persist_provider(选中)`。
4. 否则 → `config.default`（`provider_name` 留 None，由 `config.get(None)` 取默认）。

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_build_app_provider_memory.py
from pathlib import Path

import pytest
import yaml

from wentian.cli import build_app


def _write_config(tmp_path: Path, providers: dict, default: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(
        yaml.safe_dump({"providers": providers, "default": default}),
        encoding="utf-8",
    )
    return p


_TWO = {
    "a": {"protocol": "anthropic", "model": "m", "api_key": "k"},
    "b": {"protocol": "anthropic", "model": "m2", "api_key": "k2"},
}


def _build(tmp_path, **kw):
    cfg = _write_config(tmp_path, _TWO, "a")
    return build_app(
        config_path=cfg,
        sessions_dir=tmp_path / "sessions",
        show_banner=False,
        **kw,
    )


def test_remembered_valid_skips_selector(tmp_path):
    calls = []
    selector = lambda names, default: (calls.append(("sel", names, default)) or "a")
    repl = _build(tmp_path, remembered_provider="b", provider_selector=selector)
    assert calls == []  # 选择器没被调用
    assert repl._provider.name == "b"


def test_remembered_invalid_falls_through_to_selector(tmp_path):
    calls = []
    selector = lambda names, default: (calls.append(default) or "a")
    repl = _build(tmp_path, remembered_provider="ghost", provider_selector=selector)
    assert calls == ["a"]  # 无效 remembered 被忽略 → 弹选择器，default=config.default
    assert repl._provider.name == "a"


def test_dash_p_overrides_and_does_not_persist(tmp_path):
    persisted = []
    repl = _build(
        tmp_path,
        provider_name="b",
        remembered_provider="a",
        persist_provider=persisted.append,
    )
    assert repl._provider.name == "b"
    assert persisted == []  # -p 一次性，不写 state


def test_force_pick_shows_selector_despite_remembered(tmp_path):
    calls = []
    selector = lambda names, default: (calls.append(default) or "a")
    repl = _build(
        tmp_path, remembered_provider="b", force_pick=True, provider_selector=selector
    )
    assert calls == ["b"]  # force_pick → 弹选择器，default 预选 remembered
    assert repl._provider.name == "a"


def test_selector_choice_is_persisted(tmp_path):
    persisted = []
    selector = lambda names, default: "b"
    repl = _build(tmp_path, provider_selector=selector, persist_provider=persisted.append)
    assert repl._provider.name == "b"
    assert persisted == ["b"]  # 选择器选定 → 写 state


def test_default_path_unchanged_when_no_new_args(tmp_path):
    # 缺省（无 remembered/persist/selector）→ 用 config.default，回归安全
    repl = _build(tmp_path)
    assert repl._provider.name == "a"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_build_app_provider_memory.py -v`
Expected: FAIL — `build_app() got an unexpected keyword argument 'remembered_provider'`

- [ ] **Step 3: Write minimal implementation**

Add three params to the `build_app` signature (in the keyword-only block, after `provider_selector`):

```python
    remembered_provider: str | None = None,
    persist_provider: Callable[[str], None] | None = None,
    force_pick: bool = False,
```

Replace the provider-resolution block (`src/wentian/cli.py:376-386`):

```python
    # 2. Provider — Patch（2026-06-21）记忆上次选择：
    #    -p 一次性覆盖（不 persist）> remembered（跳选择器）> 选择器（写 state）> default。
    if provider_name is not None:
        pass  # -p 显式覆盖，transient
    elif (
        remembered_provider is not None
        and remembered_provider in config.providers
        and not force_pick
    ):
        provider_name = remembered_provider  # 跳过选择器
    elif len(config.providers) > 1 and provider_selector is not None:
        preselect = (
            remembered_provider
            if remembered_provider in config.providers
            else config.default
        )
        provider_name = provider_selector(list(config.providers), preselect)
        if persist_provider is not None:
            persist_provider(provider_name)
    # else: provider_name 留 None → config.get(None) 取 default（静默）

    provider_cfg = config.get(provider_name)  # uses default when None
    provider = create_provider(provider_cfg)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_build_app_provider_memory.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Regression + commit**

```bash
uv run pytest tests/ -k "build_app or cli" -q
uvx ruff check src/wentian/cli.py tests/test_build_app_provider_memory.py
git add src/wentian/cli.py tests/test_build_app_provider_memory.py
git commit -m "[patch] build_app — provider 解析记忆上次选择（-p 一次性 / remembered 跳选 / 选定写 state / --pick 强制）"
```

---

### Task 5: REPL.switch_provider — 切换时回写 state

**Files:**
- Modify: `src/wentian/repl.py:254-316`（`__init__` 加 `persist_provider` 注入 + 存字段）、`src/wentian/repl.py:1008-1018`（`switch_provider`）
- Test: `tests/test_repl_provider_persist.py`

**Interfaces:**
- Consumes: 无新依赖。
- Produces: `REPL(..., persist_provider: Callable[[str], None] | None = None)`；切换成功后调用它。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_repl_provider_persist.py
from tests.helpers import make_repl  # 见下方说明，若无则用项目既有 REPL 构造夹具


def test_switch_provider_persists_on_success(repl_factory):
    persisted = []
    repl = repl_factory(persist_provider=persisted.append)
    repl.switch_provider("b")  # repl_factory 的 provider_factory 支持 "b"
    assert persisted == ["b"]


def test_switch_provider_does_not_persist_on_failure(repl_factory):
    persisted = []
    repl = repl_factory(persist_provider=persisted.append)
    repl.switch_provider("nonexistent")  # provider_factory 对未知名抛异常
    assert persisted == []
```

> **实现者注**：本项目已有大量 REPL 构造测试（见 `tests/test_repl_*.py`）。复用其中既有的 REPL 构造方式造 `repl_factory` fixture：`provider_factory` 对 `"b"` 返回桩 provider、对未知名 `raise ValueError`。把 `persist_provider` 透传进 `REPL(...)`。**不要新建平行夹具体系**——照抄最近的 `test_repl_*.py` 构造法。

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_repl_provider_persist.py -v`
Expected: FAIL — `REPL.__init__() got an unexpected keyword argument 'persist_provider'`

- [ ] **Step 3: Write minimal implementation**

Add to `REPL.__init__` keyword-only params (near `commands`/`memory_store`):

```python
        # Patch（2026-06-21）— provider 切换回写 state 的回调；None ⇒ 不持久化
        # （回归安全，测试可注入 list.append 断言）。
        persist_provider: Callable[[str], None] | None = None,
```

And store it in `__init__` body (alongside other `self._...` assignments):

```python
        self._persist_provider = persist_provider
```

Modify `switch_provider` (after the successful `self._store.save(...)`/before the success print):

```python
    def switch_provider(self, name: str) -> None:
        """按名称切换 provider，打印切换成功/失败，并回写 state（Patch 2026-06-21）。"""
        try:
            new_provider = self._provider_factory(name)
            self._provider = new_provider
            self._session.provider = new_provider.name
            self._store.save(self._session)
            self._update_compactor_provider(new_provider)
            if self._persist_provider is not None:
                self._persist_provider(new_provider.name)
            self._console.print(f"[green]已切换 provider：{name}[/green]")
        except Exception as exc:
            self._console.print(f"[red]切换 provider 失败：{exc}[/red]")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_repl_provider_persist.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Regression + commit**

```bash
uv run pytest tests/ -k "repl" -q
uvx ruff check src/wentian/repl.py tests/test_repl_provider_persist.py
git add src/wentian/repl.py tests/test_repl_provider_persist.py
git commit -m "[patch] REPL.switch_provider — 切换成功回写 state.last_provider（失败不写）"
```

---

### Task 6: 颜色集中 — Theme 经 DI 穿进各 UI 组件

**Files:**
- Modify: `src/wentian/render.py`（8 处色）、`src/wentian/ui/banner.py`（4 处）、`src/wentian/ui/mascot.py`（FACE_STYLE）、`src/wentian/ui/confirm.py`（3 处）、`src/wentian/ui/select.py`（1 处）、`src/wentian/ui/input.py`（1 处 ❯）、`src/wentian/repl.py`（`_CINNABAR` 等内联色）、`src/wentian/cli.py`（穿线）
- Test: `tests/test_theme_threading.py`

**Interfaces:**
- Consumes: `Theme`（Task 2）。
- Produces: 各组件接受注入的 `Theme`；缺省回退模块级 cinnabar 常量（兜底，零回归）。

**穿线原则**（DRY、最小签名扰动）：
- 各组件**新增可选 `theme: Theme | None = None`**（kw-only），内部 `t = theme or _DEFAULT`，其中 `_DEFAULT = THEMES["cinnabar"]`（模块级，import 自 `ui.theme`）。把内联 `"#C84B31"` / `"green"` / `"red"` / `"yellow"` 换成 `t.accent` / `t.success` / `t.error` / `t.warning`。
- `build_app` 解析 `theme = get_theme(state_theme or config.theme)` 后用 `theme=theme` 注入 Renderer / banner / confirm_fn / select / PromptInput / REPL。
- `mascot.FACE_STYLE` 保留为模块常量（cinnabar，兜底）；`build_banner` 改用 `theme.face_style`。

**断言策略**：注入 accent 为 sentinel 值（如 `#0000FF`）的 `Theme`，断言该色出现在渲染输出里——证明 theme 真的流到了渲染点，而非读死常量。

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_theme_threading.py
import io

from rich.console import Console
from rich.text import Text

from wentian.ui.banner import build_banner
from wentian.ui.theme import Theme

_SENTINEL = Theme(
    name="t", accent="#0000FF", success="#00FF00", error="#FF0000",
    warning="#FFFF00", muted="grey58",
)


def _render(renderable) -> str:
    buf = io.StringIO()
    Console(file=buf, force_terminal=True, color_system="truecolor", width=80).print(
        renderable
    )
    return buf.getvalue()


def test_banner_uses_injected_theme_accent():
    grid = build_banner(
        version="0.11.1", provider_name="prov", model="m",
        session_id="sid", resumed=False, theme=_SENTINEL,
    )
    out = _render(grid)
    # sentinel 蓝在 truecolor 下编码为 38;2;0;0;255
    assert "0;0;255" in out


def test_banner_defaults_to_cinnabar_when_no_theme():
    grid = build_banner(
        version="0.11.1", provider_name="prov", model="m",
        session_id="sid", resumed=False,
    )
    out = _render(grid)
    # 默认仍是朱砂 #C84B31 → 200;75;49
    assert "200;75;49" in out
```

> **实现者注**：对 `render.py`（工具标记 ⏺ 用 accent、结果 ⎿ 用 success/error/warning）与 `confirm.py`（标题用 accent）各补一条同款 sentinel 断言；模式同上（注入 sentinel theme → 断言色码出现）。`select.py` 仅 `reverse` 无品牌色，可只加「不崩 + 接受 theme 参数」的烟囱测试。

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_theme_threading.py -v`
Expected: FAIL — `build_banner() got an unexpected keyword argument 'theme'`

- [ ] **Step 3: Write minimal implementation**

`ui/banner.py` — 加 `theme` 参数：

```python
from wentian.ui.theme import THEMES, Theme

_DEFAULT = THEMES["cinnabar"]

def build_banner(*, version, provider_name, model, session_id, resumed,
                 theme: Theme | None = None):
    t = theme or _DEFAULT
    info = Text()
    info.append("文天 WentianCode", style="bold")
    info.append(f" v{version}", style="dim")
    info.append("\n")
    info.append(provider_name, style=t.accent)
    if model:
        info.append(" · ", style="dim")
        info.append(model)
    info.append("\n")
    label = "已恢复" if resumed else "新会话"
    info.append(f"{label} ", style="dim")
    info.append(session_id, style="italic dim")
    face = Text(TEXT_FACES[0], style=t.face_style)
    grid = Table.grid(padding=(0, 2))
    grid.add_column(no_wrap=True)
    grid.add_column()
    grid.add_row(face, info)
    return grid
```

对 `render.py` / `confirm.py` / `select.py` / `input.py` / `repl.py` 同法：各加 `theme: Theme | None = None`（kw-only）+ `t = theme or _DEFAULT`，内联色替换为 `t.<slot>`。`build_app` 把解析出的 `theme` 注入每个组件（Renderer、build_banner 调用、confirm 包装、select、PromptInput、REPL）。**保留**各文件原模块级 cinnabar 常量作兜底（删不删都行，先保留确保零回归）。

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_theme_threading.py -v`
Expected: PASS

- [ ] **Step 5: Full regression + commit**

```bash
uv run pytest tests/ -q
uvx ruff check src/wentian tests/test_theme_threading.py
git add -A
git commit -m "[patch] 颜色集中 — Theme 经 DI 穿进 banner/render/confirm/select/input/repl（默认 cinnabar 零回归）"
```

---

### Task 7: `/theme` 运行时命令（持久化）

**Files:**
- Modify: `src/wentian/commands/builtins.py`（加 `_h_theme` + 注册）、`src/wentian/repl.py`（`switch_theme` + theme holder live 重主题 + 注入 `persist_theme`）、`src/wentian/commands/context.py`（CommandContext 若需 `switch_theme` 方法签名）
- Test: `tests/test_theme_command.py`

**Interfaces:**
- Consumes: `get_theme`、`THEMES`、`save_theme`。
- Produces: `/theme` LOCAL 命令；`REPL.switch_theme(name)` 切活动主题 + 调 `persist_theme`。

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_theme_command.py
def test_theme_command_known_switches_and_persists(repl_factory):
    persisted = []
    repl = repl_factory(persist_theme=persisted.append)
    repl.switch_theme("ink")
    assert repl._theme.name == "ink"
    assert persisted == ["ink"]


def test_theme_command_unknown_keeps_current_and_does_not_persist(repl_factory):
    persisted = []
    repl = repl_factory(persist_theme=persisted.append)  # 起始 cinnabar
    repl.switch_theme("bogus")
    assert repl._theme.name == "cinnabar"
    assert persisted == []


def test_theme_no_arg_prints_current_and_options(repl_factory, capsys_or_console):
    repl = repl_factory()
    repl.switch_theme("")  # 约定：空参 → 打印当前 + 列表，不变更
    # 断言输出含 "cinnabar" 与 "ink"/"plain"（按项目既有打印断言法）
```

> **实现者注**：`repl_factory` 复用 Task 5 的构造夹具，额外透传 `persist_theme`。打印断言沿用项目既有 console 捕获法（见 `test_commands_builtins.py`）。`switch_theme("")` 的空参语义 = 打印当前主题 + 可选列表（不变更、不持久化）。

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_theme_command.py -v`
Expected: FAIL — `REPL` 无 `switch_theme` / `persist_theme` 参数

- [ ] **Step 3: Write minimal implementation**

`REPL.__init__`：加 `theme: Theme | None = None` 与 `persist_theme: Callable[[str], None] | None = None`；存 `self._theme = theme or THEMES["cinnabar"]`、`self._persist_theme = persist_theme`。

`REPL.switch_theme`：

```python
    def switch_theme(self, name: str) -> None:
        """运行时切主题：空参打印当前+列表；已知名切换并写 state；未知名提示不变。"""
        from wentian.ui.theme import THEMES, get_theme

        arg = name.strip()
        if not arg:
            self._console.print(
                f"[dim]当前主题：{self._theme.name}　可选：{', '.join(sorted(THEMES))}[/dim]"
            )
            return
        if arg not in THEMES:
            self._console.print(
                f"[yellow]未知主题 '{arg}'，可选：{', '.join(sorted(THEMES))}[/yellow]"
            )
            return
        self._theme = get_theme(arg)
        self._renderer.set_theme(self._theme)  # live 重主题（见下）
        if self._persist_theme is not None:
            self._persist_theme(arg)
        self._console.print(f"[green]已切换主题：{arg}[/green]")
```

`Renderer.set_theme(theme)`：一行 setter 更新其持有的 theme（后续 ⏺/⎿ 读新值）。

`commands/builtins.py` — `_h_theme` handler + 注册 `CommandSpec(name="theme", type=LOCAL, ...)`：

```python
def _h_theme(ctx, args: str) -> bool | None:
    """切换/查看终端主题。"""
    ctx.switch_theme(args)
    return None
```

在 `build_builtin_registry` 注册（与 `/provider` 同列）；`CommandContext` 加 `switch_theme(self, args: str) -> None` 协议方法，REPL 的 CommandContext 实现转调 `self.switch_theme`。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_theme_command.py -v`
Expected: PASS

- [ ] **Step 5: Regression + commit**

```bash
uv run pytest tests/ -q
uvx ruff check src/wentian tests/test_theme_command.py
git add -A
git commit -m "[patch] /theme 运行时命令 — 切换+持久化（state.theme），live 重主题，未知名安全提示"
```

---

### Task 8: main() 接线 + `--pick` + 启动期 state 解析 + 版本号 + 验收证据

**Files:**
- Modify: `src/wentian/cli.py:858-906`（typer `main()`）、`src/wentian/cli.py:374-386` 附近（build_app 内 theme 解析）、`src/wentian/__init__.py`、`pyproject.toml`
- Test: `tests/test_cli_main_wiring.py`（轻量，能力允许下）

**Interfaces:**
- Consumes: `load_state`、`save_last_provider`、`save_theme`、`get_theme`、Tasks 4/5/7 的注入点。
- Produces: 端到端启动持久化行为。

- [ ] **Step 1: build_app 内 theme 解析**

在 `build_app` 加 kw-only 参数 `remembered_theme: str | None = None`，并在配置加载后解析：

```python
    from wentian.ui.theme import get_theme
    theme = get_theme(remembered_theme or config.theme)
```

把 `theme` 注入 Renderer / banner / confirm / select / PromptInput / REPL（承接 Task 6/7 的注入点）。

- [ ] **Step 2: main() 读写 state + --pick option**

```python
@app.command()
def main(
    provider: Annotated[Optional[str], typer.Option("--provider", "-p", help="Provider name from config.")] = None,
    continue_: Annotated[bool, typer.Option("--continue", help="Resume the most recent session.")] = False,
    resume: Annotated[Optional[str], typer.Option("--resume", help="Resume a specific session by id.")] = None,
    pick: Annotated[bool, typer.Option("--pick", help="强制弹出 provider 选择器（忽略已记忆的选择）。")] = False,
) -> None:
    from wentian.state import default_state_path, load_state, save_last_provider, save_theme

    state_path = default_state_path()
    state = load_state(state_path)

    tty = sys.stdin.isatty() and sys.stdout.isatty()
    if tty:
        selector = select_provider
        input_fn = PromptInput(history_path=default_history_path())
        listener = EscListener()
        from wentian.ui.confirm import confirm_action
        confirm = confirm_action
    else:
        selector = input_fn = listener = confirm = None

    try:
        repl = build_app(
            provider_name=provider,
            continue_=continue_,
            resume_id=resume,
            provider_selector=selector,
            input_fn=input_fn,
            interrupt_listener=listener,
            confirm_fn=confirm,
            remembered_provider=state.last_provider,
            remembered_theme=state.theme,
            persist_provider=lambda n: save_last_provider(n, state_path),
            persist_theme=lambda n: save_theme(n, state_path),
            force_pick=pick,
        )
    except (ConfigError, FileNotFoundError) as exc:
        Console(stderr=True).print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1)

    repl.run()
```

> `build_app` 需把 `persist_provider`/`persist_theme` 继续透传给 `REPL(...)`，让运行时 `/provider`·`/theme` 也走同一回调。

- [ ] **Step 3: 版本号 patch bump**

`src/wentian/__init__.py` 与 `pyproject.toml`：`0.11.0` → `0.11.1`。

- [ ] **Step 4: 回归 + lint**

```bash
uv run pytest tests/ -q
uvx ruff check src/wentian
```
Expected: 全绿、无 lint 错。

- [ ] **Step 5: 验收证据（完成前验证铁律）**

1. **三主题真实终端截图**（品味闸，主会话亲自持）：
   - `WENTIAN_CONFIG` 指向含 `theme: cinnabar|ink|plain` 的临时 config，真进程跑横幅 + 一次工具调用，`open` 或截图各一张；不满意当场调 `ink/plain` 色值（Task 2）。
2. **PTY 冒烟 — 模型记忆**：
   - 临时 `XDG_STATE_HOME`，配两个 provider，启动选 `b` → 退出 → 重启：**不弹选择器**、横幅显示 `b`。
   - `wt --pick` → 重新弹选择器。
3. **PTY 冒烟 — 主题持久化**：
   - 启动 `/theme ink` → 退出 → 重启：横幅/标记为 ink 色。
4. 贴现场输出/截图到验收回复。

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "[patch] main() 接线 state 读写 + --pick + theme 解析；版本 0.11.1；三主题截图 + PTY 冒烟验收"
```

---

## Self-Review

**Spec coverage（逐条比对 spec/patches 文档）：**
- §3 state.py → Task 1 ✓
- §4 模型记忆（解析顺序/触发点/接线/--pick/无效降级）→ Task 4（build_app）+ Task 5（REPL switch）+ Task 8（main 接线/--pick）✓
- §5.1 theme.py → Task 2 ✓；§5.2 config theme → Task 3 ✓；§5.3 解析（state.theme 覆盖 config）→ Task 8 ✓；§5.4 颜色集中+DI → Task 6 ✓；§5.5 /theme 持久化 → Task 7 ✓
- §6 版本/流程 → Task 8（patch bump）✓；§7 测试计划 → 各 Task 测试 ✓；§8 文件清单 → 全覆盖 ✓

**Placeholder scan：** ink/plain 色值标注为「截图品味闸敲定」是有意延迟（Task 2 注释 + Task 8 闸口），非占位 bug；其余步骤均含可运行代码/命令。Task 5/7 的 `repl_factory` 明确要求复用既有 `test_repl_*.py` 构造法（不新建平行夹具）。

**Type consistency：** `persist_provider`/`persist_theme: Callable[[str], None] | None`、`theme: Theme | None`、`remembered_provider`/`remembered_theme: str | None`、`force_pick: bool` 在 build_app↔main↔REPL 间命名/类型一致；`RuntimeState(last_provider, theme)` 字段名贯穿 state↔build_app↔main 一致；`Theme` 槽位 `accent/success/error/warning/muted/face_style` 在 theme↔threading 一致。

**结论：** 无未覆盖 spec 项，无占位，类型一致。可执行。
