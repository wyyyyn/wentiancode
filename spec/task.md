# WentianCode（文天）v0.1 Tasks

> 基于已批准的 spec.md + plan.md。铁律：每个任务先写失败测试（RED），再最小实现（GREEN），绿灯后清理（REFACTOR）。验证命令在项目根目录用 `uv run pytest` 执行。

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `pyproject.toml` | uv 项目定义；依赖 anthropic/openai/typer/rich/pyyaml；dev 依赖 pytest；scripts `wentian`/`wt` |
| 新建 | `src/wentian/__init__.py` | 包标记 + 版本号 |
| 新建 | `src/wentian/providers/base.py` | StreamEvent 三 dataclass、Message 类型、Provider ABC |
| 新建 | `src/wentian/config.py` | YAML 解析、校验、provider 选择 |
| 新建 | `src/wentian/providers/factory.py` | protocol → Provider 类分发 |
| 新建 | `src/wentian/session.py` | Session dataclass + SessionStore 持久化 |
| 新建 | `src/wentian/render.py` | Renderer：thinking 暗色斜体 + Markdown 流式渲染 |
| 新建 | `src/wentian/repl.py` | REPL 循环 + 斜杠命令 + 错误回滚 |
| 新建 | `src/wentian/providers/anthropic.py` | AnthropicProvider（参数组装 + 事件映射） |
| 新建 | `src/wentian/providers/openai_compat.py` | OpenAICompatProvider |
| 新建 | `src/wentian/cli.py` | typer 入口、装配、启动参数 |
| 新建 | `tests/conftest.py` | FakeProvider、tmp 配置/数据目录 fixtures |
| 新建 | `tests/test_*.py` | 各模块对应测试（见 plan 测试策略表） |

## T1: 项目脚手架

**文件：** `pyproject.toml`、`src/wentian/__init__.py`
**依赖：** 无
**步骤（脚手架任务，无生产逻辑，TDD 以"测试基础设施可用"为验证）：**
1. `uv init --package`，补 `pyproject.toml`：依赖 `anthropic` `openai` `typer` `rich` `pyyaml`，dev 组 `pytest`；`[project.scripts]` 暂只留 `wentian = "wentian.cli:app"` 占位（cli 在 T13 落地，先不导出也行——若 uv sync 报错则注释掉 scripts，T13 再开）
2. 写一个冒烟测试 `tests/test_smoke.py::test_import`，断言 `import wentian` 成功
**验证：** `uv sync && uv run pytest -q` → 1 passed

## T2: 统一事件 + Provider 抽象 + FakeProvider

**文件：** `src/wentian/providers/base.py`、`tests/test_providers_base.py`、`tests/conftest.py`
**依赖：** T1
**RED：**
1. 测试：`ThinkingDelta("x").text == "x"`；`Done(usage=None)` 可建
2. 测试：直接实例化 `Provider()` 抛 TypeError（ABC）
3. 测试：`FakeProvider([ThinkingDelta("a"), TextDelta("b"), Done(None)])` 迭代 `stream([])` 按序产出三事件且以 Done 结尾
4. 跑测试确认因模块不存在而失败
**GREEN：** 实现 base.py 三 dataclass + `Message` TypedDict + Provider ABC；conftest.py 实现 FakeProvider（实现统一接口——它就是 AC8 的假后端）
**REFACTOR：** 类型标注收紧（`StreamEvent` union 导出）
**验证：** `uv run pytest tests/test_providers_base.py -q` 全绿

## T3: 配置加载与校验

**文件：** `src/wentian/config.py`、`tests/test_config.py`
**依赖：** T1
**RED：**
1. tmp_path 写合法 YAML（两个 provider + default）→ `load_config(path)` 返回 Config，字段逐一断言（含 `thinking: true` 解析为 bool）
2. `Config.get()` 返回 default 对应项；`Config.get("名")` 按名取；取不存在的名抛 ConfigError
3. 缺 `api_key` / `protocol` 非法 / `default` 指向不存在 → 各抛 ConfigError（消息含字段名）
4. 跑测试确认失败
**GREEN：** 实现 ProviderConfig/Config dataclass + `load_config` + 校验
**REFACTOR：** 校验逻辑抽 `_validate`
**验证：** `uv run pytest tests/test_config.py -q` 全绿

## T4: Provider 工厂

**文件：** `src/wentian/providers/factory.py`、`tests/test_factory.py`
**依赖：** T2、T3
**RED：**
1. `create_provider(anthropic 协议的 cfg)` 返回 AnthropicProvider 实例（此时类可以是占位空壳——先在 anthropic.py/openai_compat.py 建仅含 `__init__` 存 cfg 的骨架）
2. openai 协议同理；注册表外的 protocol 抛 ConfigError
3. 跑测试确认失败
**GREEN：** factory 注册表 dict + 分发；两个 provider 骨架类（`stream` 暂 `raise NotImplementedError`）
**REFACTOR：** 无
**验证：** `uv run pytest tests/test_factory.py -q` 全绿

## T5: 会话持久化

**文件：** `src/wentian/session.py`、`tests/test_session.py`
**依赖：** T2
**RED：**
1. `SessionStore(tmp_path)`：`create()` 返回带 id/时间戳的空 Session；`save` 后目录出现 `<id>.json`
2. save → load(id) 往返，messages/provider/时间字段相等
3. 两个会话不同 updated_at → `load_latest()` 取新者；空目录 `load_latest()` 返回 None
4. 目录里放一个坏 JSON → `list()` 跳过不抛，其余正常返回（含首条用户消息摘要）
5. 跑测试确认失败
**GREEN：** 实现 Session + SessionStore（原子写：tmp 文件 + `os.replace`）
**REFACTOR：** 序列化抽 `to_dict/from_dict`
**验证：** `uv run pytest tests/test_session.py -q` 全绿

## T6: 渲染器——thinking 与正文区分

**文件：** `src/wentian/render.py`、`tests/test_render.py`
**依赖：** T2
**RED：**
1. `Renderer(Console(record=True, width=80))`，喂 `[ThinkingDelta("让我想想"), TextDelta("答案"), Done(None)]` → `render_stream` 返回 `"答案"`（只含正文）
2. 导出文本含 `🤔` 前缀且含"让我想想"；thinking 与正文内容都出现
3. 纯正文事件流（无 thinking）→ 输出不含 `🤔`
4. 跑测试确认失败
**GREEN：** 实现 Renderer：thinking 暗色斜体逐段 print；正文先按纯文本路径累积返回（Markdown 渲染下个任务做）
**REFACTOR：** 无
**验证：** `uv run pytest tests/test_render.py -q` 全绿

## T7: 渲染器——Markdown 流式 + 定格

**文件：** `src/wentian/render.py`、`tests/test_render.py`
**依赖：** T6
**RED：**
1. 喂含 `# 标题`、`- 列表`、```python 围栏``` 的 TextDelta 流 → 录制输出**不含**裸 "```" 字符，且含 Rich Markdown 渲染特征（如代码块边框字符 `─`/`│` 或标题装饰）
2. 返回值仍是累积的原始 Markdown 源文本（入史存源文，不存渲染结果）
3. thinking 文本里出现 `# xx` → 仍按纯文本输出（不被渲染成标题）
4. 跑测试确认失败
**GREEN：** 正文路径接 `rich.live.Live(transient=True)` 重渲 `Markdown(buffer)`（约 10fps 节流）；Done 后关闭 Live、`console.print(Markdown(full))` 定格
**REFACTOR：** Live 阶段与定格阶段抽成私有方法；record 模式下 Live 不可用时（非 TTY）退化为只定格输出——测试走该路径需在实现里以 `console.is_terminal` 分支
**验证：** `uv run pytest tests/test_render.py -q` 全绿

## T8: REPL——一轮对话

**文件：** `src/wentian/repl.py`、`tests/test_repl.py`
**依赖：** T2、T5、T6（用 T7 完成后的 Renderer 更佳，但接口不变）
**RED：**
1. 注入 FakeProvider（产出 `TextDelta("回答")`+Done）、tmp SessionStore、record Console、`input_fn` 依次返回 `"你好"`、`"/exit"` → `repl.run()` 正常退出
2. 退出后 session.messages == [user("你好"), assistant("回答")]，且磁盘上 `<id>.json` 已含两条（AC3/AC6 离线版）
3. 第二轮输入也能携带第一轮历史调用 provider（FakeProvider 记录收到的 messages，断言含第一轮两条）
4. 跑测试确认失败
**GREEN：** REPL 类 + run 循环 + 对话一轮逻辑
**REFACTOR：** 一轮对话抽 `_chat_once`
**验证：** `uv run pytest tests/test_repl.py -q` 全绿

## T9: REPL——斜杠命令

**文件：** `src/wentian/repl.py`、`tests/test_repl.py`
**依赖：** T8
**RED（每条命令一个最小测试）：**
1. `/help` → 输出含全部六条命令名
2. `/new` → 当前 session 换成新 id，旧会话文件保留
3. `/sessions` → 输出含已存会话的 id
4. `/resume <id>` → 当前 session 换成指定 id，历史载入；坏 id 报错不崩
5. `/provider <名>` → 调用注入的 `provider_factory(名)` 替换 provider；未知名报错不崩
6. `/exit` → run 返回
7. 未知斜杠命令 → 提示"未知命令"不崩
8. 跑测试确认失败
**GREEN：** 命令分发 dict + 各 handler（provider_factory 作为构造参数注入，便于测试）
**REFACTOR：** handler 签名统一
**验证：** `uv run pytest tests/test_repl.py -q` 全绿

## T10: REPL——错误回滚

**文件：** `src/wentian/repl.py`、`tests/test_repl.py`
**依赖：** T8
**RED：**
1. FakeProvider.stream 抛 RuntimeError → run 不崩，输出含错误提示，session.messages 为空（user 消息已回滚），磁盘未写入该轮
2. 出错后下一轮输入仍能正常对话
3. 跑测试确认失败
**GREEN：** `_chat_once` 包 try/except：异常时 pop 本轮 user 消息、打印错误、continue
**REFACTOR：** 无
**验证：** `uv run pytest tests/test_repl.py -q` 全绿

## T11: AnthropicProvider

**文件：** `src/wentian/providers/anthropic.py`、`tests/test_provider_anthropic.py`
**依赖：** T4
**RED（mock `anthropic.Anthropic`，断言 `messages.stream` 调用参数与事件产出）：**
1. `thinking: false` 的 cfg → stream 调用 kwargs **不含** `thinking` 键；含 `model=cfg.model`、`max_tokens=64000`
2. `thinking: true` → kwargs 含 `thinking={"type":"adaptive","display":"summarized"}`
3. kwargs 不含 `temperature`/`top_p`/`top_k`/`budget_tokens`（Opus 4.8 会 400）
4. mock 流产出 thinking_delta("想")、text_delta("答") 事件 → 统一事件 `[ThinkingDelta("想"), TextDelta("答"), Done(usage)]`，usage 取自 `get_final_message()`
5. `base_url` 设置时透传给客户端构造；system 参数透传
6. 跑测试确认失败
**GREEN：** 实现参数组装 + 事件映射（迭代 `stream` 事件对象，按 `event.type`/`delta.type` 分支）
**REFACTOR：** 事件映射抽 `_map_event`
**验证：** `uv run pytest tests/test_provider_anthropic.py -q` 全绿

## T12: OpenAICompatProvider

**文件：** `src/wentian/providers/openai_compat.py`、`tests/test_provider_openai_compat.py`
**依赖：** T4
**RED（mock `openai.OpenAI`）：**
1. `chat.completions.create` 收到 `stream=True`、`model=cfg.model`；`base_url`/`api_key` 透传构造
2. system 文本注入 messages 首位 `{"role":"system",...}`
3. mock chunks：`delta.content="答"` → TextDelta；`delta.reasoning_content="想"` → ThinkingDelta；无该属性的 chunk 不崩；流尽产出 Done
4. 跑测试确认失败
**GREEN：** 实现（reasoning_content 用 getattr 容错）
**REFACTOR：** 无
**验证：** `uv run pytest tests/test_provider_openai_compat.py -q` 全绿

## T13: CLI 装配

**文件：** `src/wentian/cli.py`、`tests/test_cli.py`、`pyproject.toml`（开启 scripts）
**依赖：** T3、T4、T5、T8（全链路就绪）
**RED（typer CliRunner + monkeypatch：配置路径指向 tmp、REPL.run 替换为记录桩）：**
1. 无参数启动 → 用 default provider、新建 session 装配 REPL
2. `--provider deepseek` → 用指定 provider
3. `--continue` → load_latest 的 session；无历史时回退新建并提示
4. `--resume <id>` → 指定 session；坏 id 报错退出码非 0
5. 配置文件缺失/非法 → 友好报错退出码非 0（不打印 traceback）
6. 跑测试确认失败
**GREEN：** typer app + 装配函数；`wentian`、`wt` 两个 script 入口指向同一 app
**REFACTOR：** 装配逻辑抽 `build_app(...)` 纯函数（返回 REPL 实例），CLI 壳只做参数翻译
**验证：** `uv run pytest tests/test_cli.py -q` 全绿；`uv run wentian --help` 与 `uv run wt --help` 均输出帮助

## T14: 全量回归 + 收尾

**文件：** 无新增
**依赖：** T1–T13
**步骤：**
1. `uv run pytest -q` 全绿、无警告噪音
2. 按全局 CLAUDE.md 逐功能点提交已在各任务后进行，此处确认工作区干净
**验证：** `uv run pytest -q` 输出 0 failed；`git status` 干净

## 执行顺序

```
T1 ─► T2 ─► T4 ─► T11 ─► T13 ─► T14
  │     ├─► T5 ─► T8 ─► T9      ▲
  │     └─► T6 ─► T7   └► T10 ──┤
  └─► T3 ─────────────► (T4/T13)
T12 与 T11 平行（都依赖 T4）
```

依赖满足即可并行（如 T3 与 T2、T11 与 T12、T6/T7 与 T5）；串行执行则按编号顺序即可。

---

# v0.2 Tasks（F13–F18：Claude Code 式交互）

> 基于已批准的 spec.md（F13–F18/AC11–AC16）+ plan.md v0.2 章节（C1–C7）。铁律不变：RED → GREEN → REFACTOR。

## v0.2 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `pyproject.toml` | 加 `prompt_toolkit`；version 0.2.0 |
| 修改 | `src/wentian/__init__.py` | `__version__ = "0.2.0"` |
| 新建 | `src/wentian/ui/__init__.py` | ui 包标记 |
| 新建 | `src/wentian/ui/banner.py` | C1 横幅纯函数 |
| 修改 | `src/wentian/providers/base.py` | Provider 增可选 `model` 属性 |
| 修改 | `src/wentian/repl.py` | C2 status_line；C6 interrupt_listener + 中断语义 |
| 新建 | `src/wentian/ui/input.py` | C3 PromptInput + default_history_path |
| 新建 | `src/wentian/ui/select.py` | C4 select_provider |
| 新建 | `src/wentian/ui/spinner.py` | C5 WaitingSpinner |
| 修改 | `src/wentian/render.py` | C5 RenderResult + spinner/计时接入；C6 _StreamPump + 中断检查 |
| 新建 | `src/wentian/ui/interrupt.py` | C6 InterruptListener 协议 + NullListener + EscListener |
| 修改 | `src/wentian/cli.py` | C7 装配：banner/selector/PromptInput/EscListener 注入 |
| 修改 | `tests/conftest.py` | FakeListener、阻塞式 FakeProvider |
| 新建 | `tests/test_ui_*.py` | banner/input/select/spinner/interrupt 测试 |

## T15: v0.2 脚手架

**文件：** `pyproject.toml`、`src/wentian/__init__.py`、`src/wentian/ui/__init__.py`
**依赖：** 无（v0.1 已全绿）
**步骤（脚手架任务）：**
1. pyproject：dependencies 加 `prompt_toolkit`，version → `0.2.0`
2. `__init__.py` 加 `__version__ = "0.2.0"`；建空 `ui/` 包
3. 冒烟测试：`import wentian.ui` 成功、`wentian.__version__ == "0.2.0"`、`import prompt_toolkit` 可用
**验证：** `uv sync && uv run pytest tests/test_smoke.py -q` 全绿

## T16: C1 横幅 + Provider.model

**文件：** `src/wentian/ui/banner.py`、`src/wentian/providers/base.py`、`tests/test_ui_banner.py`
**依赖：** T15
**RED：**
1. 测试：`build_banner(version="0.2.0", provider_name="sf", model="deepseek-ai/DeepSeek-V3.2", session_id="x", resumed=False)` 经 `Console(record=True, width=80)` 打印后，导出文本含版本、provider:model、会话 id、圆角框字符 `╭`、「新会话」
2. 测试：`resumed=True` → 含「已恢复」
3. 测试：AnthropicProvider/OpenAICompatProvider 实例 `.model` 等于配置 model（沿用现有 mock 构造方式）
4. 跑测试确认失败
**GREEN：** banner.py 纯函数（Panel + box.ROUNDED + 小字符画）；base.py ABC 加类属性 `model: str = ""`；两 provider `__init__` 设 `self.model`
**REFACTOR：** 字符画常量提模块级
**验证：** `uv run pytest tests/test_ui_banner.py -q` 全绿，全量回归无破

## T17: C2 REPL.status_line

**文件：** `src/wentian/repl.py`、`tests/test_repl.py`
**依赖：** T16（用到 provider.model）
**RED：**
1. 测试：构造 REPL（FakeProvider name="fake"）→ `status_line()` 含 "fake"、会话 id、"0 条消息"
2. 测试：一轮对话后消息数变 "2 条消息"
3. 测试：`_cmd_provider` 切到另一个 fake 后 status_line 反映新名字；`_cmd_new` 后会话 id 变化且消息数归零
4. 跑测试确认失败
**GREEN：** 实现 `status_line()`（getattr 兜底 model 为空时省略 `:model` 段）
**验证：** `uv run pytest tests/test_repl.py -q` 全绿

## T18: C3 PromptInput 多行输入框

**文件：** `src/wentian/ui/input.py`、`tests/test_ui_input.py`
**依赖：** T17（status_provider 接 REPL.status_line）
**RED：**
1. 测试（pipe input + DummyOutput）：发 `"hello\r"` → 返回 `"hello"`
2. 测试：发 `"a\x0ab\r"`（Ctrl+J）→ 返回 `"a\nb"`；发 `"a\x1b\rb\r"`（Alt+Enter）→ 返回 `"a\nb"`
3. 测试：history_path=tmp 文件，提交 "one" 后新实例发 `"\x1b[A\r"` → 返回 "one"（跨实例历史）；文件已落盘
4. 测试：`status_provider` 赋值后，session 的 bottom_toolbar callable 返回该函数输出
5. 测试：`default_history_path()` 在 XDG_STATE_HOME 设定下返回对应路径
6. 跑测试确认失败
**GREEN：** PromptSession(multiline=True) + 按键绑定（enter 提交 / c-j、escape+enter 换行 / 首行↑末行↓翻历史）+ FileHistory + bottom_toolbar 闭包
**REFACTOR：** 按键绑定抽 `_build_key_bindings()`
**验证：** `uv run pytest tests/test_ui_input.py -q` 全绿

## T19: C4 select_provider 选择器

**文件：** `src/wentian/ui/select.py`、`tests/test_ui_select.py`
**依赖：** T15
**RED：**
1. 测试（pipe input + DummyOutput）：names=["a","b","c"] default="a"，发 `"\r"` → "a"
2. 测试：发 `"\x1b[B\r"`（↓+回车）→ "b"；发两次 ↓ + ↑ + 回车 → "b"
3. 测试：发 `"\x03"`（Ctrl+C）→ "a"（default）
4. 测试：default="b" 时初始高亮在 b（直接 `\r` → "b"）
5. 跑测试确认失败
**GREEN：** inline Application + FormattedTextControl + kb（up/down/enter/c-c/escape），erase_when_done=True
**验证：** `uv run pytest tests/test_ui_select.py -q` 全绿

## T20: C5a WaitingSpinner

**文件：** `src/wentian/ui/spinner.py`、`tests/test_ui_spinner.py`
**依赖：** T15
**RED：**
1. 测试：fake clock 从 0 起，`render_line()` 文案含 "(0s)"；clock 推到 5.3 → "(5s)"
2. 测试：帧轮换——不同时刻 render_line 首字符在 FRAMES 集合内且随时间变化
3. 测试：非 TTY console 下 `start()/stop()` 不产生任何输出（record 导出为空）；stop 幂等（连调两次不抛）
4. 跑测试确认失败
**GREEN：** WaitingSpinner（clock 注入、TTY 才开 Live(get_renderable=…)）
**验证：** `uv run pytest tests/test_ui_spinner.py -q` 全绿

## T21: C5b RenderResult + 计时接入 render

**文件：** `src/wentian/render.py`、`src/wentian/repl.py`、`tests/test_render.py`、`tests/test_repl.py`
**依赖：** T20
**RED：**
1. 测试：`render_stream` 返回 `RenderResult`；`result.text` 等于旧语义全文；`result.interrupted is False`
2. 测试：repl `_chat_once` 后 assistant 消息内容 == result.text（既有断言迁移）
3. 跑测试确认失败（返回类型变更）
**GREEN：** RenderResult dataclass；render_stream 开头 spinner.start()、首事件 spinner.stop()；TTY 正文 Live 改 `get_renderable=lambda: Group(Markdown(buffer), 计时行)`，定格只印 Markdown；repl 改用 result.text
**REFACTOR：** 既有 test_render/test_repl 断言机械更新（约 20–30 处）；`_active_live` 单句柄收口
**验证：** `uv run pytest -q` 全量全绿（148+ 新增）

## T22: C6a _StreamPump 可中断流消费

**文件：** `src/wentian/render.py`、`tests/test_render.py`、`tests/conftest.py`
**依赖：** T21
**RED：**
1. conftest 加"阻塞式 FakeProvider"：yield 2 个 TextDelta 后 `threading.Event().wait()` 永卡
2. 测试：`render_stream(events, interrupt=ev)`，0.2s 后另一线程 set ev → 0.5s 内返回，`RenderResult(text="前两段", interrupted=True)`
3. 测试：interrupt 已 set 且首事件未到（FakeProvider 直接卡）→ 返回 `RenderResult("", True)`
4. 测试：pump 模式下 provider 抛错 → 异常原样冒出（回滚路径不变）
5. 测试：`interrupt=None` 时行为与 T21 完全一致（直接迭代路径）
6. 跑测试确认失败
**GREEN：** _StreamPump（泵线程 + queue + ("event"/"error"/"end") 协议）；render_stream 双检查点（get 超时循环 + 每事件前）；命中 → stop pump/spinner/Live → dim 打印 `⎿ 已中断` → 返回 interrupted 结果；renderer 内 `except KeyboardInterrupt` 同样转 interrupted
**验证：** `uv run pytest tests/test_render.py -q` 全绿，0.5s 时限断言不抖

## T23: C6b REPL 中断语义 + InterruptListener 协议

**文件：** `src/wentian/ui/interrupt.py`（协议 + NullListener）、`src/wentian/repl.py`、`tests/conftest.py`（FakeListener）、`tests/test_repl.py`
**依赖：** T22
**RED：**
1. 测试：FakeListener 注入 + 边产出边 set 的 FakeProvider → 一轮后 assistant=partial 已入史且落盘
2. 测试：零正文即中断 → user 消息回滚、会话目录无新文件
3. 测试：中断后 REPL 继续接受下一条输入并正常对话（FakeProvider 第二轮完整产出）
4. 测试：默认（不注入 listener）时行为与 v0.1 等价（NullListener 永不触发）
5. 跑测试确认失败
**GREEN：** InterruptListener Protocol + NullListener；REPL 构造加 `interrupt_listener=NullListener()`；`_chat_once` 用 `with listener as ev:` 包住 stream+render，按 RenderResult.interrupted + text 空否分支
**验证：** `uv run pytest tests/test_repl.py -q` 全绿

## T24: C6c EscListener（真实终端监听）

**文件：** `src/wentian/ui/interrupt.py`、`tests/test_ui_interrupt.py`
**依赖：** T23
**RED：**
1. 测试（pty.openpty 注入 slave fd）：`with EscListener(fd) as ev:` 后 master 写 `\x1b` → 0.3s 内 ev.is_set()
2. 测试：写 `\x1b[A`（方向键）→ 0.3s 后 ev 仍未 set
3. 测试：with 退出后 termios 设置等于进入前快照（tcgetattr 对比）
4. 测试：with 体内抛异常 → termios 仍还原（finally 语义）
5. 跑测试确认失败
**GREEN：** EscListener（termios 快照/cbreak/daemon select 线程/50ms 转义探测/退出 tcflush+还原）
**验证：** `uv run pytest tests/test_ui_interrupt.py -q` 全绿（pty 测试在 macOS 本地稳定）

## T25: C7 cli 装配收口

**文件：** `src/wentian/cli.py`、`tests/test_cli.py`
**依赖：** T16、T18、T19、T23（全部组件就绪）
**RED：**
1. 测试：build_app 后 console 输出含横幅（版本 + provider 名）；`show_banner=False` 则无
2. 测试：注入 recording fake selector——两 provider 且无 -p → 被调用且收到 (names, default)，返回值成为生效 provider；单 provider / 显式 -p → 不被调用
3. 测试：`--continue` 启动时横幅含「已恢复」
4. 测试：非 TTY（CliRunner 默认）不装 PromptInput/EscListener（input_fn 仍为默认、listener 为 NullListener）——经注入点观察
5. 跑测试确认失败
**GREEN：** build_app 新参数（provider_selector/input_fn/history_path/show_banner）+ 装配顺序（plan C7）；typer main TTY 判断接真实 select_provider/PromptInput/EscListener
**REFACTOR：** 装配函数内聚检查，确保 build_app 仍纯装配无 I/O 判断泄漏
**验证：** `uv run pytest -q` 全量全绿

## v0.2 执行顺序

```
T15 → T16 → T17 → T18 ─┐
   └→ T19（可与 T16–T18 并行）├→ T25
   └→ T20 → T21 → T22 → T23 → T24 ─┘
```

## v0.2 教学隔离规约（用户要求，2026-06-11）

为教学回溯，每个阶段的产物必须可独立定位（仅标记，不影响功能）：

1. **一任务一提交**：commit message 前缀 `[T16/C1/F13]` 式三段标记（任务/组件/需求），严禁跨任务混提交。
2. **一组件一文件**：v0.2 新组件各居一文件（`ui/banner.py` / `ui/select.py` / `ui/input.py` / `ui/spinner.py` / `ui/interrupt.py`），不合并。
3. **文件头标记**：每个新建/修改文件的模块 docstring 首行注明 `v0.2 · C编号 · F编号（任务 T编号）`；修改 v0.1 文件时在改动函数/类的 docstring 加同款标记行。
4. **测试同名对应**：每个组件的测试文件一一对应（`test_ui_banner.py` ↔ `ui/banner.py`），便于按阶段查阅。

## T27: 横幅改版（Claude Code 布局 + 像素小人）【2026-06-11 追加，已完成】

**文件：** `src/wentian/ui/banner.py`、`tests/test_ui_banner.py`、`assets/`
spec F13 改版 → RED（无框 + 像素字符断言）→ GREEN（像素栅格半块渲染 + Table.grid 两列布局）→ PNG 预览目检。

## T28: 像素猫 mascot + 眨眼动画【2026-06-11 追加，已完成】

**文件：** `src/wentian/ui/mascot.py`（新，共享帧模块）、`banner.py`、`spinner.py`、`tests/test_ui_spinner.py`、`assets/`
**依赖：** T27
spec F13/F17 改版（=^_^= 骨架 + 动画）→ RED（文本猫脸帧 + render_block 断言）→ GREEN：
- `mascot.py`：PIXEL_OPEN/PIXEL_BLINK 两帧 + TEXT_FACES + pick_frame（每 2 秒周期末 0.5s 眨眼）+ render_mascot
- 等待期 Live 显示多行像素猫（render_block，rich 刷新线程驱动眨眼与秒数）；流式期单行 `=^_^= 构思中… (Ns)` 同节奏眨眼
- banner 用静态睁眼帧；素材 `assets/wentian-mascot{,-blink}.png`（旧版已入 `_originals/`）
