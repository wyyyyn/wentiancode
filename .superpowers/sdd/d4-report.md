# T114 报告 — v0.10 波次三：REPL 接入斜杠命令系统（C91/F73/F74/F76/N35）

## 做了什么

严格 TDD（先写失败测试 → 跑确认失败 → 实现转绿 → ruff 收口）。集成枢纽任务，把 commands/ 包接进 REPL。

### `src/wentian/repl.py` 实现

1. **构造函数加两个可选鸭子参数**（None ⇒ 回退 v0.9，与既有 compactor/memory_runner 同模式）：
   - `commands: object | None = None` — 注入的 `CommandRegistry`（用 `.lookup` / `.visible`），存入 `self._commands`。
   - `memory_store: object | None = None` — 用于 `/memory` 展示（鸭子读 `.read_indexes_for_injection()` / `.user_dir` / `.project_dir`），存入 `self._memory_store`。

2. **`_dispatch_command(line)` 重写**（spec B）：
   - `self._commands is not None` → 注册中心路径：`from wentian.commands.parser import parse` → `parse(line)` → None 打印「请输入命令名，输入 /help 查看帮助」并 `return False` → `lookup(name)` → None 打印 `未知命令：/{name}  输入 /help 查看帮助` 并 `return False` → 否则 `result = spec.handler(self, parsed.args)`、`return bool(result)`。
   - `else`（commands=None）→ **保留既有硬编码 dict 分发逐字不动**（v0.9 回归路径）。

3. **实现 CommandContext 协议的 14 个方法**（AST 比对确认全覆盖、`isinstance(repl, CommandContext)` 真）：
   - 新增：`print`（→ `self._console.print`）、`send_user_message`（→ `_chat_once`）、`set_mode`（纯操作不打印）、`token_usage`（→ `_last_round_usage`）、`memory_summary`、`visible_commands`、`clear_context`（/clear 语义）。
   - 既有对齐：`get_mode`、`status_line`。
   - 归并自老命令的共享逻辑：`new_session` / `list_sessions(*, all_projects)` / `resume_session(sid)` / `switch_provider(name)` / `compact_now() -> str`。

4. **老命令归并（spec E，零重复）**：把 `_cmd_new` / `_cmd_sessions` / `_cmd_resume` / `_cmd_provider` / `_cmd_compact` 的逻辑迁进对应 ctx 方法；旧 `_cmd_*` 变成调 ctx 方法的薄壳（保留方法名——既有测试 `repl._cmd_provider("other")` 与 legacy dict 仍指向它们）。legacy 路径与注册中心路径共享同一份实现。
   - `compact_now` **返回**可读汇报串（`_h_compact` 负责打印）；无 compactor 返回「/compact 不可用：未启用上下文压缩」。
   - `new_session` / `resume_session` / `switch_provider` **自己打印**动态确认/错误（沿用旧 `_cmd_*` 文案——对应 builtins 的 `_h_session new/resume`、`_h_provider` 是纯路由不打印确认）。
   - `list_sessions` 打印列举。

5. **`status_line` 模式标记改括号式（spec D，AC88，唯一有意行为变更）**：首段 `{self._mode.value}` → `[{self._mode.name}]`（`[DEFAULT]` / `[ACCEPT_EDITS]` / `[PLAN]` / `[BYPASS]`）。其余段（会话 id、消息数、`│ 计划模式` 后缀）不变。

### 打印职责核对（按 builtins.py 各 handler 实读）
- `_h_clear` 打印「已清空当前对话上下文。」→ `clear_context` 纯操作不打印 ✅
- `_h_plan` / `_h_do` 调 `set_mode`（自身不打印模式文案，仅 set + 可选 send）→ `set_mode` 纯操作不打印 ✅
- `_h_session new/resume`、`_h_provider` 纯路由不打印确认 → `new_session` / `resume_session` / `switch_provider` 自己打印确认/错误 ✅
- `_h_compact` / `_h_status` / `_h_memory` / `_h_help` 由 handler 组合并打印 → `compact_now` / `status_line` / `memory_summary` / `visible_commands` 只提供数据 ✅
- **未发现双重打印或两边都不打印的情况，未改动 builtins.py。**

## 改了哪 3 个既有 status_line 断言（spec D 明列）
全在 `tests/test_repl.py`：
1. `TestStatusLine::test_fresh_repl_status_line`：`startswith("default")` → `startswith("[DEFAULT]")`
2. `TestStatusLine::test_status_line_first_segment_is_mode_not_provider`：`startswith("default")` → `startswith("[DEFAULT]")`
3. `TestModeCycle::test_status_line_shows_each_mode_value`：`"default"/"acceptEdits"/"plan"/"bypassPermissions"` → `"[DEFAULT]"/"[ACCEPT_EDITS]"/"[PLAN]"/"[BYPASS]"`

## 偏离与疑虑（额外改了 2 个既有断言 — 同一行为变更的必然casualty）

spec D 明列 3 项，但 status_line 首段括号化这个**唯一有意行为变更**还连带打破了另外 2 个既有断言，它们检的是「status_line 首段显示当前模式」这同一件事，不更新会让全套变红、违反「完成前验证全绿」铁律。已按同一意图最小化更新，并在此显式登记：

1. `tests/test_repl.py::TestModeCycle::test_default_mode_injectable`（第 612 行）：`startswith("acceptEdits")` → `startswith("[ACCEPT_EDITS]")`。这是 spec D 漏列的同类断言（status_line 首段=模式），与明列 3 项同因同改。
2. `tests/test_cli.py::test_promptinput_like_status_provider_wired`（第 426 行）：`assert "default" in fake.status_provider()` → `assert "[DEFAULT]" in ...`。该 cli 测试经 `build_app` 取真实 `repl.status_line`，其注释本就写明「状态栏首段改为权限模式」，意图一致，仅适配括号式。

除以上 5 项（spec D 明列 3 + 同因连带 2）外，既有测试一律零修改保持绿。**未改动 builtins.py / context.py / registry.py / parser.py / spec.py。**

## 新增测试（`tests/test_repl.py` 续，注入 `build_builtin_registry()` 走注册中心路径）
- `TestT114CommandContextProtocol`：isinstance 真、send_user_message 跑一轮、get/set_mode（set 不打印）、token_usage、visible_commands（含 None⇒[]）、memory_summary（有 store / 无 store「未启用」）。
- `TestT114RegistryDispatch`：/help 走分发假 provider 零调用、普通文本走 _chat_once、/nope 未知命令提示+零调用、裸 / 提示、/exit 经注册中心退出。
- `TestT114StatusLineMarkers`：/plan→[PLAN]、/do→[DEFAULT]、/permission acceptEdits→[ACCEPT_EDITS]。
- `TestT114Clear`：/clear 留同 id + messages 空 + _persisted_count==0 + 指纹空 + _last_round_usage None + 磁盘落盘为空 + 「已清空」；与 /session new（新 id）区分。
- `TestT114MergedSessionCommands`：/session new≡/new、/session list≡/sessions、/session list --all→`store.list(all_projects=True)`、/session resume≡/resume、/provider 切换、/compact（manual=True）、/compact 无 compactor 不可用提示。
- `TestT114ReviewPrompt`：/review→send_user_message→一轮 AI（provider 被调一次）。
- `TestT114LegacyPathNoRegistry`：commands=None 时 /help 走 legacy、不抛、列出命令。

`_make_repl` 辅助加 `commands` / `memory_store` 两个可选参数透传。

## 最终验证（当场新鲜证据）

```
uv run pytest tests/test_repl.py -q
114 passed in 1.45s        # 既有 91 → +23 新

uv run pytest -q           # 全套
1182 passed, 1 warning in 25.45s
# 唯一 warning 为 test_config 既有 ${API_KEY} 展开告警，与本任务无关

uvx ruff check .
All checks passed!

uvx ruff format --check .
127 files already formatted
```

ruff 亲自跑确认（check 干净 + format 无待改）。`src/wentian/repl.py`、`tests/test_repl.py` 经 `uvx ruff format` 整理（仅格式、无语义变化），重跑全套仍 1182 全绿。

## commit
见仓库最新提交（消息 `[T114/C91/F73/F74/F76/N35] v0.10 波次三：...`，无 Co-Authored-By）。
