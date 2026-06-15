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

## T29: =^_^= 文本脸定稿 + 输入框去边框修复【2026-06-11 追加，已完成】

**文件：** `ui/mascot.py`（精简为纯文本帧）、`ui/banner.py`、`ui/spinner.py`、`ui/input.py`、对应测试
**依赖：** T28
**背景（用户截图实锤的两个问题）：**
1. 像素猫在 Terminal.app 字体下变形成"螃蟹"——半块像素画依赖字体渲染，不可控。决策：mascot 就用 `=^_^=` 文本本体（等宽字体下永不变形）。
2. 输入框手绘边框（pt 渲染器之外的 print）与 prompt_toolkit 重绘机制冲突，真实终端上 prompt 重复堆叠满屏。决策：删除一切 out-of-band 终端写入，提示符改为纯 `❯ `（pt 全权管理）。
**RED→GREEN：** 源码级断言（input.py 不得含盒线字符/isatty）+ 文本脸断言 + prompt message 断言 → 实现 → 229 测试全绿 → pyte 仿真验证（空回车 5 次无残留、无堆叠、/exit 干净）。
**注：** `assets/wentian-mascot*.png` 保留作教学历史（记录像素画尝试与否决过程）。

---

# v0.3 Tasks（F19–F28：工具系统）

> 依据已批准的 spec（F19–F28）与 plan（C8–C13）。延续教学隔离规约：一任务一提交，commit 前缀 `[T30/C8/F19]` 式三段标记；新文件 docstring 首行注明 `v0.3 · C编号 · F编号（任务 T编号）`。

## v0.3 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `src/wentian/providers/base.py` | ToolSpec / ToolCallEvent / Message 扩展 / Done.raw_content / stream 签名 |
| 新建 | `src/wentian/tools/__init__.py` | 包初始化与公开导出 |
| 新建 | `src/wentian/tools/base.py` | Tool ABC + ToolError + spec() |
| 新建 | `src/wentian/tools/registry.py` | ToolRegistry |
| 新建 | `src/wentian/tools/files.py` | read_file / write_file / edit_file |
| 新建 | `src/wentian/tools/shell.py` | run_command |
| 新建 | `src/wentian/tools/search.py` | find_files / search_text |
| 新建 | `src/wentian/tools/executor.py` | ToolExecutor + ToolOutcome |
| 修改 | `src/wentian/providers/anthropic.py` | 工具声明 / tool_use 解析 / raw_content / 历史转换 |
| 修改 | `src/wentian/providers/openai_compat.py` | 工具声明 / 碎片拼接 / 历史转换 |
| 修改 | `src/wentian/render.py` | RenderResult 扩展 + 工具调用屏显 |
| 修改 | `src/wentian/repl.py` | 单轮工具回合编排 |
| 修改 | `src/wentian/cli.py`、`pyproject.toml`、`__init__.py` | 装配 + 版本 0.3.0 |
| 新建 | `tests/test_tools_registry.py` 等 6 个 | tools 层测试（与模块一一对应） |
| 修改 | 既有 providers/render/repl/session/cli 测试 | v0.3 增量用例 |

## T30: 契约扩展（providers/base.py）

**文件：** `src/wentian/providers/base.py`、`tests/test_providers_base.py`、`pyproject.toml`、`src/wentian/__init__.py`
**依赖：** 无
**RED：**
1. 测试：`ToolSpec(name, description, parameters)` 可构造、frozen
2. 测试：`ToolCallEvent(id, name, arguments)` 可构造；`arguments` 接受 dict 与 None
3. 测试：`Done()` 的 `raw_content` 默认 None；`Done(usage=…, raw_content=[{…}])` 可携带
4. 测试：FakeProvider 的 stream 接受 `tools=` 关键字（None 默认）且既有事件序列不变
5. 跑测试确认失败
**GREEN：** base.py 加 ToolSpec / ToolCallEvent / ToolCallDict / Message 扩展（tool 角色、tool_calls、tool_call_id、is_error、raw_content，`total=False`）/ Done.raw_content / Provider.stream 签名加 `tools: list[ToolSpec] | None = None`；conftest FakeProvider 同步签名；版本号 0.3.0
**REFACTOR：** `__all__` 与模块 docstring 更新
**验证：** `uv run pytest tests/test_providers_base.py -q` 全绿，且 `uv run pytest -q` 全量不破（签名向后兼容）

## T31: C8 Tool ABC + 注册中心

**文件：** `src/wentian/tools/base.py`、`src/wentian/tools/registry.py`、`src/wentian/tools/__init__.py`、`tests/test_tools_registry.py`
**依赖：** T30
**RED：**
1. 测试：定义 FakeTool（name/description/parameters/run）→ `registry.register` 后 `get(name)` 取回同一实例；`get("不存在")` 返回 None
2. 测试：`specs()` 返回 ToolSpec 列表，字段与工具属性一致、顺序与注册序一致（AC17 的「假工具」）
3. 测试：重名 register → ValueError
4. 测试：`ToolError("msg")` 是 Exception 子类
5. 跑测试确认失败
**GREEN：** Tool ABC（name/description/parameters/timeout_s/requires_confirmation=False/run 抽象）+ ToolError + spec() + ToolRegistry
**验证：** `uv run pytest tests/test_tools_registry.py -q` 全绿

## T32: C9 read_file + write_file

**文件：** `src/wentian/tools/files.py`、`tests/test_tools_files.py`
**依赖：** T31
**RED：**
1. 测试（tmp_path 为 root）：write_file 写新文件返回含路径的成功文本；父目录不存在时自动创建；覆盖已有文件
2. 测试：read_file 读回写入内容；`offset`/`limit` 行范围生效；相对路径基于 root 解析；绝对路径放行
3. 测试：read_file 不存在 / 路径是目录 → ToolError（信息含路径）
4. 测试：read_file 超 2000 行 → 截断且文末含截断说明
5. 测试：缺 `path` 参数 → ToolError
6. 跑测试确认失败
**GREEN：** ReadFileTool / WriteFileTool（构造接收 root；轻量参数校验）
**验证：** `uv run pytest tests/test_tools_files.py -q` 全绿

## T33: C9 edit_file（F25 核心）

**文件：** `src/wentian/tools/files.py`、`tests/test_tools_files.py`
**依赖：** T32
**RED：**
1. 测试：old_string 唯一匹配 → 文件被替换、返回成功文本
2. 测试：零匹配 → ToolError 信息含「0」与路径，文件未变
3. 测试：三处匹配 → ToolError 信息含「3」，文件未变
4. 测试：old_string == new_string → ToolError；文件不存在 → ToolError
5. 跑测试确认失败
**GREEN：** EditFileTool（`requires_confirmation=True`，write_file 同步补此标记）
**验证：** `uv run pytest tests/test_tools_files.py -q` 全绿

## T34: C9 run_command

**文件：** `src/wentian/tools/shell.py`、`tests/test_tools_shell.py`
**依赖：** T31
**RED：**
1. 测试：`echo hi` → 结果含 `hi` 与退出码 0；cwd 为 root（`pwd` 验证）
2. 测试：`exit 3` → 结果含退出码 3（非零不算 ToolError——模型需要看到失败输出）
3. 测试：stderr 输出被捕获并标注
4. 测试：构造 timeout_s 极小的实例跑 `sleep 5` → ToolError 含「超时」
5. 测试：超长输出被截断到上限并附说明
6. 跑测试确认失败
**GREEN：** RunCommandTool（`/bin/sh -c`、capture、subprocess timeout、头尾截断、requires_confirmation=True）
**验证：** `uv run pytest tests/test_tools_shell.py -q` 全绿

## T35: C9 find_files + search_text

**文件：** `src/wentian/tools/search.py`、`tests/test_tools_search.py`
**依赖：** T31
**RED：**
1. 测试（tmp_path 造树含 .git/ 与嵌套目录）：find_files `**/*.py` → 相对路径排序列表；`.git` 内文件不出现
2. 测试：超 200 个匹配 → 截断附说明；零匹配 → 明确「无匹配」文本（非错误）
3. 测试：search_text 正则命中 → 输出含 `路径:行号:行内容`；二进制文件（含 \0）被跳过
4. 测试：非法正则 → ToolError 含原因
5. 跑测试确认失败
**GREEN：** FindFilesTool / SearchTextTool（共享跳过目录集合）
**验证：** `uv run pytest tests/test_tools_search.py -q` 全绿

## T36: C10 ToolExecutor（F24/F26 核心）

**文件：** `src/wentian/tools/executor.py`、`tests/test_tools_executor.py`
**依赖：** T31
**RED（全用 FakeTool，不依赖六工具）：**
1. 测试：正常执行 → ToolOutcome(content=返回值, is_error=False)
2. 测试：run 抛 ToolError → is_error=True 且 content 为其 message
3. 测试：run 抛意外异常（ValueError）→ is_error=True、不向外抛、content 含异常信息
4. 测试：未注册工具名 → is_error=True 含「未注册」
5. 测试：arguments=None → is_error=True 含「参数」「解析」
6. 测试：睡眠 FakeTool（timeout_s=0.2，睡 5s）→ 0.5s 内返回 is_error=True 含「超时」
7. 测试：requires_confirmation 工具 + confirm 返回 False → denied=True、run **未被调用**（计数器验证）；confirm 收到含工具名的描述串
8. 测试：只读工具不触发 confirm
9. 跑测试确认失败
**GREEN：** ToolExecutor（守护线程 join 超时 + 确认门 + 全路径吞异常）+ ToolOutcome
**验证：** `uv run pytest tests/test_tools_executor.py -q` 全绿

## T37: C11 AnthropicProvider 工具声明与解析

**文件：** `src/wentian/providers/anthropic.py`、`tests/test_provider_anthropic.py`
**依赖：** T30
**RED（mock SDK）：**
1. 测试：`stream(…, tools=[spec])` → kwargs 含 `tools=[{name, description, input_schema}]`；tools=None → kwargs 无 tools 键
2. 测试：mock final_message 含 text + 两个 tool_use 块 → 事件序列为 …TextDelta…、ToolCallEvent×2（id/name/arguments 对应 block.input）、Done
3. 测试：含 tool_use 时 Done.raw_content 为 final.content 各块 dump（含 type 字段）；纯文本回复 Done.raw_content 为 None
4. 跑测试确认失败
**GREEN：** `_build_kwargs` 工具转换 + 流尾解析 final.content + raw_content 装配
**验证：** `uv run pytest tests/test_provider_anthropic.py -q` 全绿

## T38: C11 AnthropicProvider 中性历史转换

**文件：** `src/wentian/providers/anthropic.py`、`tests/test_provider_anthropic.py`
**依赖：** T37
**RED：**
1. 测试：assistant 消息带 raw_content → 请求 messages 中该条 content 原样等于 raw_content（thinking 块保真）
2. 测试：assistant 带 tool_calls 无 raw_content → 重建为 [text?, tool_use…] 块；text 为空时无 text 块
3. 测试：连续两条 `role:"tool"` → 合并为一条 user 消息含两个 tool_result 块（tool_use_id/is_error 透传）
4. 测试：纯 user/assistant 历史 → 转换结果与 v0.2 行为完全一致（回归保护）
5. 跑测试确认失败
**GREEN：** `_convert_messages`（stream 内统一走它）
**验证：** `uv run pytest tests/test_provider_anthropic.py -q` 全绿

## T39: C11 OpenAICompatProvider 工具声明与碎片拼接

**文件：** `src/wentian/providers/openai_compat.py`、`tests/test_provider_openai_compat.py`
**依赖：** T30
**RED（mock 流 chunk）：**
1. 测试：请求含 `tools=[{type:"function", function:{…}}]`；tools=None 不带
2. 测试：三个 chunk 分片到达（首片带 index/id/name，后两片各带 arguments 碎片）→ 流尾产出一个 ToolCallEvent，arguments 为拼接后的 dict
3. 测试：两个工具调用交错分片（index 0/1）→ 两个 ToolCallEvent 按 index 序
4. 测试：arguments 拼接后非法 JSON → ToolCallEvent.arguments 为 None（不抛）
5. 跑测试确认失败
**GREEN：** delta.tool_calls 按 index 累积 + 流尾 json.loads + 事件发出（Done 之前）
**验证：** `uv run pytest tests/test_provider_openai_compat.py -q` 全绿

## T40: C11 OpenAICompatProvider 历史转换

**文件：** `src/wentian/providers/openai_compat.py`、`tests/test_provider_openai_compat.py`
**依赖：** T39
**RED：**
1. 测试：assistant 带 tool_calls → payload 含 `tool_calls=[{id, type:"function", function:{name, arguments: json 字符串}}]`；raw_content 被忽略
2. 测试：tool 消息 → `{role:"tool", tool_call_id, content}`；is_error=True → content 前缀 `[error] `
3. 测试：纯文本历史 → 与 v0.2 透传行为一致（回归保护）
4. 跑测试确认失败
**GREEN：** `_build_messages` 扩展为完整转换
**验证：** `uv run pytest tests/test_provider_openai_compat.py -q` 全绿

## T41: C12 渲染层——收集与屏显（F27）

**文件：** `src/wentian/render.py`、`tests/test_render.py`
**依赖：** T30
**RED：**
1. 测试：FakeProvider 事件含 ToolCallEvent×2 → RenderResult.tool_calls 按序收集；正文 text 不受影响；流式渲染期间 ToolCallEvent 不产生输出
2. 测试：Done.raw_content → RenderResult.raw_content 透传
3. 测试：render_tool_call 输出含 `⏺`、工具名、参数摘要（超长参数值截断）
4. 测试：render_tool_result 三态——成功（⎿ + 内容首行）/失败（含「失败」样式标记）/拒绝（含「拒绝」）输出可区分
5. 跑测试确认失败
**GREEN：** RenderResult 扩展 + 事件分支 + 两个新渲染方法
**验证：** `uv run pytest tests/test_render.py -q` 全绿

## T42: C12 REPL 单轮回合——主路径（F23 核心）

**文件：** `src/wentian/repl.py`、`tests/test_repl.py`
**依赖：** T36、T41
**RED（脚本化 FakeProvider：首调返回 text+2 个 tool_calls，二调返回纯文本；FakeExecutor 记录调用）：**
1. 测试：REPL(registry=None) 一轮对话 → 行为与 v0.2 全等（不传 tools、历史只有 user/assistant）——回归保护
2. 测试：带 registry 的一轮 → provider.stream 收到 tools=registry.specs()
3. 测试：工具轮 → executor 按序收到两个调用；历史依次为 user / assistant(text+tool_calls+raw_content) / tool×2 / assistant(round2 text)；已落盘
4. 测试：round2 的 stream 调用收到含 tool 消息的完整历史与同份 tools
5. 测试：无工具调用的回复 → 单次 stream、不调 executor
6. 跑测试确认失败
**GREEN：** `_chat_once` 拆出 `_run_tool_round`；REPL 构造增 registry/executor 参数（默认 None）
**验证：** `uv run pytest tests/test_repl.py -q` 全绿

## T43: C12 REPL 单轮回合——边界路径

**文件：** `src/wentian/repl.py`、`tests/test_repl.py`
**依赖：** T42
**RED：**
1. 测试：round2 再请求工具 → executor 只被调 round1 的次数；console 输出含单轮限制提示；round2 assistant 消息只存文本（无 tool_calls 键）
2. 测试：round1 中断（FakeListener + 中断标记）→ executor 未被调、tool_calls 丢弃、沿用 v0.2 部分正文/零正文语义
3. 测试：round2 抛异常 → user 消息与工具交互**不**回滚、已落盘、屏显错误、REPL 续命
4. 测试：denied 的 outcome → 以 is_error 入史回灌（content 含「拒绝」）
5. 跑测试确认失败
**GREEN：** 边界分支补全
**验证：** `uv run pytest tests/test_repl.py -q` 全绿

## T44: F28 会话持久化往返

**文件：** `tests/test_session.py`
**依赖：** T30
**RED：**
1. 测试：含 assistant(tool_calls+raw_content) 与 tool(is_error) 消息的会话 save → load → messages 深度相等
2. 测试：v0.2 旧格式会话文件（无新字段）load 正常（向后兼容）
3. 跑测试确认失败（若 session 层纯透传则可能直接绿——直接绿则记录为「契约测试」不算违例，因为它锁定的是序列化行为）
**GREEN/确认：** 透传即合规；如需改动仅限 to_dict/from_dict
**验证：** `uv run pytest tests/test_session.py -q` 全绿

## T45: C13 cli 装配收口

**文件：** `src/wentian/cli.py`、`tests/test_cli.py`
**依赖：** T42、T33、T34、T35
**RED：**
1. 测试：默认 build_app → REPL 收到的 registry 含六个工具名（read_file/write_file/edit_file/run_command/find_files/search_text）
2. 测试：注入 `tool_registry=`/`tool_executor=` → 透传给 REPL
3. 测试：非 TTY 下默认 confirm 恒 False（构造 executor 后验证）
4. 测试：工具启用时 system prompt 含当前工作目录路径
5. 跑测试确认失败
**GREEN：** build_app 装配六工具 + executor + 确认函数（TTY input / 非 TTY 拒）+ system prompt 附加段
**验证：** `uv run pytest tests/test_cli.py -q` 全绿

## T46: 全量回归 + 收尾

**文件：** 全部
**依赖：** T30–T45
1. `uv run pytest -q` 全量全绿、输出无告警
2. 管道冒烟：`echo "你好" | uv run wentian`（无 key 时验证错误路径干净，无崩溃无残留）
3. 复查教学隔离规约：每任务一提交、docstring 标记齐全
4. 更新 spec/README.md 文档进度表
**验证：** 全量测试输出 + git log 整洁

## v0.3 执行顺序

```
T30 ─┬→ T31 ─┬→ T32 → T33 ─┐
     │       ├→ T34（并行）  ├→ T45 ─→ T46
     │       ├→ T35（并行）  │
     │       └→ T36 ────────┤
     ├→ T37 → T38（并行）    ├→ T42 → T43 ─┘（T42 依赖 T36+T41）
     ├→ T39 → T40（并行）    │
     ├→ T41 ────────────────┘
     └→ T44（任意时点）
```

# v0.4 Tasks（F29–F34：Agent Loop）

> 依据已批准的 spec（F29–F34）与 plan（C14–C20）。延续教学隔离规约：一任务一提交，commit 前缀 `[T47/C14/F30]` 式三段标记；新文件 docstring 首行注明 `v0.4 · C编号 · F编号（任务 T编号）`。并行任务遵守 pathspec 提交协议（`git add -- <own files>` + `git commit -m msg -- <own files>`）。

## v0.4 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `src/wentian/agent/__init__.py` | 包初始化（docstring only，无 re-export，防并行冲突） |
| 新建 | `src/wentian/agent/events.py` | StopReason + AgentEvent 联合 + RoundResult |
| 新建 | `src/wentian/agent/bridge.py` | StreamBridge + call_in_thread |
| 新建 | `src/wentian/agent/collector.py` | RoundCollector 双路收集器 |
| 新建 | `src/wentian/agent/batch.py` | classify / partition_waves / Wave |
| 新建 | `src/wentian/agent/loop.py` | AgentLoop（五停机条件 + 原子入史） |
| 修改 | `src/wentian/render.py` | StreamView 抽取 + render_usage |
| 修改 | `src/wentian/repl.py` | asyncio.run 驱动 + /plan //do + 状态栏 + 删 _run_tool_round |
| 修改 | `src/wentian/cli.py`、`pyproject.toml`、`src/wentian/__init__.py` | 版本 0.4.0 |
| 修改 | `tests/conftest.py` | ScriptedProvider 提升 + run_to_list helper |
| 新建 | `tests/test_agent_events.py` 等 5 个 | agent 层测试（与模块一一对应） |
| 新建 | `tests/test_repl_plan_mode.py` | 计划模式测试 |
| 修改 | `tests/test_render.py`、`tests/test_repl.py`、`tests/test_cli.py` | v0.4 增量与单轮→多轮语义迁移 |

## T47: C14 事件契约（agent/events.py）

**文件：** `src/wentian/agent/__init__.py`、`src/wentian/agent/events.py`、`tests/test_agent_events.py`
**依赖：** 无
**RED：**
1. 测试：StopReason 恰有五个成员（completed/max_rounds/user_cancelled/unknown_tool_loop/stream_error）
2. 测试：RoundStart/UsageUpdate/StreamEnd/ToolCallStarted/ToolResultReady/RoundEnd/AgentDone 可构造、frozen（赋值抛 FrozenInstanceError）
3. 测试：AgentDone 默认 error=None；RoundResult(text, tool_calls, raw_content, usage, done_seen) 可构造、frozen
4. 测试：ToolCallStarted.call 接受 providers.base.ToolCallEvent；事件联合可 isinstance 分发（一个 match/if 链覆盖全部类型）
5. 跑测试确认失败
**GREEN：** events.py（只 import stdlib + providers.base）；__init__.py docstring only
**REFACTOR：** docstring 标记 `v0.4 · C14 · F30（任务 T47）`
**验证：** `uv run pytest tests/test_agent_events.py -q` 全绿

## T48: C15 同步→异步桥（agent/bridge.py）

**文件：** `src/wentian/agent/bridge.py`、`tests/test_agent_bridge.py`
**依赖：** T47
**RED（脚本化同步生成器；测试用 asyncio.run 包裹，不引 pytest-asyncio）：**
1. 测试：三事件生成器 → `asyncio.run(收集 drain(None))` 按原序得三事件
2. 测试：interrupt 预先置位 → drain 立即返回、零事件产出；中途置位 → 不再产出后续事件
3. 测试：生成器抛 RuntimeError → drain 重抛同异常
4. 测试：阻塞生成器（产出两事件后 Event.wait 挂死）→ drain 拿到两事件后 interrupt 置位 → 正常返回（守护线程遗留，不 join）
5. 测试：`call_in_thread(慢函数)` 返回其返回值；函数抛错 → 异常透出（executor 永不抛，此处仅契约验证）
6. 跑测试确认失败
**GREEN：** StreamBridge（守护线程 + queue.Queue 三元组 + get_nowait 清空 + 0.05s sleep 轮询）+ call_in_thread（专用守护线程 + holder 轮询）。**禁用 asyncio.to_thread / call_soon_threadsafe（plan R1）**
**REFACTOR：** docstring 注明与 render._StreamPump 的教学镜像关系
**验证：** `uv run pytest tests/test_agent_bridge.py -q` 全绿

## T49: C14 双路收集器（agent/collector.py）

**文件：** `src/wentian/agent/collector.py`、`tests/test_agent_collector.py`
**依赖：** T47
**RED：**
1. 测试：feed(TextDelta) 返回同一事件（透传）且累积进 text；feed(ThinkingDelta) 透传且不进 text
2. 测试：feed(ToolCallEvent) 返回 None（静默）且按序收集
3. 测试：feed(Done(usage, raw_content)) 返回 None、捕获 usage 与 raw_content、done_seen=True
4. 测试：result(interrupted=False) 返回 RoundResult 各字段正确；未见 Done 时 done_seen=False、usage/raw_content 为 None
5. 跑测试确认失败
**GREEN：** RoundCollector
**验证：** `uv run pytest tests/test_agent_collector.py -q` 全绿

## T50: C18 渲染 StreamView 抽取 + render_usage（F34）

**文件：** `src/wentian/render.py`、`tests/test_render.py`
**依赖：** T47（仅时序，文件不相交可与 T48/T49/T51 并行）
**RED：**
1. 测试：StreamView 在 record Console 上 start → feed(Thinking/Text…) → finish，输出文本与 render_stream 消费同序列事件的输出一致（含 thinking dim 样式与正文 Markdown）
2. 测试：finish(interrupted=True) 且有正文 → 输出含「已中断」标记；返回值为累积正文
3. 测试：render_usage(Usage(...), rounds=3) 输出单行含输入/输出数字与轮数；usage=None 不输出
4. 跑测试确认失败
**GREEN：** 抽取 StreamView；render_stream 改薄拉式包装；新增 new_stream_view / render_usage
**REFACTOR：** 删除抽取后的重复私有方法
**验证：** `uv run pytest tests/test_render.py -q` 全绿，且**既有 render 测试零修改**；`uv run pytest -q` 全量不破（repl 测试同样零修改——重构硬验收）

## T51: C16 安全分批（agent/batch.py）

**文件：** `src/wentian/agent/batch.py`、`tests/test_agent_batch.py`
**依赖：** T47
**RED（FakeRegistry：get 按 dict 查；FakeTool 带 requires_confirmation 属性）：**
1. 测试：classify 四态——已注册只读 → read_only；requires_confirmation=True → side_effect；未注册 → unknown；allowed 名单外 → blocked（优先级：unknown > blocked > 读写判定，按 plan 顺序）
2. 测试：partition_waves([读,读,写,读]) → [并发(读,读), 串行(写), 串行(读)]；全读 → 单并发 Wave；含 unknown → 独立串行 Wave 保位
3. 测试：requires_confirmation 属性缺失的工具 → side_effect（fail-safe）
4. 测试（并发证明）：两个 sleep(0.2) 只读 FakeTool 经并发 Wave 执行 → 总墙钟 < 0.35s；结果列表按原调用顺序
5. 跑测试确认失败
**GREEN：** classify / Wave / partition_waves + 并发执行辅助（asyncio 任务先发后按序 await）
**验证：** `uv run pytest tests/test_agent_batch.py -q` 全绿

## T52: C17 AgentLoop 主路径（F29 核心）

**文件：** `src/wentian/agent/loop.py`、`tests/test_agent_loop.py`、`tests/conftest.py`（ScriptedProvider 提升 + run_to_list）
**依赖：** T48、T49、T51
**RED（ScriptedProvider 三脚本：text+2 tool_calls → text+1 tool_call → 纯文本；FakeExecutor 记录调用并回成功）：**
1. 测试：事件序列符合文法——RoundStart(1)…StreamEnd(1)、⏺/⎿×2、RoundEnd(1)、RoundStart(2)…RoundEnd(2)、RoundStart(3)…AgentDone(COMPLETED, rounds=3)
2. 测试：messages 形状——user / assistant(text+tool_calls+raw_content 原样) / tool×2 / assistant(+tool_calls) / tool / assistant(纯文本)；tool 消息按调用原序、tool_call_id 对应
3. 测试：arguments=None 的调用 → 入史存 {}、executor 收到 None（v0.3 契约）
4. 测试：TextDelta 在对应轮 StreamEnd 之前出现在事件流中（双路实时性证据，AC28）
5. 测试：无 tool_calls 的单轮 → 一次 RoundStart + AgentDone(COMPLETED, rounds=1)、入史仅 assistant 文本
6. 跑测试确认失败
**GREEN：** AgentLoop.run 主循环（桥+收集器+分批+原子入史）；conftest 提升 ScriptedProvider、加 run_to_list
**REFACTOR：** test_repl.py 原 ScriptedProvider 定义改为 conftest import
**验证：** `uv run pytest tests/test_agent_loop.py tests/test_repl.py -q` 全绿

## T53: C17 AgentLoop 停机条件（F29 边界）

**文件：** `src/wentian/agent/loop.py`、`tests/test_agent_loop.py`
**依赖：** T52
**RED：**
1. 测试：max_rounds=2、脚本持续要工具 → 第 2 轮不执行工具（executor 调用数=第 1 轮的数量）、第 2 轮 assistant 只存文本（无 tool_calls 键）、AgentDone(MAX_ROUNDS, rounds=2)
2. 测试：连续两轮全部调用未注册名 → AgentDone(UNKNOWN_TOOL_LOOP)；错误结果已按对入史；第二轮前穿插一次已注册调用 → 计数重置、循环走到 COMPLETED
3. 测试：FakeListener 在第 2 轮置位中断、该轮有部分文字 → 该轮 assistant 只存文本、第 1 轮工具交互保留、AgentDone(USER_CANCELLED)；零文字中断 → 该轮不入史（messages 长度=第 1 轮结束时）
4. 测试：第 2 轮 stream 抛错 → 第 1 轮成块保留、第 2 轮零入史、AgentDone(STREAM_ERROR, error 含异常信息)；首轮即抛错 → messages 仅含 user（调用方回滚用）
5. 测试：blocked 调用（allowed_tools 名单外）→ executor 未被调、合成错误结果含「计划模式」与可用工具名、不计入 unknown 连击
6. 跑测试确认失败
**GREEN：** 五停机分支 + unknown 连击计数 + blocked 合成结果
**验证：** `uv run pytest tests/test_agent_loop.py -q` 全绿

## T54: C17 用量累计（F34）

**文件：** `src/wentian/agent/loop.py`、`tests/test_agent_loop.py`
**依赖：** T53
**RED：**
1. 测试：三轮各报 Usage → 每轮 UsageUpdate(round, total) 数值正确、AgentDone.usage 为总和
2. 测试：部分轮 usage=None → 跳过该轮 UsageUpdate、总和只计有报轮次
3. 测试：全程无 usage → 零 UsageUpdate、AgentDone.usage=None
4. 跑测试确认失败
**GREEN：** 跨轮累计 + 条件发出
**验证：** `uv run pytest tests/test_agent_loop.py -q` 全绿

## T55: C19 REPL 接入 AgentLoop（F29）

**文件：** `src/wentian/repl.py`、`tests/test_repl.py`
**依赖：** T52、T53、T54、T50
**RED（ScriptedProvider + FakeListener + tmp store）：**
1. 测试：多轮工具回合 → 历史完整成对入史、store.save 被调 ≥ 轮数（逐轮落盘）+ 终了一次；屏显含 ⏺/⎿ 与多轮正文
2. 测试：`_run_tool_round` 不复存在（hasattr 断言）；registry=None 纯对话回合行为与 v0.2 全等（回归）
3. 测试：首轮零文字中断 → user 消息弹出、未落盘（len-baseline 回滚）；首轮流错误 → 同回滚 + 屏显错误
4. 测试：第 2 轮才出错 → user 与第 1 轮轨迹保留、已落盘、屏显错误、REPL 续命
5. 测试：MAX_ROUNDS / UNKNOWN_TOOL_LOOP 停机 → 对应黄提示文案出现
6. 测试：回合结束屏显用量行（脚本含 usage 时）；无 usage 不显示
7. 跑测试确认失败
**GREEN：** `_chat_once` 重写（asyncio.run + _consume_agent + len-baseline 回滚 + 停机提示）；删 _run_tool_round；构造增 max_rounds/plan_tools
**验证：** `uv run pytest tests/test_repl.py -q` 全绿
**注意：** v0.3 单轮语义的既有用例按多轮语义迁移（「round2 再请求工具→提示」改为「达上限→提示」等），迁移在本任务内完成并在提交信息中说明

## T56: C19 计划模式（F33 核心）

**文件：** `src/wentian/repl.py`、`tests/test_repl_plan_mode.py`
**依赖：** T55
**RED：**
1. 测试：/plan 后下一回合 provider.stream 收到的 tools 仅含三只读名、system 含计划模式后缀；/do 后恢复全量 tools、system 无后缀
2. 测试：计划模式中脚本请求 write_file → executor 未被调、入史 tool 消息 is_error=True 且 content 含「计划模式」、循环继续（AgentDone 非 UNKNOWN_TOOL_LOOP）
3. 测试：status_line() 计划模式含「计划模式」标记、/do 后消失
4. 测试：`/do 按计划执行` → 退出计划模式且「按计划执行」作为用户消息触发一次回合；裸 /do 仅切换不发消息
5. 测试：/help 输出含 /plan 与 /do；/plan 重复执行幂等
6. 跑测试确认失败
**GREEN：** _plan_mode 状态 + _effective_tools_and_system + allowed_tools 注入 + 两个命令 handler + status_line 扩展
**验证：** `uv run pytest tests/test_repl_plan_mode.py tests/test_repl.py -q` 全绿

## T57: C20 装配与版本收口

**文件：** `src/wentian/cli.py`、`src/wentian/__init__.py`、`pyproject.toml`、`tests/test_cli.py`、`tests/test_smoke.py`
**依赖：** T55、T56
**RED：**
1. 测试：`__version__ == "0.4.0"`（smoke/横幅断言更新）
2. 测试：默认 build_app → REPL 多轮回合可用（注入 ScriptedProvider 端到端跑通一次三轮回合）
3. 跑测试确认失败
**GREEN：** 版本号双处 + build_app 必要微调（如有）
**验证：** `uv run pytest tests/test_cli.py tests/test_smoke.py -q` 全绿
**约束：** pyproject diff 仅版本号，零新依赖

## T58: 全量回归 + 收尾

**文件：** 全部 + `spec/README.md`、`spec/checklist.md`
**依赖：** T47–T57
1. `uv run pytest -q` 全量全绿、无告警（基线 408+ 全部保持）
2. 管道冒烟：`printf '/exit\n' | uv run wentian` 横幅示 0.4.0、退出码 0、无 traceback
3. 分层 import 现场检查：agent 层零 SDK/rich/prompt_toolkit/wentian.tools import；repl/render 维持既有约束
4. 复查教学隔离规约：一任务一提交、docstring 标记、pathspec 协议执行情况
5. 更新 spec/README.md 进度表 + checklist.md 离线项取证
**验证：** 全量测试输出 + git log 整洁

## v0.4 执行顺序

```
T47 ─┬→ T48（bridge）   ┐
     ├→ T49（collector）├─（四任务并行，文件不相交）→ T52 → T53 → T54 → T55 → T56 → T57 → T58
     ├→ T50（render）   │        （loop.py 串行）      （repl.py 串行）
     └→ T51（batch）    ┘
```

- 波次 1：T47（单任务，契约先行）
- 波次 2：T48 / T49 / T50 / T51 并行（pathspec 提交协议）
- 波次 3：T52 → T53 → T54 串行（同文件 loop.py）
- 波次 4：T55 → T56 串行（同文件 repl.py）
- 波次 5：T57 → T58 串行收口

# v0.5 Tasks（F35–F40：结构化系统提示 + 提示词缓存）

## v0.5 文件清单

| 操作 | 文件 | 职责 |
| 新建 | `src/wentian/prompt/__init__.py` | prompt 包导出 |
| 新建 | `src/wentian/prompt/system.py` | 七固定模块 + 可选槽位 + `build_system_prompt`（C21）|
| 新建 | `tests/test_prompt_system.py` | 模块拼装/顺序/解耦/人格/约定测试 |
| 新建 | `src/wentian/prompt/reminders.py` | `EnvInfo` + `<system-reminder>` 构造 + cadence + `build_request_decorator`（C22）|
| 新建 | `tests/test_prompt_reminders.py` | 提醒格式/cadence/注入/不改原 测试 |
| 改 | `src/wentian/providers/base.py` | `Usage` 扩两缓存字段（C25）|
| 改 | `src/wentian/providers/anthropic.py` | system→带 cache_control 块数组；解析缓存字段（C23）|
| 改 | `src/wentian/providers/openai_compat.py` | 解析 `prompt_tokens_details.cached_tokens`（C24）|
| 改 | `tests/test_provider_*.py` | 缓存断点结构 + 命中解析测试 |
| 改 | `src/wentian/agent/loop.py` | `request_decorator` 注入 + usage 累计含缓存（C25）|
| 改 | `tests/test_agent_loop.py` | decorator 注入/不持久化/None 回归 |
| 改 | `src/wentian/render.py` | `render_usage` 缓存命中显示（C26）|
| 改 | `src/wentian/repl.py` | system 来自 prompt 包 + decorator 注入 + 计划模式提醒迁移（C27）|
| 改 | `src/wentian/cli.py` + `pyproject.toml` | `build_app` 用 `build_system_prompt`；版本 0.5.0（C28）|

## T59: C21 系统提示模块化组装 + 文天人格（F35/F36/F37）

**文件：** `src/wentian/prompt/system.py`、`src/wentian/prompt/__init__.py`、`tests/test_prompt_system.py`
**依赖：** 无
**RED（先写失败测试）：**
1. 测试：`build_system_prompt(PromptContext(cwd=…, tool_names=("read_file","edit_file",…)))` 返回串依次包含七个固定模块标识，模块间以空行（`\n\n`）分隔、顺序固定
2. 测试：可选模块本版渲染为空 → 输出不含空行残渣 / 孤立分隔（结尾无多余 `\n\n`）
3. 测试：传 `modules=` 注入一个假模块 → 出现在输出中，证明拼装器与模块定义解耦（AC33）
4. 测试：身份/语气模块含文天人格关键串（如「文天」「=^_^=」「照顾」等约定关键词）（AC34 离线半）
5. 测试：「工具使用」模块文本含关键约定句（如「编辑文件前先读取」「优先用专用工具」）（AC35 一半）
6. 测试：`tool_names` 注入的工具名出现在「工具使用」模块中
7. 跑测试确认因功能缺失失败
**GREEN：** 实现 `PromptContext`、七固定模块 `(name, render)`、可选模块（render 恒空）、`build_system_prompt`（渲染→过滤空→`"\n\n"` 连接）；`__init__.py` 导出
**REFACTOR：** 模块文案抽常量；保持绿
**验证：** `uv run pytest tests/test_prompt_system.py -q` 全绿
**注意：** `prompt/` 包零后端 SDK / 零 rich / 零 prompt_toolkit import（分层铁律）

## T60: C22 动态提醒请求时拼装（F39）

**文件：** `src/wentian/prompt/reminders.py`、`tests/test_prompt_reminders.py`
**依赖：** 无（与 T59 并行；同包不同文件）
**RED：**
1. 测试：`render_env_reminder(EnvInfo(...))` 含 `<system-reminder>` 开闭标签与四项（工作目录/操作系统/日期/git 分支）
2. 测试：`render_switch_reminder(plan_mode=True, round_index=1)` 返回完整提醒；`round_index=6` 完整；`round_index=2..5` 返回一行精简；`plan_mode=False` 返回 None
3. 测试：`build_request_decorator(env, plan_mode=True)` 产出的 `decorator(messages, 1)` → 新列表首条 user 前置 env 提醒、末条 user 追加 switch 提醒；**入参 messages 对象不被改动**（断言原 list 与其元素 content 不变——持久化安全）
4. 测试：`plan_mode=False` → decorator 只注 env、不注 switch
5. 测试：messages 无 user 消息 → 不抛错（env 提醒跳过）
6. 跑测试确认失败
**GREEN：** 实现 `EnvInfo`、两个 render、`build_request_decorator`（深拷需要改的 user 消息、其余浅引用；绝不 mutate 入参）
**REFACTOR：** 标签/文案抽常量；保持绿
**验证：** `uv run pytest tests/test_prompt_reminders.py -q` 全绿

## T61: C25 Usage 扩缓存字段（F40）

**文件：** `src/wentian/providers/base.py`、`tests/test_providers.py`（或对应既有用量测试文件）
**依赖：** 无
**RED：**
1. 测试：`Usage(input_tokens=1, output_tokens=2)` 默认 `cache_creation_input_tokens==0`、`cache_read_input_tokens==0`
2. 测试：`Usage(1, 2, 3, 4)` 位置构造与关键字构造均可、字段对应正确（既有构造点兼容）
3. 跑测试确认失败（新字段不存在）
**GREEN：** `Usage` 加两个默认 0 字段（置于既有两字段之后）
**REFACTOR：** 无
**验证：** `uv run pytest tests/ -q -k usage or providers` 相关全绿；既有用量测试不破

## T62: C23 Anthropic 缓存断点 + 命中解析（F38/F40）

**文件：** `src/wentian/providers/anthropic.py`、`tests/test_provider_anthropic.py`
**依赖：** T61
**RED：**
1. 测试：`_build_kwargs(messages, system="X")` → `kwargs["system"] == [{"type":"text","text":"X","cache_control":{"type":"ephemeral"}}]`（断点在 system 块）
2. 测试：`system=None` → 不含 `system` 键（v0.4 回归）
3. 测试：喂一个含 `cache_creation_input_tokens`/`cache_read_input_tokens` 的假 `message_start` usage → 解析出的 `Usage` 携带对应值；字段缺失 → 按 0
4. 跑测试确认失败
**GREEN：** 改 `_build_kwargs` 的 system 分支为块数组 + cache_control；流式 usage 解析读两缓存字段
**REFACTOR：** 保持绿
**验证：** `uv run pytest tests/test_provider_anthropic.py -q` 全绿
**注意：** 既有断言「system 为字符串」的测试按新结构迁移，迁移在本任务内完成并在提交体说明

## T63: C24 OpenAI 兼容缓存解析（F38/F40）

**文件：** `src/wentian/providers/openai_compat.py`、`tests/test_provider_openai.py`
**依赖：** T61（与 T62 并行；不同文件）
**RED：**
1. 测试：`system` 仍作单条 `{"role":"system","content":...}` 消息（回归，不打缓存标）
2. 测试：假 usage 含 `prompt_tokens_details.cached_tokens` → 映射到 `Usage.cache_read_input_tokens`，`cache_creation` 恒 0；字段缺失 → 按 0
3. 跑测试确认失败
**GREEN：** 用量解析读 `prompt_tokens_details.cached_tokens`（防御性 getattr/get）
**REFACTOR：** 保持绿
**验证：** `uv run pytest tests/test_provider_openai.py -q` 全绿

## T64: C25 循环装配回调 + 跨轮缓存累计（F39/F40）

**文件：** `src/wentian/agent/loop.py`、`tests/test_agent_loop.py`
**依赖：** T60、T61
**RED：**
1. 测试：`AgentLoop.run(messages, request_decorator=deco)` → 每轮 `provider.stream` 收到的 messages 是 `deco(messages, n)` 的产出（用记录式 decorator/provider 断言每轮 round_index 递增、outgoing≠原件）
2. 测试：循环结束后**入史与落盘的 `messages` 原件不含任何提醒块**（持久化纯净）
3. 测试：`request_decorator=None` → 发请求的 messages 即原件，事件序列与 v0.4 全等（回归）
4. 测试：跨轮 usage 累计把 `cache_read/creation` 也相加（多轮脚本带缓存字段）
5. 跑测试确认失败
**GREEN：** `run` 增 `request_decorator` 参，stream 前算 outgoing；usage 累计含缓存两字段
**REFACTOR：** 保持绿
**验证：** `uv run pytest tests/test_agent_loop.py -q` 全绿

## T65: C26 渲染缓存命中（F40）

**文件：** `src/wentian/render.py`、`tests/test_render.py`
**依赖：** T61
**RED：**
1. 测试：`render_usage(Usage(...,cache_read_input_tokens=10), rounds=2)` 输出含缓存读（与创建，>0 时）信息
2. 测试：缓存字段全 0 / usage=None → 输出与 v0.4 逐字一致（既有 render_usage 测试零修改保持绿）
3. 跑测试确认失败
**GREEN：** `render_usage` 缓存>0 时追加 `· 缓存读 X · 缓存写 Y`
**REFACTOR：** 保持绿
**验证：** `uv run pytest tests/test_render.py -q` 全绿（含既有项零修改）

## T66: C27 REPL 接线 + 计划模式提醒迁移（F35/F39/F33 迁移）

**文件：** `src/wentian/repl.py`、`tests/test_repl.py`、`tests/test_repl_plan_mode.py`
**依赖：** T59、T60、T64
**RED：**
1. 测试：REPL 的 `system` 含七模块结构（来自 build_system_prompt）、**不含计划模式后缀文案**（AC40 断言）
2. 测试：每回合向 `agent.run` 传入非 None 的 `request_decorator`；发给 provider 的 messages 含 `<system-reminder>`，但 store 落盘的 messages 不含（AC37）
3. 测试（F33 回归）：`/plan` 后声明过滤仍只暴露三只读工具、越权 write_file 仍被 blocked 拦截回灌、`status_line` 计划模式标记不变
4. 测试：计划模式下 switch 提醒经 decorator 注入（多轮按 cadence 变化）（AC38）
5. 跑测试确认失败
**GREEN：** `system` 改由注入的结构化提示承载；`_effective_tools_and_system` 删 system 后缀分支（只管 tools）；构造并传 `request_decorator`；计划模式文案迁 reminders
**REFACTOR：** 保持绿
**验证：** `uv run pytest tests/test_repl.py tests/test_repl_plan_mode.py -q` 全绿
**注意：** F33 既有用例中「system 含计划后缀」的断言迁移为「switch 提醒含计划文案」，迁移在本任务内完成、提交体说明

## T67: C28 装配与版本收口（F35）

**文件：** `src/wentian/cli.py`、`src/wentian/__init__.py`、`pyproject.toml`、`tests/test_cli.py`
**依赖：** T59、T66
**RED：**
1. 测试：`build_app(...)` 装配的 REPL `system` 非空且含七模块结构（替换 `_tools_system_prompt`）
2. 测试：`__version__ == "0.5.0"`
3. 跑测试确认失败
**GREEN：** `build_app` 用 `build_system_prompt(PromptContext(cwd=root, tool_names=…))`；删/弃用 `_tools_system_prompt`；版本号 0.5.0（源码 + pyproject + lock 同步）
**REFACTOR：** 保持绿
**验证：** `uv run pytest tests/test_cli.py -q` 全绿

## T68: 全量回归 + 收尾

**文件：** 全仓
**依赖：** T59–T67
**步骤（非 TDD，验证收口）：**
1. `uv run pytest -q` → 504 + v0.5 新增全绿、无告警
2. 分层现场检查：`prompt/` 包零 SDK/rich/prompt_toolkit import；agent 层仍零 `wentian.tools` import（grep 取证）
3. 管道冒烟：`printf '/exit\n' | uv run wentian` → 横幅示 v0.5.0、退出码 0、无 traceback
4. `pyproject` diff 仅版本号、零新增依赖
**验证：** 上述四项各留现场证据，记入 checklist

## v0.5 执行顺序

```
T59（system）┐
T60（reminders）├─（prompt 包 + Usage，三任务并行，文件不相交）
T61（Usage）  ┘
        │
        ├→ T62（anthropic，依赖 T61）┐
        ├→ T63（openai，依赖 T61）   ├─（provider/render 并行）
        └→ T65（render，依赖 T61）   ┘
T64（loop，依赖 T60+T61）
        │
T66（repl，依赖 T59+T60+T64）→ T67（cli/版本，依赖 T59+T66）→ T68（全量回归收尾）
```

- 波次 1：T59 / T60 / T61 并行（prompt 包两文件 + base.py，互不相交）
- 波次 2：T62 / T63 / T65 并行（均只依赖 T61，文件不相交）
- 波次 3：T64 串行（loop.py，依赖 T60 的 decorator 形态 + T61 的 Usage）
- 波次 4：T66 → T67 串行（repl.py → cli.py/版本）
- 波次 5：T68 全量回归收尾
