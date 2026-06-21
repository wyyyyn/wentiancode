# Patch · 模型记忆 + 可切换主题（2026-06-21）

> 补丁级改动，**不进大版本**（折进 v0.11 线，完成时打 patch tag）。本文件是该补丁的**唯一事实来源**；先改本文、再动代码。四件套大 spec（spec.md/plan.md/task.md/checklist.md）不重跑——本补丁 doc 作为它们的增量附录。

## 1. 动机

两个体验问题：

1. **模型每次都要重选**：配了多个 provider 时，启动会弹 `select_provider` 选择器；选择从不写回磁盘（`config.yaml` 只读，`default:` 不变），所以每次启动都重问。运行时 `/provider` 切换也只改 session 文件，不影响下次启动。
2. **配色硬编码、不可换**：单一品牌色 朱砂 `#C84B31` + 裸 `green/red/yellow`，散落在 8 个文件 ~42 处，常量在 3 处重复，没有主题概念。

目标：**启动选过的模型下次不用再选** + **配色收敛成可切换主题**。

## 2. 范围（In / Out）

**In**
- A. 记住上次使用的 provider，下次启动跳过选择器。
- B. 可切换主题：config 选 + 运行时 `/theme` 切换，两者都持久化；内置 3 套（朱砂默认 / 墨青 / 素白）。
- 颜色集中进 `ui/theme.py`，语义化命名。

**Out（YAGNI）**
- 不做用户自定义任意色值（只内置主题）。
- 不做 per-project 记忆（state 全局一份即可）。
- 不重构无关代码、不动其它功能。

## 3. 统一持久化层 —— `src/wentian/state.py`（新增）

与 `config.yaml` **分离**，绝不回写用户手编的 config（避免破坏注释/格式）。

- 路径：`$XDG_STATE_HOME/wentian/state.yaml`，回退 `~/.local/state/wentian/state.yaml`。
  - 复用 `ui/input.py:default_history_path()` 同款 XDG_STATE 模式（已有先例）。
- 数据结构：

  ```python
  @dataclass(frozen=True)
  class RuntimeState:
      last_provider: str | None = None
      theme: str | None = None
  ```

- `load_state(path: Path | None = None) -> RuntimeState`
  - 文件缺失 / 解析失败 / 非映射 → 返回空 `RuntimeState()`，**绝不抛异常**（与 config 可选块的"安全降级"同构）。
  - 未知字段忽略；字段缺失走默认 None。
- `save_state(state: RuntimeState, path=None) -> None` 与便捷函数
  `save_last_provider(name, path=None)` / `save_theme(name, path=None)`：
  - **读-改-写**：先 `load_state` 再只覆盖目标字段（不丢另一字段）。
  - **原子写**：写临时文件 + `os.replace`（沿用 memory INDEX 的 temp+replace 模式）。
  - 自动 `mkdir parents=True`；写失败只警告不崩（best-effort，不阻断启动/运行）。

## 4. Feature A — 记住上次模型

### 4.1 启动期解析顺序（`build_app`）

按优先级决定 `provider_name`：

1. `-p / --provider NAME` → 用 NAME，**transient**（一次性覆盖，**不写 state**）。
2. 否则 remembered `state.last_provider`：**若仍是 config 里的有效 provider** → 用它，**跳过选择器**。← 「下次不用再选」。
3. 否则 `len(providers) > 1` 且 selector 接线（TTY）→ 弹选择器（光标预选 remembered-or-default）→ **写 state**。
4. 否则 → `config.default`（静默）。

边界：
- remembered provider 已从 config 删除 → 视为无效，落到 3/4，**不崩**。
- `--pick` 标志（新增）：强制弹选择器，即使有 remembered；选完照常写 state。

### 4.2 持久化触发点

- **写**：选择器选定 + 运行时 `/provider` 切换。
- **不写**：`-p` 一次性覆盖、静默默认。
- 与用户脑暴结论一致（"开机选择器的选择 + 运行时 /provider 切换，都写入磁盘"）。

### 4.3 接线（保持 `build_app` 纯函数可测）

`build_app` 新增参数（全部带默认值 → 缺省即 v0.11 字节级行为，存量测试零影响）：

- `remembered_provider: str | None = None` —— 已加载的 state.last_provider。
- `persist_provider: Callable[[str], None] | None = None` —— 选定/切换时回调写 state。
- `force_pick: bool = False` —— `--pick`。

I/O 边界在 typer `main()`：
- 启动 `load_state()`，把 `remembered_provider` / `theme` 传进 `build_app`。
- 注入真正的 `persist_provider`（内部调 `save_last_provider`）。
- 新增 `--pick` typer option，置 `force_pick`。

`REPL.switch_provider(name)`：成功切换后调注入的 `persist_provider`（None 则 no-op）。

## 5. Feature B — 可切换主题

### 5.1 `src/wentian/ui/theme.py`（新增）

```python
@dataclass(frozen=True)
class Theme:
    name: str
    accent: str       # 品牌强调：mascot 脸 / 提示符 ❯ / ⏺ 工具标记 / 命令名 / 标题
    success: str      # 工具结果「成功」⎿
    error: str        # 工具结果「失败」⎿（配 bold）
    warning: str      # 工具结果「已拒绝」⎿ / 会话提示
    muted: str        # 次要信息（多数地方仍用 "dim" 修饰；muted 供需要具体色处）

    @property
    def face_style(self) -> str:
        return f"bold {self.accent}"
```

内置 `THEMES: dict[str, Theme]`（**水墨东方调**；ink/plain 最终色值由实现期真实终端截图敲定）：

| name | 中文 | accent | success / error / warning | 说明 |
|------|------|--------|---------------------------|------|
| `cinnabar` | 朱砂（默认） | `#C84B31` | green / red / yellow（**今日原样**） | 默认零视觉变化、零回归基线 |
| `ink` | 墨青 | 冷青绿（如 `#3A8A7D` 一类，截图定） | 和谐化的青/赭/暖黄 | 冷、冷静，东方水墨 |
| `plain` | 素白 | 克制灰阶/近无彩（截图定） | 低彩语义色 | 低色终端 / 极简 |

- `get_theme(name: str | None) -> Theme`：未知/None → 默认 `cinnabar` + `warnings.warn`（安全降级，"绝不崩"）。
- `DEFAULT_THEME_NAME = "cinnabar"`。

### 5.2 Config

- 新增可选顶层键 `theme: <name>`，缺失 → `"cinnabar"`。
- `Config.theme: str = "cinnabar"` 字段；`_build_config_from_raw` 读 `raw.get("theme")`。
- 未知名 → 不在 config 层校验报错，留给 `get_theme` 安全降级（与可选块一致）。

### 5.3 主题解析（启动）

`name = state.theme or config.theme or DEFAULT_THEME_NAME` → `get_theme(name)`。
即：运行时 `/theme` 写的 `state.theme` **覆盖** config 的声明默认（与 provider 同构）。

### 5.4 颜色集中 + DI 穿线

把 ~42 处内联色改为读 `Theme` 语义字段。`build_app` 解析出 `theme: Theme` 后用 DI 传入：
Renderer、`build_banner`、REPL（状态/确认文案）、`confirm_action`、`select_provider`、`PromptInput`（❯ 提示符样式）。

- 模块级 `cinnabar` 常量保留作**兜底**：任何未穿到 theme 的旧调用点仍渲染品牌色（防御纵深，不回归）。
- 默认主题 = 今日色 → **默认路径视觉零变化**；已核实**无任何测试 pin 死具体色值**，回归风险低。

### 5.5 运行时 `/theme` 命令（持久化）

- `/theme`（无参）→ 打印当前主题 + 可选列表。
- `/theme <name>` → `get_theme` 校验（未知则提示 + 不变）→ 切活动主题 + `save_theme(name)` 写 state → 下次启动沿用。
- **Live 重主题**：用**可变 theme holder**（沿用代码库已有的 `system_holder` 持有者模式）注入 render/confirm/prompt；切换后续打印的 ⏺/⎿/❯/确认菜单读新主题。横幅不重印（已在屏上）。
- 注册为受保护 LOCAL 控制命令（同 `/plan` `/provider` 一类），进 `commands/builtins.py` + 注册中心。

## 6. 版本与流程

- **不进大版本**：折进 v0.11 线，完成时打 **patch**（如 `0.11.1`，最终号验收时定）。无新四件套。
- 全程 **TDD 红-绿-重构**（铁律，补丁不豁免）。
- 完成前验证 = ① 全套测试绿 ② 真实终端截图（三主题，品味闸）③ PTY 冒烟（选模型 → 重启 → 不再弹；`/theme` 切 → 重启 → 沿用）。

## 7. 测试计划（TDD，先红后绿）

**state.py**
- 缺失文件 → 空 state；损坏/非映射 → 空 state（不抛）。
- save→load 往返；save_last_provider 不丢 theme、save_theme 不丢 last_provider（读-改-写）。
- 原子写（temp+replace）；XDG_STATE_HOME 优先、`~/.local/state` 回退。

**build_app 解析**
- remembered 有效 → 跳过 selector（selector 不被调用）。
- remembered 无效（已删）→ 落选择器/默认，不崩。
- `-p` 覆盖且**不**触发 persist。
- `--pick`（force_pick）→ 即使有 remembered 也调 selector。
- 选择器选定 → persist_provider 被调一次（参数=所选名）。
- 缺省参数 → 行为与现状字节级一致（回归）。

**REPL.switch_provider**
- 切换成功 → persist_provider 被调（参数=新名）；失败 → 不调。

**theme.py**
- `THEMES` 含 cinnabar/ink/plain 三套；`cinnabar.accent == "#C84B31"`。
- `get_theme` 已知返回对应、未知/None → cinnabar（+warn）。
- `face_style == f"bold {accent}"`。

**config theme**
- 缺 `theme:` → `"cinnabar"`；给定合法名 → 原样；给定未知名 → 不在 config 报错（留 get_theme 降级）。

**主题穿线 / `/theme`**
- 注入 sentinel-accent 的 Theme → banner/render/confirm 用该 accent（断言 accent 流到渲染）。
- `/theme <known>` → 活动主题变 + save_theme 被调；`/theme <unknown>` → 提示不变、不写。
- 启动解析：state.theme 覆盖 config.theme；都缺 → cinnabar。

## 8. 受影响文件清单

**新增**：`src/wentian/state.py`、`src/wentian/ui/theme.py`、对应 `tests/test_state.py`、`tests/test_theme.py`。

**改动**：
- `config.py`：`Config.theme` 字段 + 解析。
- `cli.py`：`main()` 载 state / 注入 persist / `--pick` option；`build_app` 新参数 + 解析顺序 + theme 穿线。
- `repl.py`：`switch_provider` 调 persist；持活动 theme holder；用 theme 替换 `_CINNABAR` 等内联色。
- `ui/banner.py`、`ui/mascot.py`、`ui/confirm.py`、`ui/select.py`、`ui/input.py`、`render.py`：内联色 → theme 字段（带 cinnabar 兜底）。
- `commands/builtins.py` + 注册中心：注册 `/theme`。
- `__init__.py` / `pyproject.toml`：patch 版本号（验收时定）。
