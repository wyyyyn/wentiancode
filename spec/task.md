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

# v0.6 Tasks（F41–F49：权限系统 · 五层防御）

## v0.6 文件清单

| 操作 | 文件 | 职责 |
| 新建 | `src/wentian/permissions/__init__.py` | permissions 纯包导出 |
| 新建 | `src/wentian/permissions/decision.py` | Mode/Category/Verdict/Source/Decision + MODE_CYCLE（C29）|
| 新建 | `src/wentian/permissions/blacklist.py` | 内置危险命令正则 + `check_command`（C29）|
| 新建 | `tests/test_perm_blacklist.py` | 黑名单命中/不可关测试 |
| 新建 | `src/wentian/permissions/sandbox.py` | `check_path`：解析软链接+前缀+最近祖先（C30）|
| 新建 | `tests/test_perm_sandbox.py` | 沙箱围栏/软链接逃逸/新建文件测试 |
| 新建 | `src/wentian/permissions/rules.py` | Rule/RuleSet/LayeredRules + 友好名路由 + glob（C31）|
| 新建 | `tests/test_perm_rules.py` | 精确/glob/友好名/同层 deny>allow 测试 |
| 新建 | `src/wentian/permissions/settings.py` | 三层 YAML 加载合并 + 降级（C32）|
| 新建 | `tests/test_perm_settings.py` | 三层优先级/缺失/格式非法降级测试 |
| 新建 | `src/wentian/permissions/modes.py` | 四档×三类兜底表（C33）|
| 新建 | `tests/test_perm_modes.py` | 模式矩阵逐格/值域 {Allow,Ask} 测试 |
| 新建 | `src/wentian/permissions/pipeline.py` | 五层短路编排（C34）|
| 新建 | `tests/test_perm_pipeline.py` | 短路/跳层不误拦/安全默认测试 |
| 改 | `src/wentian/tools/base.py` | Tool 加 category/friendly_name/参数抽取；requires_confirmation 派生（C35）|
| 改 | `src/wentian/tools/files.py`·`search.py`·`shell.py` | 六工具声明 category + friendly_name（C35）|
| 改 | `src/wentian/tools/executor.py` | 删确认门、回归纯执行+超时（C35）|
| 改 | `tests/test_tools_*.py` | 工具元数据 + executor 去门迁移 |
| 改 | `src/wentian/agent/loop.py` | run_call 接 permission_gate + Deny 合成回灌（C36）|
| 改 | `tests/test_agent_loop.py` | gate=None 回归 / Deny 回灌 / 保序 / 只读并发 |
| 新建 | `src/wentian/ui/confirm.py` | 三选一审批菜单（C37）|
| 新建 | `tests/test_ui_confirm.py` | ↑↓/数字键/默认高亮/Esc 取消测试 |
| 改 | `src/wentian/ui/input.py` | Shift+Tab 绑定 → on_mode_cycle（C38）|
| 改 | `src/wentian/repl.py` | ask 回调+永久落盘 / 权限模式状态栏 / plan 统一（C37/C38）|
| 改 | `tests/test_repl.py`·`test_repl_plan_mode.py` | 状态栏/Shift+Tab/plan 统一/永久落盘/F33 回归 |
| 改 | `src/wentian/cli.py`·`__init__.py`·`pyproject.toml`·`.gitignore` | 装配 gate+初始模式；版本 0.6.0；gitignore local（C39）|

## T69: C29 判定类型 + 危险命令黑名单（F41/N10）

**文件：** `src/wentian/permissions/decision.py`、`blacklist.py`、`__init__.py`、`tests/test_perm_blacklist.py`
**依赖：** 无
**RED（先写失败测试）：**
1. 测试：`check_command("rm -rf /")` 及变体（`rm -fr /`、`rm -rf ~`、`rm -rf $HOME`）返回 `Decision(verdict=DENY, source=BLACKLIST)`
2. 测试：`dd of=/dev/sda`、`mkfs.ext4 /dev/sdb`、`:(){ :|:& };:`、`> /dev/sda` 命中 Deny
3. 测试：`ls -la`、`git status`、`echo hi` 返回 None（不拦）
4. 测试：黑名单无任何开关/配置参数可关（模块无 enable/disable 接口——结构性断言）
5. 跑测试确认因功能缺失失败
**GREEN：** 实现 `decision.py` 全枚举与 `Decision`、`MODE_CYCLE`；`blacklist.py` 的 `_DANGEROUS` 正则组 + `check_command`
**REFACTOR：** 正则抽常量、加注释说明各模式；保持绿
**验证：** `uv run pytest tests/test_perm_blacklist.py -q` 全绿
**注意：** 纯包零 SDK/rich/prompt_toolkit import（分层铁律）；文档化黑名单为启发式、非完备

## T70: C30 路径沙箱（F42/N11）

**文件：** `src/wentian/permissions/sandbox.py`、`tests/test_perm_sandbox.py`
**依赖：** T69（用 Decision 类型）
**RED：**
1. 测试：项目内已存在文件 `check_path("sub/a.txt", root)` 返回 None（放行）
2. 测试：`/etc/passwd`、`../outside`、绝对路径出根 → `Decision(DENY, SANDBOX)`
3. 测试：项目内建软链接指向 `/etc` → 经其访问目标 → Deny（**先解析后比对**）
4. 测试：项目内尚不存在的新文件、含多级未创建中间目录 → None（按最近已存在祖先解析、不误判）
5. 跑测试确认失败
**GREEN：** 实现 `check_path`（规整→存在则 resolve / 不存在则上溯最近祖先 resolve→`is_relative_to` 前缀判断）
**REFACTOR：** 抽辅助；保持绿
**验证：** `uv run pytest tests/test_perm_sandbox.py -q` 全绿（用 `tmp_path` 真实建目录/软链接）

## T71: C31 规则引擎 + 友好名路由 + glob（F43/F44 同层）

**文件：** `src/wentian/permissions/rules.py`、`tests/test_perm_rules.py`
**依赖：** T69
**RED：**
1. 测试：`Bash(git status)` 精确放行 `git status`、不放行 `git push`；`Bash(git *)` 放行所有 git 子命令
2. 测试：`Write(src/**)` 放行 `src/a/b.py`、不放行 `docs/x`；命令串里 `**` 等价 `*`（不跨目录解释）
3. 测试：友好名 Bash/Read/Write/Edit/Glob/Grep → 内置工具名映射正确
4. 测试：`RuleSet` 同层 deny 优先于 allow（同一 target 同时被 allow 与 deny 命中 → DENY）
5. 测试：`LayeredRules` local>project>user 就近命中即止（本地 allow 盖项目 deny）
6. 跑测试确认失败
**GREEN：** 实现 `Rule`/`RuleSet`/`LayeredRules`、友好名映射、精确+glob（文件类跨目录 `**`、命令类 `**`≡`*`）匹配
**REFACTOR：** glob 转换抽函数；保持绿
**验证：** `uv run pytest tests/test_perm_rules.py -q` 全绿

## T72: C32 三层配置加载合并 + 降级（F44/N14）

**文件：** `src/wentian/permissions/settings.py`、`tests/test_perm_settings.py`
**依赖：** T71（用 RuleSet/LayeredRules）
**RED：**
1. 测试：三层文件各设不同 `defaultMode` → 生效层为 本地>项目>用户（逐层断言）；皆无 → `Mode.DEFAULT`
2. 测试：三层 allow/deny 合并为 `LayeredRules`，优先级正确（复用 T71 语义，端到端断言）
3. 测试：文件缺失 → 该层空集；三层全缺 → 空规则 + default 模式
4. 测试：某层 YAML 非法（解析错）/结构错（permissions 非 dict）→ **该层降级空集、其余正常、不抛、不致 `load_settings` 失败**
5. 跑测试确认失败
**GREEN：** 实现 `Settings`、`load_settings`（三文件 safe_load→RuleSet，异常/结构错 try 包成空集，defaultMode 取首个合法）
**REFACTOR：** 路径默认值抽函数（复用 config.py 的 XDG 风格）；保持绿
**验证：** `uv run pytest tests/test_perm_settings.py -q` 全绿（`tmp_path` 写三文件）

## T73: C33 模式兜底表（F45）

**文件：** `src/wentian/permissions/modes.py`、`tests/test_perm_modes.py`
**依赖：** T69
**RED：**
1. 测试：`mode_fallback(mode, category)` 四档×三类共 12 格逐格断言（按 spec F45 矩阵）
2. 测试：所有返回值 ∈ {ALLOW, ASK}，**绝不出现 DENY**（值域断言）
3. 跑测试确认失败
**GREEN：** 实现 `dict[Mode, dict[Category, Verdict]]` 查表 + `mode_fallback`
**REFACTOR：** 无
**验证：** `uv run pytest tests/test_perm_modes.py -q` 全绿

## T74: C34 五层流水线编排（F46）

**文件：** `src/wentian/permissions/pipeline.py`、`tests/test_perm_pipeline.py`
**依赖：** T69、T70、T71、T72、T73
**RED：**
1. 测试：黑名单命中 → 不再进沙箱/规则（短路，source=BLACKLIST）
2. 测试：deny 规则命中 → 不进模式兜底；allow 规则命中 → 不进模式兜底（直接放行）
3. 测试：跳层不误拦——非命令类不被黑名单拦、命令类不被沙箱拦，均继续进后续层
4. 测试：规则未命中 → 落模式兜底，返回该格 Allow/Ask
5. 测试：安全默认——类别无法判定/参数不可解析时按副作用处理（走 Ask 或 Deny），不静默放行
6. 跑测试确认失败
**GREEN：** 实现 `PermissionPipeline.decide`（①黑名单仅命令类 ②沙箱仅文件类 ③规则 ④模式兜底，逐层短路）
**REFACTOR：** 保持绿
**验证：** `uv run pytest tests/test_perm_pipeline.py -q` 全绿

## T75: C35 Tool 元数据 + executor 去确认门（F43/F45 分类、F26 取代）

**文件：** `src/wentian/tools/base.py`、`files.py`、`search.py`、`shell.py`、`executor.py`、`tests/test_tools_*.py`
**依赖：** T69（用 Category）
**RED：**
1. 测试：六工具各暴露正确 `category`（读/找/搜→READ_ONLY，写/改→FILE_WRITE，命令→COMMAND_EXEC）与 `friendly_name`
2. 测试：`requires_confirmation` 派生正确（`category != READ_ONLY`）——`batch.classify` 既有行为不变（回归）
3. 测试：参数抽取——命令类抽出 command 串、文件类抽出 path 列表
4. 测试：`ToolExecutor` **不再有 confirm 门**（删 confirm 参数后纯执行+超时；既有健壮性用例迁移保持绿）
5. 跑测试确认失败
**GREEN：** `Tool` 加 `category`/`friendly_name`/`command_arg`/`path_args` 与 `requires_confirmation` 派生属性；六工具声明；`executor` 删 confirm 分支
**REFACTOR：** 保持绿
**验证：** `uv run pytest tests/test_tools_base.py tests/test_tools_files.py tests/test_tools_search.py tests/test_tools_shell.py tests/test_tools_executor.py -q` 全绿
**注意：** executor 既有「确认门拒绝」用例迁移/删除在本任务内完成、提交体说明（F26→五层取代）

## T76: C36 AgentLoop 判定门接入 + Deny 回灌（F46/F49）

**文件：** `src/wentian/agent/loop.py`、`tests/test_agent_loop.py`
**依赖：** T74、T75
**RED：**
1. 测试：`AgentLoop(..., permission_gate=None)` → 行为与 v0.5 完全一致（事件序列、入史，回归）
2. 测试：假 gate 对某调用返回**成形的拒绝结果对象**（鸭子兼容 ToolOutcome：call_id/name/content/is_error=True/denied）→ loop **原样回灌该对象**、**不调 executor.execute**；gate 返回 None → 正常进 executor
3. 测试：单批 [读A, 写B(门返回拒绝), 读C] → denied 与 allow 结果按原调用序、原 call.id 配对入史、互不串位（AC51）
4. 测试：只读 wave（假 gate 对只读同步返 None=放行）仍并发、不被门串行化（AC53）
5. 跑测试确认失败
**GREEN：** `__init__` 加 `permission_gate`；`run_call` 在 blocked 判定后、executor 前接 gate：`denied = await gate(call); if denied is not None: return denied`，None 才进 executor。**loop 不合成 outcome、不 import permissions**（拒绝对象由装配层 gate 构造，见 C36/C37）
**REFACTOR：** 保持绿
**验证：** `uv run pytest tests/test_agent_loop.py -q` 全绿
**注意：** agent 层仍零 `wentian.tools`/`wentian.permissions` import——gate 为 duck-typed async 回调，返回值鸭子兼容 ToolOutcome

## T77: C37 人在回路 UI + ask 回调 + 永久落盘（F48/N13）

**文件：** `src/wentian/ui/confirm.py`、`src/wentian/repl.py`、`tests/test_ui_confirm.py`、`tests/test_repl.py`
**依赖：** T76
**RED：**
1. 测试：`ui/confirm` 渲染多行块（工具名+参数预览+原因+三选项），默认高亮「允许本次」
2. 测试：↑↓ 移光标 + 回车选中；数字键 1/2/3 直选；返回 `{ALLOW_ONCE, ALLOW_ALWAYS, DENY}`（prompt_toolkit pipe input）
3. 测试：Esc/Ctrl+C → 抛 `Cancelled`、REPL 干净结束本轮、不退出程序、无 task 泄漏（AC52）
4. 测试：REPL 的 ask 回调——ALLOW_ALWAYS 把**精确**规则写入 `<根>/.wentian/settings.local.yaml` 的 `permissions.allow` 且内存即时生效（重载 settings 断言含该规则）
5. 跑测试确认失败
**GREEN：** 实现 `ui/confirm`（仿 `ui/select.py`）；REPL 构 ask 回调（调 confirm + 永久落盘 + 内存追加），包进 gate 注入
**REFACTOR：** 保持绿
**验证：** `uv run pytest tests/test_ui_confirm.py tests/test_repl.py -q` 全绿

## T78: C38 Shift+Tab 模式切换 + 状态栏 + plan 统一（F47）

**文件：** `src/wentian/ui/input.py`、`src/wentian/repl.py`、`tests/test_ui_input.py`、`tests/test_repl.py`、`tests/test_repl_plan_mode.py`
**依赖：** T77
**RED：**
1. 测试：`input` 的 Shift+Tab 绑定触发 `on_mode_cycle` 回调（pipe input 模拟）
2. 测试：REPL Shift+Tab 循环 default→acceptEdits→plan→bypassPermissions→default；**跨轮保持**（下一轮 mode 不被重置）
3. 测试：`status_line` 首段显当前权限模式、**不再含 provider 名**；会话/消息数照旧
4. 测试（plan 统一）：`/plan`→mode=PLAN、`/do`→mode=DEFAULT（固定回 default）；mode==PLAN 时 F33 机制全绿（声明过滤仅三只读 / blocked 拦截 / 计划提醒经 system-reminder）——回归 AC40
5. 跑测试确认失败
**GREEN：** `input` 加 `s-tab` 绑定 + `on_mode_cycle` 属性；REPL `_mode` 状态 + 推进逻辑；`status_line` 改首段；`_plan_mode` 收编为 `_mode==PLAN` 派生；`/plan`·`/do` 改设 `_mode`
**REFACTOR：** 保持绿
**验证：** `uv run pytest tests/test_ui_input.py tests/test_repl.py tests/test_repl_plan_mode.py -q` 全绿
**注意：** F33 既有「`_plan_mode` 布尔」相关断言迁移为「`_mode==PLAN`」，迁移在本任务内完成、提交体说明

## T79: C39 装配与版本收口（F44/N17）

**文件：** `src/wentian/cli.py`、`src/wentian/__init__.py`、`pyproject.toml`、`.gitignore`、`tests/test_cli.py`
**依赖：** T74、T77、T78
**RED：**
1. 测试：`build_app` 装配 `PermissionPipeline(load_settings(cwd))` + ask 回调 → gate 注入 AgentLoop；初始 `_mode == settings.default_mode`
2. 测试：非交互/非 TTY → ask 恒 Deny（安全默认 N16）
3. 测试：`__version__ == "0.6.0"`；`.gitignore` 含 `.wentian/settings.local.yaml`
4. 跑测试确认失败
**GREEN：** `build_app` 装配 pipeline+gate+初始模式；删 `_make_confirm` 与 executor confirm 注入；版本 0.6.0（源码+pyproject+lock）；.gitignore 追加
**REFACTOR：** 保持绿
**验证：** `uv run pytest tests/test_cli.py -q` 全绿

## T80: 全量回归 + 收尾

**文件：** 全仓
**依赖：** T69–T79
**步骤（非 TDD，验证收口）：**
1. `uv run pytest -q` → 574 + v0.6 新增全绿、无告警
2. 分层现场检查：`permissions/` 包零 SDK/rich/prompt_toolkit import；agent 层仍零真实 `wentian.tools`/`wentian.permissions` import（grep 取证）
3. `ruff format --check .` 通过、`ruff check .` 无告警（N17）
4. 管道冒烟：`printf '/exit\n' | uv run wentian` → 横幅示 v0.6.0、状态栏显权限模式、退出码 0、无 traceback
5. `pyproject` diff 仅版本号、零新增依赖；`.gitignore` 含 settings.local
**验证：** 上述各项各留现场证据，记入 checklist

## v0.6 执行顺序

```
波次1（permissions 纯包 leaf，文件不相交）：
  T69（types+黑名单）┐
  T70（沙箱，依赖T69）├─ T69 先；T70/T71/T73 依赖 T69 后可并行
  T71（规则，依赖T69）│
  T73（模式表，依赖T69）┘
        │
  T72（settings，依赖T71）
        │
波次2：T74（pipeline，依赖 T69-T73 全部）
波次3：T75（Tool 元数据+executor，依赖 T69）  ← 可与波次1/2 并行（仅依赖 T69、改 tools 层）
波次4：T76（loop 判定门，依赖 T74+T75）
波次5：T77（人在回路 UI+repl，依赖 T76）→ T78（Shift+Tab+状态栏+plan 统一，依赖 T77）
波次6：T79（cli/版本，依赖 T74+T77+T78）
波次7：T80（全量回归收尾）
```

- 波次 1：T69 先落（其余三个 permissions 模块依赖其类型）；之后 T70/T71/T73 并行、T72 依赖 T71；T75（tools 层，仅依赖 T69）可与本波并行
- 波次 4 起进入串行接线区（loop→repl→cli），改同一批界面/装配文件，串行保正确性
- 人在回路 UI（T77）与 Shift+Tab/状态栏（T78）都改 repl.py，串行；UI 组件 `ui/confirm.py`、`ui/input.py` 互不相交但经 repl 汇合

# v0.7 Tasks（F50–F55：MCP 客户端接入）

> 前置：v0.6 权限系统（T69–T80）跑完后再开 v0.7（用户拍板：先落 v0.7 spec、暂不开发）。本节为待执行任务。新增 `src/wentian/mcp/` 纯包 + 升级 `config.py` 两层加载 + `cli.py` 装配。stdlib-only，零新增第三方依赖。

## v0.7 文件清单

| 操作 | 文件 | 职责 |
| 新建 | `src/wentian/mcp/__init__.py` | mcp 纯包导出 |
| 新建 | `src/wentian/mcp/protocol.py` | JSON-RPC 2.0 编解码 + Response/Notification（C40）|
| 新建 | `tests/test_mcp_protocol.py` | 构造/解析往返/error/通知/畸形测试 |
| 新建 | `src/wentian/mcp/transport.py` | Transport ABC + Stdio + Http（urllib+SSE）（C41）|
| 新建 | `tests/test_mcp_transport.py` | stdio 假脚本端到端 + http.server JSON/SSE 测试 |
| 新建 | `tests/_fake_mcp_server.py` | 测试用 stdlib 假 MCP Server 脚本（stdio）+ http 假服务 helper |
| 新建 | `src/wentian/mcp/client.py` | MCPClient：三步会话 + id→等待槽配对 + 超时（C42）|
| 新建 | `tests/test_mcp_client.py` | 三步/乱序回包配对/超时/error 测试 |
| 新建 | `src/wentian/mcp/adapter.py` | MCPTool(Tool)：远端工具→统一工具 + 命名空间 + 错误转 ToolError（C43）|
| 新建 | `tests/test_mcp_adapter.py` | 命名空间/schema 透传/readOnlyHint→category/content 拼接/错误 |
| 改 | `src/wentian/config.py` | load_config 升两层深合并 + MCPServerConfig + ${VAR} 展开 + mcpServers 校验（C44）|
| 改 | `tests/test_config.py` | 两层合并/向后兼容/stdio·http 解析/${VAR}/字段缺失 |
| 新建 | `src/wentian/mcp/manager.py` | MCPManager.discover_and_register + 故障隔离 + close_all（C45）|
| 新建 | `tests/test_mcp_manager.py` | 一坏一好隔离/注册/close_all 终止子进程/空 no-op |
| 改 | `src/wentian/cli.py`·`__init__.py`·`pyproject.toml`·`uv.lock` | build_app 装配 manager + 退出 close_all；版本 0.7.0（C46）|
| 改 | `tests/test_cli.py`·`test_smoke.py` | 有/无 mcpServers 装配；无配置冒烟同 v0.6；close_all 被调用 |

## T81: C40 JSON-RPC 2.0 编解码（F51）

**文件：** `src/wentian/mcp/protocol.py`、`__init__.py`、`tests/test_mcp_protocol.py`
**依赖：** 无
**RED（先写失败测试）：**
1. 测试：`build_request("tools/list", None, id=1)` 产 `{"jsonrpc":"2.0","id":1,"method":"tools/list"}`（params=None 时省略 params 键）；带 params 时含 params
2. 测试：`build_notification("notifications/initialized", None)` 无 `id` 键、含 method
3. 测试：`parse_message({"jsonrpc":"2.0","id":1,"result":{...}})` → `Response(id=1, result=..., error=None)`；带 error → `Response(error={code,message})`
4. 测试：`parse_message({"jsonrpc":"2.0","method":"x","params":{}})`（无 id）→ `Notification(method="x", ...)`；畸形（无 id 无 method）→ None
5. 跑测试确认因功能缺失失败
**GREEN：** 实现 `build_request`/`build_notification`/`parse_message` 与 `Response`/`Notification` frozen dataclass
**REFACTOR：** 抽 `JSONRPC_VERSION` 常量；保持绿
**验证：** `uv run pytest tests/test_mcp_protocol.py -q` 全绿
**注意：** 纯函数零 IO 零线程；mcp 包 leaf（仅 import stdlib）

## T82: C44 两层配置加载 + MCP 配置 + ${VAR}（F50/N23）

**文件：** `src/wentian/config.py`、`tests/test_config.py`
**依赖：** 无（与 T81 并行，不同文件）
**RED：**
1. 测试：仅用户级文件存在时 `load_config()` 结果与旧单文件等价（providers/default 不变、`mcp_servers` 为空 dict）——向后兼容
2. 测试：用户级 + 项目级 `.wentian/config.yaml` 两文件 → providers 同名键被项目级覆盖、新增并入；`default` 项目级存在则覆盖
3. 测试：`mcpServers` 解析——带 `command` 的条目 → `StdioServerConfig`（args 默认 []、env 默认 {}）；带 `url` 的 → `HttpServerConfig`（headers 默认 {}）；两层合并对 mcpServers 同样生效
4. 测试：env/headers 值含 `${VAR}` → 从 `os.environ` 展开；缺失变量 → 空串 + 告警（不抛）
5. 测试：stdio 缺 command / http 缺 url → `ConfigError`（字段级消息）
6. 跑测试确认失败
**GREEN：** `load_config` 加项目级文件读取 + 深合并；`Config` 加 `mcp_servers`；新增 `StdioServerConfig`/`HttpServerConfig` + 解析 + `${VAR}` 展开 + 校验
**REFACTOR：** 深合并与 `${VAR}` 展开各抽辅助函数；保持绿
**验证：** `uv run pytest tests/test_config.py -q` 全绿（`tmp_path` + monkeypatch 环境变量）
**注意：** 显式 `path` 入参仍走单文件直载（不触发两层，供 `-c`/测试）；XDG 风格沿用既有 `_default_config_path`

## T83: C41 StdioTransport（F52）

**文件：** `src/wentian/mcp/transport.py`、`tests/_fake_mcp_server.py`、`tests/test_mcp_transport.py`
**依赖：** T81（用 protocol 编解码可选）
**RED：**
1. 先写 `tests/_fake_mcp_server.py`：一个 stdlib 脚本，循环读 stdin 行 → `json.loads` → 按 method 回 JSON 行（initialize/tools/list/tools/call 最小实现）；可经参数模拟「不回包」「先写 stderr」
2. 测试：`StdioTransport(cfg)` start 后 `send` 一条 request、经 `on_message` 回调收到对应 response（端到端 subprocess）
3. 测试：假 server 往 stderr 写大量内容 → 不阻塞、不污染 on_message（stderr 被独立抽干）
4. 测试：`close()` 后子进程已终止（`poll()` 非 None）
5. 跑测试确认失败
**GREEN：** 实现 `Transport` ABC + `StdioTransport`（Popen + stdout 读取线程逐行 json + stderr 抽干线程 + send 加锁写行 + close terminate→kill）
**REFACTOR：** 读取线程循环抽函数；保持绿
**验证：** `uv run pytest tests/test_mcp_transport.py -k stdio -q` 全绿
**注意：** env 用 `{**os.environ, **cfg.env}`；text 模式按行分帧

## T84: C41 HttpTransport（F52）

**文件：** `src/wentian/mcp/transport.py`、`tests/test_mcp_transport.py`（续）
**依赖：** T81
**RED：**
1. 测试 helper：用 `http.server.HTTPServer` + 线程起本地假 server，可配置「返即时 JSON」或「返 SSE 事件流」，并记录收到的请求头
2. 测试：`HttpTransport(cfg)` `send` 一条 request → 即时 JSON 响应分支 → `on_message` 收到 response
3. 测试：SSE 响应分支（`Content-Type: text/event-stream`，`data: {json}\n\n`）→ 正确解析出 JSON-RPC 消息投 on_message
4. 测试：配置的 `headers`（如 `Authorization`）出现在假 server 收到的请求头
5. 跑测试确认失败
**GREEN：** 实现 `HttpTransport`（urllib POST + Accept 头；按响应 Content-Type 分流即时 JSON / SSE 行解析；close 关流）
**REFACTOR：** SSE 解析抽函数（data 累积、空行分隔事件）；保持绿
**验证：** `uv run pytest tests/test_mcp_transport.py -k http -q` 全绿
**注意：** 假 server 用回环地址 + 端口 0 自动分配；测试结束 shutdown server 线程

## T85: C42 MCPClient 三步会话 + id 配对（F51/N20）

**文件：** `src/wentian/mcp/client.py`、`tests/test_mcp_client.py`
**依赖：** T81、T83（或用假 transport）
**RED：**
1. 写假 transport（实现 Transport 接口、`send` 时按预设把 response 经 on_message 回投，可控制乱序/延迟）
2. 测试：`initialize()` 发 initialize 请求收能力、随后发出 `notifications/initialized`（假 transport 断言收到该通知）
3. 测试：`list_tools()` 解析 `result.tools[]` → `RemoteTool`，含 `read_only`（取 `annotations.readOnlyHint`，缺省 False）
4. 测试：`call_tool(name, args)` 取 `result.content[]` text 块拼文本返回；`result.isError` 或 JSON-RPC error → raise
5. 测试：**乱序回包**——并发/乱序的多个请求，响应按 id 正确配对（不串位）；请求超时 → raise（清理 pending）
6. 跑测试确认失败
**GREEN：** 实现 `MCPClient`（`_next_id` 加锁、`_pending: dict[int,_Waiter]`、`_route` 按 id 唤醒、`_send_request` 等 event 超时、initialize/list_tools/call_tool、close 唤醒所有挂起）
**REFACTOR：** `_Waiter`（Event+result 位）抽出；保持绿
**验证：** `uv run pytest tests/test_mcp_client.py -q` 全绿
**注意：** MCP 包内不引 asyncio——纯 threading + Event；多线程并发各占独立 id/waiter（N20）

## T86: C43 MCPTool 适配（F53/F54）

**文件：** `src/wentian/mcp/adapter.py`、`tests/test_mcp_adapter.py`
**依赖：** T85（用 RemoteTool/MCPClient）、T75（用 `Category`；v0.6 已落）
**RED：**
1. 测试：`MCPTool("fs", remote, client)` 的 `name == "fs__read_file"`（命名空间）、`description`/`parameters` 透传远端
2. 测试：`remote.read_only=True` → `category==READ_ONLY` 且 `requires_confirmation` 派生为 False；`read_only=False` → `FILE_WRITE` 且 `requires_confirmation` True（F54 安全默认）
3. 测试：`run(args)` 调 `client.call_tool("read_file", args)`（去命名空间用原始远端名）并返其文本
4. 测试：`client.call_tool` raise（传输/远端错）→ `MCPTool.run` raise `ToolError`（可读原因，不崩溃）
5. 跑测试确认失败
**GREEN：** 实现 `MCPTool(Tool)`：构造定 name/description/parameters/category/timeout_s；`run` 调 client + 异常转 ToolError
**REFACTOR：** 命名空间前缀拼接抽常量分隔符 `__`；保持绿
**验证：** `uv run pytest tests/test_mcp_adapter.py -q` 全绿
**注意：** adapter 是 mcp 包唯一跨层 import 处（`wentian.tools.base` 的 Tool/ToolError + `Category`——与 `Tool.category` 同源，v0.6 落在 `wentian.permissions.decision`），合法跨层、与内置工具同规

## T87: C45 MCPManager 发现 + 故障隔离 + 生命周期（F55/N21）

**文件：** `src/wentian/mcp/manager.py`、`tests/test_mcp_manager.py`
**依赖：** T82（MCPServerConfig）、T85（MCPClient）、T86（MCPTool）
**RED：**
1. 测试：`discover_and_register({好Server}, registry)` → 注册到 registry 的工具名带命名空间、`report.ok[name]==工具数`
2. 测试：两 Server 一坏（command 不存在 / initialize 不回触发超时 / http 不可达）一好 → 坏的进 `report.failed[name]`（含原因）、好的正常注册；**不抛、不影响好 Server**（N21 隔离）
3. 测试：坏 Server 的 transport 被 close（不泄漏子进程）
4. 测试：`close_all()` 终止所有缓存 client 的子进程（断言 `poll()` 非 None）；幂等可重复调
5. 测试：空 `servers` → no-op（registry 不变、report 全空）
6. 跑测试确认失败
**GREEN：** 实现 `MCPManager`（遍历 servers，按类型建 transport→client→initialize→list_tools→MCPTool→register，try/except 记 failed+close，成功缓存 client+ok）、`close_all`、`DiscoveryReport`
**REFACTOR：** 单 Server 发现抽 `_connect_one`；保持绿
**验证：** `uv run pytest tests/test_mcp_manager.py -q` 全绿（用 `_fake_mcp_server.py` 真子进程 + 坏配置）
**注意：** 每 Server 发现用 client 超时兜底防卡死；register 撞名（理论上不会）记 warning 跳过

## T88: C46 cli 装配 + 生命周期接线 + 版本（F55/N23）

**文件：** `src/wentian/cli.py`、`src/wentian/__init__.py`、`pyproject.toml`、`uv.lock`、`tests/test_cli.py`、`tests/test_smoke.py`
**依赖：** T87
**RED：**
1. 测试：`build_app` 在配了 mcpServers 时调 `manager.discover_and_register`、把命名空间工具注册进 registry（用假 server 配置）
2. 测试：无 mcpServers → 不建 manager / no-op，registry 仅六内置工具，行为同 v0.6
3. 测试：REPL 退出路径调用 `manager.close_all()`（mock manager 断言被调；含异常退出 try/finally）
4. 测试：版本字符串为 `0.7.0`
5. 跑测试确认失败
**GREEN：** `build_app` 接 `cfg.mcp_servers` → MCPManager → 发现注册 → 汇报 report；REPL 持有 manager、退出 close_all（try/finally + atexit 兜底）；版本升 0.7.0（源码+pyproject+lock）
**REFACTOR：** report 汇报文案抽函数；保持绿
**验证：** `uv run pytest tests/test_cli.py tests/test_smoke.py -q` 全绿
**注意：** 零新增第三方依赖（pyproject diff 仅版本号）

## T89: 全量回归 + 收尾

**文件：** 全仓
**依赖：** T81–T88
**步骤（非 TDD，验证收口）：**
1. `uv run pytest -q` → v0.1–v0.6 全部 + v0.7 新增全绿、无告警
2. 分层现场检查：`mcp/` 包除 `adapter.py`（import `tools.base` + `Category`）外零跨层 import；零第三方 MCP/HTTP 库（grep 取证）；MCP 包内零 asyncio
3. `ruff format --check .` 通过、`ruff check .` 无告警
4. 管道冒烟（无 mcpServers）：`printf '/exit\n' | uv run wentian` → 横幅示 v0.7.0、退出码 0、无 traceback、行为同 v0.6
5. 离线 MCP 冒烟：配一个 `_fake_mcp_server.py` 的 stdio Server → 启动见接入汇报、其工具进 registry；退出无残留子进程
6. `pyproject` diff 仅版本号、零新增依赖
**验证：** 上述各项各留现场证据，记入 checklist

## v0.7 执行顺序

```
波次1（并行，文件不相交）：
  T81（protocol，无依赖）
  T82（config 两层+MCP，无依赖）
波次2（并行，依赖 T81）：
  T83（StdioTransport）
  T84（HttpTransport）
波次3：T85（MCPClient，依赖 T81+T83）
波次4：T86（MCPTool 适配，依赖 T85，且需 v0.6 的 Category）
波次5：T87（MCPManager，依赖 T82+T85+T86）
波次6：T88（cli 装配+版本，依赖 T87）
波次7：T89（全量回归收尾）
```

- 波次 1 两任务完全独立（protocol 纯逻辑 / config 改既有文件），可并行
- 波次 2 两种 transport 改同一 `transport.py` 但分属不同类——若并行需注意文件合并；保险起见可串行（先 stdio 后 http）
- 波次 3 起串行：client→adapter→manager→cli 逐层依赖上一层产物
- T86 依赖 v0.6 已落的 `Category`（permissions/decision.py 或 providers.base）——v0.7 开工前确认 v0.6 已合入

---

# v0.8 任务（T90–T97：上下文管理 + 双层压缩）

> 教学隔离规约同 v0.6/v0.7：一任务一提交 `[T#/C#/F#/N#]`、一组件一文件、docstring 标记版本/组件/特性。TDD 红-绿-重构不豁免；每波次后规格/质量评审。

## T90: C47 token 估算 + provider 真实 prompt 总量 + Message.offloaded（F56/N26）

**文件：** `src/wentian/context/__init__.py`、`src/wentian/context/estimator.py`、`src/wentian/providers/base.py`、`src/wentian/providers/anthropic.py`、`tests/test_context_estimator.py`、`tests/test_providers_prompt_total.py`
**依赖：** 无
**RED：**
1. 测试：`char_estimate([msg])` 按 `content` 字符数 / `char_per_token` 向上取整；多条累加；空列表=0；`char_per_token` 可配
2. 测试：`estimate_total(prompt_total, new_messages, char_per_token) == prompt_total + char_estimate(new_messages)`
3. 测试：`AnthropicProvider.prompt_token_total(Usage(input=100, cache_read=900, cache_creation=50)) == 1050`（input 不含缓存、需相加）
4. 测试：`Provider`（base/openai）`prompt_token_total(Usage(input=1000, cache_read=900)) == 1000`（prompt_tokens 已含缓存读，不重复加）
5. 跑测试确认失败
**GREEN：** 建 `context/` 包；`estimator.py` 两个纯函数；`Provider.prompt_token_total` base 默认 `return usage.input_tokens`、`AnthropicProvider` 覆盖加缓存字段；`Message` TypedDict 加 `offloaded: bool`（total=False）
**REFACTOR：** 字符统计辅助抽出；保持绿
**验证：** `uv run pytest tests/test_context_estimator.py tests/test_providers_prompt_total.py -q` 全绿
**注意：** `context/` 包 leaf——只 import stdlib + `providers.base`（仅 `Message`/`Usage` 类型）；**核验依据**：anthropic.py:87 input 单列缓存、openai_compat.py:296 prompt_tokens 含 cached——两家语义差异在此消化

## T91: C51-config ContextConfig + ProviderConfig.context_window（F56/F58）

**文件：** `src/wentian/config.py`、`tests/test_config.py`（续）
**依赖：** 无（与 T90/T95 并行，不同文件/不同测试点）
**RED：**
1. 测试：配置无 `context:` 块 → `Config.context == ContextConfig()`（全默认：default_window 200000、reserved_output 64000、auto_margin 13000、manual_margin 3000、recent_keep_tokens 10000、recent_keep_min_messages 5、offload_single_tokens 2000、offload_round_sum_tokens 8000、char_per_token 3.5）
2. 测试：`context:` 块部分字段覆盖 → 仅覆盖项变、其余默认；两层深合并对 `context` 块逐键生效
3. 测试：`providers.X.context_window` 解析为 `ProviderConfig.context_window`；缺省为 None
4. 跑测试确认失败
**GREEN：** 新增 `@dataclass(frozen=True) ContextConfig`（带全默认）；`Config` 加 `context: ContextConfig`；`ProviderConfig` 加 `context_window: int|None=None`；解析 + 两层合并接 `context` 块
**REFACTOR：** 默认值集中；保持绿
**验证：** `uv run pytest tests/test_config.py -q` 全绿
**注意：** 整块/逐字段缺失全部安全降级为默认（不抛）；窗口语义=输入预算+输出，故触发阈值后续按 `window - reserved_output - margin` 算

## T92: C48 第一层·超大工具结果卸载（F57/N27）

**文件：** `src/wentian/context/offload.py`、`tests/test_context_offload.py`
**依赖：** T90（`char_estimate`、`Message.offloaded`）、T91（`ContextConfig`）
**RED：**
1. 测试：单条工具结果 `char_estimate > offload_single_tokens` → 原 content 写入 `artifacts_dir/tool-<id>.txt`、对话内 content 变为「预览 + 绝对路径 + 提示」、`offloaded=True`、`is_error` 保留；返回的 `OffloadAction` 含 id/path/tokens
2. 测试：**幂等**——对已 `offloaded` 的消息再扫不重复处理、不重复写盘
3. 测试：单轮多条工具结果各自不超单条阈值、合计超 `offload_round_sum_tokens` → 按 est 降序挑最大依次卸载直到合计回落、较小的原样保留
4. 测试：user / assistant 消息从不被改写（构造夹杂大 user 文本，断言原样，N27）
5. 测试：预览截断（前 ~20 行或 ~800 字取先到）
6. 跑测试确认失败
**GREEN：** 实现 `offload_oversized(messages, *, artifacts_dir, cfg)`：按相邻 tool 分组；单条闸 + 单轮合计闸（降序挑大）；写盘 + 预览替换 + 标记；返回 `list[OffloadAction]`
**REFACTOR：** 预览生成、文件写抽辅助；保持绿
**验证：** `uv run pytest tests/test_context_offload.py -q` 全绿（`tmp_path` 作 artifacts_dir）
**注意：** 只扫 `role=="tool"`；幂等靠 `offloaded` 标记；绝不动 user/assistant（N27）

## T93: C49 第二层·切割边界 + 八段摘要（F58/F59/F60）

**文件：** `src/wentian/context/summarizer.py`、`tests/test_context_summarizer.py`
**依赖：** T90（`char_estimate`）、T91（`ContextConfig`）
**开工前置：** 用 **context7** 查证 Anthropic Messages API 对**连续同角色消息**的容忍度（摘要 user 紧邻保留段首条 user）——容忍则按默认实现；否则 fallback 把 cut snap 到下一条 assistant。结论写进 summarizer docstring
**RED：**
1. 测试：`find_cut_index`——从尾部累计直到 `≥recent_keep_tokens` 且尾部 `≥recent_keep_min_messages`；保留段**首条非 tool**（构造保留段会以孤儿 tool 结果开头的历史 → snap 把孤儿推入摘要区）
2. 测试：不拆散「assistant(含 tool_calls) ↔ 其全部 tool 结果」——构造该对跨切割点，断言整对要么全摘要要么全保留
3. 测试：clamp——历史很短（< min 条）→ cut=0（不摘）；保留 token 目标远大于全历史 → cut=0
4. 测试：`summarize(fake_provider, earlier)`——发给 provider 的请求 `tools is None`、`system`/末条 user 指令含「禁止调用工具」「先草稿后正式」「八段」「<final_summary>」语义；假 provider 回 `草稿…<final_summary>正式…</final_summary>` → 抽出正式部分；空/异常 → `SummaryError`
5. 测试：`build_compacted(summary, kept)` → `[{role:user, content 含 <conversation_summary> + 边界提示「重新读取/勿脑补」}] + kept`
6. 跑测试确认失败
**GREEN：** 实现 `find_cut_index`（尾部累计 + snap 跳孤儿 tool + clamp）、`SUMMARY_SYSTEM`/指令常量、`summarize`（调 provider.stream 收文本 + 抽 final + 空判 raise）、`build_compacted`、`SummaryError`
**REFACTOR：** 八段模板、`<final_summary>` 抽取正则抽出；保持绿
**验证：** `uv run pytest tests/test_context_summarizer.py -q` 全绿（假 provider）
**注意：** **配对铁律来源**核验 anthropic.py:154-218——连续 tool 折叠成一条 user tool_result、每 `tool_use_id` 须配前序 assistant 的 tool_use；保留段绝不以 tool 开头

## T94: C50 编排 + 熔断 Compactor（F61/F62）

**文件：** `src/wentian/context/compactor.py`、`tests/test_context_compactor.py`
**依赖：** T90、T91、T92、T93
**RED：**
1. 测试：`compact(messages, last_usage)` 先调 L1 卸载（注入 spy 或断言超大 tool 结果被卸载）
2. 测试：估算 `est = prompt_token_total(last_usage) + char(messages[_last_seen_len:])`；`last_usage=None` → 全量字符；`_last_seen_len` 每次调用末更新为 `len(messages)`
3. 测试：阈值 `window - reserved_output - (manual?manual_margin:auto_margin)`；`est>threshold` 才触发 L2；自动 13K / 手动 3K 余量差异可断言
4. 测试：L2 成功 → `messages[:]` 变为「摘要+边界 + 保留段」、`summarized=True`、失败计数清零
5. 测试：**熔断**——注入「摘要必失败」假 provider：连续 3 次失败 → `_tripped=True`、后续**自动**调跳过 L2（但 L1 仍跑）；一次成功清零；`manual=True` 无视 `_tripped` 强制重试、成功则解除熔断
6. 跑测试确认失败
**GREEN：** 实现 `Compactor`（持 provider/artifacts_dir/window/cfg/_last_seen_len/_fail/_tripped）、`compact`（L1→估算→阈值→L2 try/except 熔断计数→更新 _last_seen_len）、`CompactionResult`
**REFACTOR：** 阈值计算、估算抽小函数；保持绿
**验证：** `uv run pytest tests/test_context_compactor.py -q` 全绿
**注意：** 鸭子持 provider（只用 `stream`/`prompt_token_total`）；与 registry/executor 无关；纯离线（假 provider）

## T95: C51-loop AgentLoop pre_round_compact 钩子（F62/N25）

**文件：** `src/wentian/agent/loop.py`、`tests/test_agent_loop.py`（续）
**依赖：** 无（与 T90/T91 并行；钩子是鸭子回调、不 import context 包）
**RED：**
1. 测试：`run(..., pre_round_compact=hook)` 每轮在 `RoundStart` 之后、构造 outgoing/调 provider 之前调用 `hook(messages, last_round_usage)`；首轮 `last_usage=None`、后续轮传上一轮 `round_result.usage`
2. 测试：钩子原地改写 `messages`（注入一个会删消息的假钩子 → 断言发给 provider 的 outgoing 基于改写后历史；改写在 `request_decorator` 之前）
3. 测试：**钩子为 None（默认）→ 与 v0.7 字节级等价**——既有 loop 测试**零修改**保持绿（回归断言）
4. 跑测试确认失败
**GREEN：** `run` 增可选参 `pre_round_compact`；保存跨轮 `last_round_usage`；每轮 RoundStart 后调钩子（非 None 时）再 `outgoing = request_decorator(...)`
**REFACTOR：** 保持绿；docstring 标注「先压缩、后包提醒」顺序与 None 等价契约
**验证：** `uv run pytest tests/test_agent_loop.py -q` 全绿
**注意：** loop 不持 Compactor、不解释 `CompactionResult`；钩子异常不特殊处理（compactor 内部已吞 SummaryError，钩子不抛）

## T96: C52 装配 repl/cli + /compact + 版本（F61/F62/N25）

**文件：** `src/wentian/repl.py`、`src/wentian/cli.py`、`src/wentian/__init__.py`、`pyproject.toml`、`uv.lock`、`tests/test_repl.py`、`tests/test_cli.py`、`tests/test_smoke.py`
**依赖：** T94（Compactor）、T95（loop 钩子）
**RED：**
1. 测试：`build_app` 按当前 provider 解析 `context_window`（provider 优先、否则 `ContextConfig.default_window`）、建会话产物目录、建 `Compactor` 并把 `compactor.compact`（manual=False）作为 `pre_round_compact` 注入 `AgentLoop.run`
2. 测试：`_consume_agent` 消费 `UsageUpdate` 时存 `self._last_round_usage`（屏显仍用 AgentDone.usage 不变）
3. 测试：`/compact` 命令 → 调 `compactor.compact(messages, last_round_usage, manual=True)` → 落盘 → 打印 `CompactionResult`（卸载数/是否摘要/是否熔断）；帮助表含 `/compact`
4. 测试：版本字符串 `0.8.0`
5. 测试：无 `context:` 配置全默认、行为不破坏（冒烟同 v0.7）
6. 跑测试确认失败
**GREEN：** build_app 装配 Compactor 注入；REPL 存 last_round_usage + `/compact` dispatch + 帮助条目 + provider 切换时更新 compactor；版本升 0.8.0（源码+pyproject+lock）
**REFACTOR：** CompactionResult 打印文案抽函数；保持绿
**验证：** `uv run pytest tests/test_repl.py tests/test_cli.py tests/test_smoke.py -q` 全绿
**注意：** 零新增第三方依赖（pyproject diff 仅版本号）；artifacts 目录 `<sessions_dir>/<session_id>.artifacts/`

## T97: 全量回归 + 收尾

**文件：** 全仓
**依赖：** T90–T96
**步骤（非 TDD，验证收口）：**
1. `uv run pytest -q` → v0.1–v0.7 全部 + v0.8 新增全绿、无告警（基线 823 → +N）
2. 分层现场检查：`context/` 包零 SDK/rich/prompt_toolkit import、只 import stdlib + `providers.base` 类型（grep 取证）；provider 差异在 provider 层消化；agent 层仅多一个可选钩子参数
3. `ruff format --check .` 通过、`ruff check .` 无告警
4. 管道冒烟（无 context 配置）：`printf '/exit\n' | uv run wentian` → 横幅示 v0.8.0、退出码 0、无 traceback、行为同 v0.7
5. 离线压缩冒烟：构造小窗口 + 超大工具结果 → 触发 L1 卸载（断言 artifacts 文件 + 对话留预览）；假 provider 触发 L2 → 历史变摘要+边界+保留段；`/compact` 手动触发可用
6. **字节级回归**：未注入压缩器时既有 loop/repl 测试零修改全绿
7. `pyproject` diff 仅版本号、零新增依赖
**验证：** 上述各项各留现场证据，记入 checklist

## v0.8 执行顺序

```
波次1（并行，文件不相交）：
  T90（estimator + provider.prompt_token_total + Message.offloaded，无依赖）
  T91（config ContextConfig + context_window，无依赖）
  T95（loop pre_round_compact 钩子，无依赖——鸭子回调不 import context）
波次2（并行，依赖 T90+T91）：
  T92（offload 第一层）
  T93（summarizer 第二层；开工前 context7 查证连续同角色）
波次3：T94（compactor 编排+熔断，依赖 T90/T91/T92/T93）
波次4：T96（repl/cli 装配 + /compact + 版本，依赖 T94+T95）
波次5：T97（全量回归收尾）
```

- 波次 1 三任务完全独立（estimator+provider / config / loop 钩子，文件不相交），可并行
- 波次 2 两任务都依赖 T90（估算）+T91（配置），但 offload 与 summarizer 文件不相交，可并行
- 波次 3 起串行：compactor 聚合 L1/L2 → repl/cli 装配 → 全量回归
- T93 开工前用 context7 查证 Anthropic 连续同角色容忍度（plan R2），结论落 docstring 并决定 cut snap 策略

---

# v0.9 任务（T98–T107：记忆与会话 + 项目指令）

> 教学隔离规约同 v0.6/v0.7/v0.8：一任务一提交 `[T#/C#/F#/N#]`、一组件一文件、docstring 标记版本/组件/特性。TDD 红-绿-重构不豁免；每波次后规格/质量评审。
> **复用底线**：会话恢复溢出判定与压缩**直接复用 v0.8 `context` 包**（`estimator.char_estimate`/`estimate_total`、`Compactor.compact`），**绝不重造**估算/压缩。记忆抽取与恢复压缩的 LLM 调用一律**假 provider** 离线端到端，临时目录真实读写，不联网。

## v0.9 文件清单

| 文件 | 动作 | 说明 |
| --- | --- | --- |
| `src/wentian/prompt/instructions.py` | 新建 | C53 项目指令三层加载 + `@include`（限深/visited 防环/越界拦截/体积上限），叶子、stdlib only |
| `src/wentian/memory/__init__.py` | 新建 | memory 纯包入口（导出 store/extractor/runner 公共符号），对 agent 编排层零反向依赖 |
| `src/wentian/memory/store.py` | 新建 | C56 笔记 `.md`+frontmatter 读写 + `INDEX.md` + 分级目录（user/project）+ 体积上限（复用 estimator）+ 写锁 |
| `src/wentian/memory/extractor.py` | 新建 | C57 抽取 Prompt + 解析四类笔记 + LLM 去重决策（provider 鸭子注入，仿 summarizer） |
| `src/wentian/memory/runner.py` | 新建 | C58 后台 daemon 编排 `MemoryRunner`（自建 provider + submit/close 短 join） |
| `src/wentian/session.py` | 改 | C54/C55 单文件全量 JSON → JSONL 追加；分区目录 `project_sessions_dir`；恢复卫生（截断/提醒）；过期清理 `prune_expired` |
| `src/wentian/prompt/system.py` | 改 | C59 `_render_project_instructions`/`_render_memory` 两槽由恒空改真渲染 `ctx.project_instructions`/`ctx.memory` |
| `src/wentian/cli.py` | 改 | C59 `build_app` 装配：加载指令→ctx、读两份 INDEX→ctx、分区会话目录、resume 后接 Compactor 压一次、构造并注入 `MemoryRunner` |
| `src/wentian/config.py` | 改 | C59/F69 增 `MemoryConfig`/`SessionsConfig` 解析（两层深合并、缺块/缺字段安全降级） |
| `src/wentian/repl.py` | 改 | C58 `_chat_once` 在 `COMPLETED` 后调 `runner.submit(...)`（鸭子注入，None ⇒ 回归 v0.8）；`/sessions --all`；`/exit` 短 join |
| `src/wentian/__init__.py` | 改 | 版本升 `0.9.0` |
| `pyproject.toml`、`uv.lock` | 改 | 版本 `0.9.0` 同步、零新增依赖 |
| `tests/test_instructions.py` | 新建 | T98 三层加载/`@include`/防环/越界/限深/体积上限 |
| `tests/test_session.py`（续） | 改 | T99/T100/T101/T102 JSONL 追加/分区/恢复卫生/清理 |
| `tests/test_memory_store.py` | 新建 | T103 笔记/frontmatter/INDEX/分级/体积上限/写锁 |
| `tests/test_memory_extractor.py` | 新建 | T104 抽取 Prompt/解析四类/去重决策（假 provider） |
| `tests/test_memory_runner.py` | 新建 | T105 后台线程不阻塞/异常吞/加锁/短 join |
| `tests/test_prompt_system.py`（续）、`tests/test_cli.py`（续）、`tests/test_config.py`（续）、`tests/test_repl.py`（续）、`tests/test_smoke.py`（续） | 改 | T106 两槽真渲染/装配/配置/REPL 钩子/冒烟 |

## 波次一 · 项目指令

## T98: C53 项目指令三层加载 + @include（限深 + visited 防环 + 越界拦截 + 体积上限）（C53/F63/N30/N32）

**文件：** `src/wentian/prompt/instructions.py`、`tests/test_instructions.py`
**依赖：** 无（叶子，可独立先行；与波次二、三并行，文件不相交）
**RED：**
1. 测试：三层各放不同 `WENTIAN.md`（`<cwd>/.wentian/WENTIAN.md` / `<cwd>/WENTIAN.md` / `<user_home>/.config/wentian/WENTIAN.md`）→ `load_project_instructions(cwd, user_home=..., cfg=...)` 返回三段拼接、**高优先级在前**（项目本地覆盖 → 项目根 → 用户全局），段间有清晰分隔
2. 测试：缺层静默跳过——任一层文件不存在不报错；三层全缺 → 返回空串（`""`）
3. 测试：`@include rel.md` 独占一行 → 目标文件内容**内联展开**，路径相对「包含它的文件所在目录」解析；非独占一行的 `@include`（行内有其他文字）不触发
4. 测试：**限深**——构造 a `@include` b、b `@include` c …超过默认深度 5 → 停止展开并 stderr 告警，不抛
5. 测试：**visited 防环**——构造 a→b→a 环 → 同一文件在一条 include 链上重复出现即跳过并告警，**不无限递归**
6. 测试：**越界拦截**——`@include ../../etc/x.md` 或绝对路径逃出项目根 → 拒绝该 include 并告警、不读取；**软链接指向项目外**（先 `realpath` 解析符号链接再前缀比对，与 v0.6 沙箱同规）同样被拦
7. 测试：**体积上限**——拼接后总体积超上限（`cfg` 给定）→ 按上限截断并告警
8. 跑测试确认失败（功能缺失：模块/函数未实现）
**GREEN：** 实现 `load_project_instructions(cwd: Path, *, user_home: Path|None=None, cfg) -> str`：解析三层路径按序读取 → 对每段递归内联 `@include`（`_expand(path, depth, visited, project_root)`：限深计数 + `visited` 集合防环 + 先解析符号链接后前缀比对拦越界）→ 高优先级在前拼接 → 体积上限截断；缺失/越界/超限均告警 stderr 不抛
**REFACTOR：** `@include` 行识别（独占一行正则）、越界判定（`_within_root`）、告警辅助抽小函数；保持绿
**验证：** `uv run pytest tests/test_instructions.py -q` 全绿（`tmp_path` 造三层目录 + include 链 + 软链接）
**注意：** 叶子模块（置于 `prompt/` 包内但不反向依赖同包 `system.py`）——只 import stdlib（`pathlib`/`os`/`sys`/`re`）；不 import provider/agent/registry；`@include` 越界判定**先 realpath 再比对**（与 v0.6 N11 沙箱同规、防软链逃逸）

## 波次二 · 会话 JSONL + 恢复

## T99: C54 会话存档改 JSONL 追加重构（append/load/list/load_latest、坏行跳过、可选 meta 行）（C54/F64/N29）

**文件：** `src/wentian/session.py`、`tests/test_session.py`（续）
**依赖：** 无（与 T98、波次三并行；T100/T101/T102 在其之上续接同文件，故波次二内部串行）
**RED：**
1. 测试：`SessionStore.append(session, new_messages)` 把新增消息**逐行 JSONL 追加**（不重写全文）——连续两次 append，断言文件按追加增长（行数 = 累计消息行数 + 可选 meta 行；前缀字节不变）
2. 测试：首行可选 `meta` 记录（`{"type":"meta","id":...,"created_at":...,"provider":...}`），其后每行一条 Message
3. 测试：`load(id)` 逐行解析还原 `Session`；**坏行跳过**——故意插一条非法 JSON 行 → 跳过并 stderr 告警、加载其余行、不抛给调用方
4. 测试：**无独立 meta 文件**——ID 取文件名、标题取首条 user 消息行内容、消息数 = 数消息行、`updated_at` 取文件 mtime（或末行时间）
5. 测试：`list()`/`load_latest()` 扫描目录由文件名/扫描得出列表项（标题/消息数/updated_at），按 updated_at 倒序
6. 测试：**对 Agent Loop / v0.8 压缩写回 / 权限门 / provider 适配透明**——往返 `append`→`load` 后 messages 与原始等价（含 tool 配对、`offloaded` 标记保留）
7. 跑测试确认失败（`append` 等新接口缺失 / 旧全量重写行为不符）
**GREEN：** `SessionStore` 由「单文件全量 JSON 重写」改 **JSONL 追加**：`append`（按行追加增量 + 首次写可选 meta 行）、`load`（逐行 `json.loads`，`except JSONDecodeError` 跳过 + 告警）、`list`/`load_latest`（扫目录、扫描得标题/消息数/mtime）；`Session` dataclass 保留
**REFACTOR：** 行编解码（`_encode_line`/`_decode_line`）、扫描元信息抽取（`_scan_meta`）抽小函数；保持绿
**验证：** `uv run pytest tests/test_session.py -q` 全绿（`tmp_path` 作 sessions_dir）
**注意：** 追加快、崩溃只丢最后半行；旧扁平 `.json` 不在本任务读写范围（T100 分区时一并视为遗留不列不删）

## T100: C54 按 cwd 分区目录 project_sessions_dir + slug 化 + /sessions --all 过滤（C54/F64/N29）

**文件：** `src/wentian/session.py`、`tests/test_session.py`（续）
**依赖：** T99（JSONL 追加接口）
**RED：**
1. 测试：`project_sessions_dir(cwd, data_home=...) -> Path` == `<data_home>/wentian/projects/<cwd-slug>/sessions/`；`<cwd-slug>` = 绝对 cwd 路径分隔符 `/`→`-`（与 Claude Code 同法、可读）
2. 测试：新会话 `append` 落到 `projects/<cwd-slug>/sessions/<id>.jsonl`（断言绝对路径分区正确）
3. 测试：`list(all_projects=False)`（默认）**只扫当前 `<cwd-slug>` 分区**；造两个不同 cwd 分区各放会话 → 默认只列当前分区那条
4. 测试：`list(all_projects=True)`（`--all`）跨 `projects/*/sessions/` 全扫 → 列出全部分区会话
5. 测试：**旧扁平遗留**——`<data_home>/wentian/sessions/*.json`（旧路径）→ 默认与 `--all` 均**不列、不删、不报错**
6. 跑测试确认失败（`project_sessions_dir`/`all_projects` 缺失）
**GREEN：** 新增 `project_sessions_dir(cwd, *, data_home=None)` + `_cwd_slug(cwd)`；`SessionStore` 改用分区目录；`list(all_projects=False)` 默认当前分区、`True` 跨 `projects/*/sessions/` glob；遗留扁平目录不纳入扫描
**REFACTOR：** slug 化、分区根解析（`_projects_root`）抽小函数；保持绿
**验证：** `uv run pytest tests/test_session.py -q` 全绿（`tmp_path` 作 data_home，造多分区 + 遗留扁平）
**注意：** v0.8 卸载产物 `<session_id>.artifacts/` 随会话同分区自动迁移（路径基于会话文件目录推导，无需额外改）

## T101: C55 会话恢复卫生：尾部未配对截断 + 时间跨度提醒 + 溢出复用 Compactor 压一次（C55/F65/N29/N30）

**文件：** `src/wentian/session.py`（或叶子 `src/wentian/session_recovery.py`）、`tests/test_session.py`（续）
**依赖：** T99（load）、T100（分区）；复用 v0.8 `context.estimator`/`Compactor`（**不在 session 包内 import provider**，压缩由装配层 T106 调用）
**RED：**
1. 测试：`truncate_unpaired(messages) -> messages`——历史尾部「助手 `tool_call` 无后续工具结果」的悬空调用 → 截断该未配对部分；构造该尾部 → 断言截断后送两家 provider 转换不产生 400（核验配对铁律，复用 v0.8 配对核验点）
2. 测试：`truncate_unpaired` 对已配对历史 / 空历史 → 原样返回（不误伤）
3. 测试：`resume_gap_reminder(updated_at, now, hours) -> str | None`——距上次 `updated_at` 超阈值（默认 4h）→ 返回一条时间跨度提示文案；未超 → 返回 `None`
4. 测试（装配契约，留 T106 联动断言占位）：溢出判定**复用 v0.8 estimator**——`estimate_total(prompt_total, recovered_messages) > context_window - margin` 为真 → 装配层调 `Compactor.compact()` 压一次；本任务在 session 层只提供「截断 + 提醒文案」纯函数，**不 import provider/Compactor**
5. 测试：时间跨度提醒经 v0.5 `<system-reminder>` 通道**一次性**注入、**绝不写回持久化 messages**（断言函数只返回文案、不修改入参 messages、store 落盘不含该提醒）
6. 跑测试确认失败（`truncate_unpaired`/`resume_gap_reminder` 缺失）
**GREEN：** 实现 `truncate_unpaired`（从尾部找悬空 assistant tool_call、截断未配对段）、`resume_gap_reminder`（时间差比对返回文案或 None）；二者纯函数、可注入、不 import provider
**REFACTOR：** 悬空判定（`_trailing_unpaired_index`）、文案模板抽小函数；保持绿
**验证：** `uv run pytest tests/test_session.py -q` 全绿（构造尾部未配对 / 跨时间 updated_at）
**注意：** 溢出「先压一次」**直接复用 v0.8 `Compactor.compact()`**（装配层 T106 在 resume 后按 estimator 判定调用），session 包绝不重造估算/压缩、绝不 import provider（N30 叶子边界）

## T102: C55 过期会话清理 prune_expired（含 .artifacts/）（C55/F66/N29）

**文件：** `src/wentian/session.py`、`tests/test_session.py`（续）
**依赖：** T100（分区目录）
**RED：**
1. 测试：`prune_expired(sessions_dir, retention_days, *, now=...) -> list[Path]`——构造一个 `updated_at`（mtime）早于 `retention_days`（默认 30）的会话 + 一个新的 → 旧的被删、新的保留、返回被删路径列表
2. 测试：删旧会话时**连带删其 `<id>.artifacts/` 目录**（构造产物目录 → 断言一并清除）
3. 测试：清理只针对**当前分区**（不跨分区误删）
4. 测试：**清理失败不致命**——把某文件设只读 / 造删除异常 → 告警 stderr 跳过、不抛、不影响其余清理
5. 测试：`retention_days` 可配（传不同值断言边界）
6. 跑测试确认失败（`prune_expired` 缺失）
**GREEN：** 实现 `prune_expired`：扫当前分区会话文件、mtime 早于 `now - retention_days` 的删文件 + 配套 `.artifacts/`（`shutil.rmtree`）；`try/except` 包每个删除、失败告警跳过；返回被删列表
**REFACTOR：** 过期判定、配套产物路径推导抽小函数；保持绿
**验证：** `uv run pytest tests/test_session.py -q` 全绿（`tmp_path` 造新旧会话 + 产物目录 + 只读触发异常）
**注意：** 惰性清理由装配层 T106 在 `build_app` 启动时调用一次；删除失败不致命（告警跳过），绝不因清理崩溃启动

## 波次三 · 自动记忆

## T103: C56 记忆存储 store.py：笔记 .md+frontmatter 读写 + INDEX.md + 分级目录 + 体积上限 + 写锁（C56/F68/N30/N31）

**文件：** `src/wentian/memory/__init__.py`、`src/wentian/memory/store.py`、`tests/test_memory_store.py`
**依赖：** 无（与波次一、二并行，文件不相交）；复用 v0.8 `context.estimator.char_estimate` 卡索引体积
**RED：**
1. 测试：`write_note(scope, note)`——把一条 `Note`（category / created_at / source_session / tags / 正文）写为带 **frontmatter** 的 `.md` 文件；`scope="user"` 落 `<user_home>/.config/wentian/memory/`、`scope="project"` 落 `<cwd>/.wentian/memory/`
2. 测试：frontmatter 往返——`write_note` 后 `read_note(path)` 解析回 `Note`，字段（category/created_at/source_session/tags）一致
3. 测试：`upsert_index(scope, entries)` / `read_index(scope) -> str`——每域一份 `INDEX.md`（条目标题 + 一句话摘要，**非全文**）；upsert 新增/更新条目；read 返回拼好的索引文本
4. 测试：**分级目录**——四类笔记默认归属（用户偏好/纠正反馈 → user；项目知识/参考资料 → project），但归属由 `Note.scope` 决定（LLM 可改判，store 只按字段落盘）
5. 测试：**体积上限**——`read_index` 超 `max_index_lines`（200）/ `max_index_bytes`（25KB，用 `char_estimate`/字节数卡）→ 按上限**截断**返回
6. 测试：**写锁串行**——并发多线程 `write_note`/`upsert_index` → 用 `threading.Lock` 串行，断言无交错损坏（INDEX 内容完整一致、无半行）
7. 跑测试确认失败（store 接口缺失）
**GREEN：** 实现 `store.py`：`Note` dataclass、`write_note`/`read_note`（frontmatter 编解码）、`read_index`/`upsert_index`（INDEX.md 读写 + 体积上限截断，复用 `char_estimate`）、分级目录解析（`_scope_dir`）、模块级或实例 `threading.Lock` 包所有写操作；`memory/__init__.py` 导出公共符号
**REFACTOR：** frontmatter 编解码、目录解析、索引截断抽小函数；保持绿
**验证：** `uv run pytest tests/test_memory_store.py -q` 全绿（`tmp_path` 作 user_home/cwd、并发线程断言）
**注意：** 叶子——stdlib + 复用 `context.estimator`；对 agent 编排层零反向依赖（N30）；**绝不把 api_key 等密钥写进笔记**（N32，落盘内容仅来自抽取笔记字段）

## T104: C57 记忆抽取 extractor.py：抽取 Prompt + 解析四类笔记 + LLM 去重决策（假 provider 端到端）（C57/F67/N28/N30）

**文件：** `src/wentian/memory/extractor.py`、`tests/test_memory_extractor.py`
**依赖：** T103（`Note`/store 类型）；provider 鸭子注入（仿 v0.8 summarizer，同步 `for event in provider.stream(...)`）
**RED：**
1. 测试：`extract(provider, recent_messages, existing_index) -> list[Note]`——发给 provider 的请求 `tools is None`；`system`/指令含「四类笔记（用户偏好/纠正反馈/项目知识/参考资料）」「去重」「输出结构化（如 JSON）」语义；假 provider 回固定四类笔记 JSON → 解析出 4 条 `Note`、category 正确、scope 按默认归属（user/project）
2. 测试：**去重决策**——把 `existing_index`（含某条）喂 provider，假 provider 返回「新增 / 更新已有 / 跳过已覆盖」决策 → 抽到等价信息时**跳过或更新**而非重复追加（断言结果不含重复条目）
3. 测试：解析鲁棒——provider 回非法/空 → 返回 `[]`（不抛、记 stderr）；夹杂前后噪声文本但含合法 JSON 块 → 仍能抽出
4. 测试：`recent_messages` 取**最近一轮**（末次 user + 助手正文 + 必要工具活动摘要）——构造多轮历史，断言只喂最近一轮给 provider
5. 测试：**纯解析为离线纯函数**——解析器 `parse_notes(text) -> list[Note]` 单独可测（不经 provider）
6. 跑测试确认失败（`extract`/`parse_notes` 缺失）
**GREEN：** 实现 `EXTRACT_SYSTEM`/指令常量（四类 + 去重 + 结构化输出）、`extract`（同步 `for event in provider.stream(..., tools=None)` 收文本 → `parse_notes` 解析 + 应用去重决策）、`parse_notes`（结构化解析 → `list[Note]`，异常返回 `[]` + 告警）
**REFACTOR：** Prompt 模板、JSON 块抽取正则、去重决策应用抽小函数；保持绿
**验证：** `uv run pytest tests/test_memory_extractor.py -q` 全绿（假 provider 返回固定笔记 JSON / 去重决策）
**注意：** provider 鸭子注入（只用 `stream`）、仿 summarizer 同步迭代、**不引 asyncio**；解析纯函数离线可测；抽取异常绝不外抛（runner 层吞）

## T105: C58 后台抽取编排 runner.py + REPL COMPLETED 钩子 + /exit 短 join（C58/F67/N31）

**文件：** `src/wentian/memory/runner.py`、`src/wentian/repl.py`、`tests/test_memory_runner.py`、`tests/test_repl.py`（续）
**依赖：** T103（store）、T104（extractor）
**RED：**
1. 测试：`MemoryRunner(provider_factory, store, cfg).submit(recent_messages)` 启 **daemon 线程 fire-and-forget**——`submit` 立即返回不阻塞（断言主线程不等待抽取完成）；线程内调 `extractor.extract` → `store.write_note`/`upsert_index` 落盘（用假 provider，join 后断言落盘）
2. 测试：**异常静默吞**——注入「抽取必抛异常」的假 provider → 线程内异常吞到 stderr、**不崩主线程、不中断会话、对话历史 messages 不被污染**（断言入参 messages 不变、主流程继续）
3. 测试：**自建 provider 不跨线程共享**——`MemoryRunner` 持 `provider_factory`（或 `memory.provider` 配置），线程内**新建 provider 实例**（断言不复用传入的对话 provider 对象）
4. 测试：**并发写 INDEX 加锁串行**——连发多个 `submit` → store 写锁保证 INDEX 不交错损坏（复用 T103 写锁，断言完整）
5. 测试：`close(timeout)` 供 `/exit` 短 join——最多 join 一个短超时、不卡退出、无线程泄漏（断言超时内返回、未完成线程为 daemon 不阻塞进程退出）
6. 测试（repl 续）：`_chat_once` 在 `AgentDone.stop_reason == COMPLETED` 后调 `runner.submit(recent)`；**`runner=None`（默认）→ 不抽取、与 v0.8 字节级等价**（既有 repl 测试零修改保持绿）；`/exit` 调 `runner.close(short_timeout)`
7. 跑测试确认失败（`MemoryRunner`/repl 钩子缺失）
**GREEN：** 实现 `MemoryRunner`（持 `provider_factory`/store/cfg；`submit` 启 daemon `threading.Thread` 调 extractor→store，整体 `try/except` 吞 stderr；`close(timeout)` join 活动线程一个短超时）；repl `_chat_once` 在 `COMPLETED` 后 `runner and runner.submit(...)`、`/exit` 路径 `runner and runner.close(...)`
**REFACTOR：** 线程体（`_run_extraction`）、最近一轮提取（`_recent_round`）抽小函数；保持绿
**验证：** `uv run pytest tests/test_memory_runner.py tests/test_repl.py -q` 全绿（假 provider + 线程 join 断言落盘 + 异常吞断言不崩）
**注意：** daemon 线程 fire-and-forget、绝不阻塞 REPL 输入；自建 provider 实例（线程安全，不跨线程共享对话 provider）；`memory.enabled:false` 时 runner 为 None（钩子短路、回归 v0.8）；**不引 asyncio**

## 波次四 · 装配 + 验收

## T106: C59 两槽真渲染 + cli.build_app 装配 + config MemoryConfig/SessionsConfig + 版本 0.9.0（C59/F63/F65/F68/F69/N29）

**文件：** `src/wentian/prompt/system.py`、`src/wentian/cli.py`、`src/wentian/config.py`、`src/wentian/__init__.py`、`pyproject.toml`、`uv.lock`、`tests/test_prompt_system.py`（续）、`tests/test_cli.py`（续）、`tests/test_config.py`（续）、`tests/test_smoke.py`（续）
**依赖：** T98（instructions）、T101（恢复卫生 + Compactor 契约）、T103/T104/T105（memory 包）、复用 v0.8 `Compactor`
**RED：**
1. 测试（config）：无 `memory:` 块 → `Config.memory == MemoryConfig()`（默认 `enabled=True`、`provider=None`、`max_index_lines=200`、`max_index_bytes=25600`）；无 `sessions:` 块 → `SessionsConfig()`（默认 `retention_days=30`、`resume_gap_reminder_hours=4`）；部分字段覆盖仅覆盖项变（两层深合并逐键）；缺块/缺字段安全降级不抛
2. 测试（system.py）：`_render_project_instructions(ctx)` 在 `ctx.project_instructions` 非空时渲染该文本进「项目/自定义指令」模块；`_render_memory(ctx)` 在 `ctx.memory` 非空时渲染进「长期记忆」模块；**两槽均空时拼装无空行残渣、缓存前缀稳定**（断言无残渣 + 字节稳定，N29）
3. 测试（cli）：`build_app` 调 `load_project_instructions(cwd,...)` 灌 `ctx.project_instructions`、读 user+project 两份 `INDEX.md` 拼进 `ctx.memory`（≤200 行/25KB 上限）、会话用 `project_sessions_dir(cwd)`、启动调 `prune_expired`
4. 测试（cli）：resume 路径——`load`→`truncate_unpaired`→按 estimator 判 `estimate_total > window - margin` 则调 `Compactor.compact()` **压一次**再进对话；`updated_at` 超阈值则经 `<system-reminder>` 通道注入 `resume_gap_reminder` 文案一次、不写回持久化
5. 测试（cli）：按 `memory.enabled` 构造 `MemoryRunner`（`enabled=False` → runner=None）、按 `memory.provider` 决定自建 provider 类型（缺省复用当前对话 provider 类型）、注入 REPL
6. 测试（smoke/版本）：版本字符串 `0.9.0`；**无 `WENTIAN.md`/无 memory 配置/无历史会话 → 行为与 v0.8 一致**（两槽空、runner=None、既有冒烟通过）
7. 跑测试确认失败（渲染恒空 / 装配未接 / 配置缺类 / 版本旧）
**GREEN：** `config.py` 增 `@dataclass(frozen=True) MemoryConfig`/`SessionsConfig`（全默认）+ 解析 + 两层合并；`system.py` 两 `_render_*` 改真渲染 ctx 字段（空则该模块整体省略、无残渣）；`cli.build_app` 装配指令/INDEX 注入、分区会话、`prune_expired`、resume 截断+提醒+Compactor 压一次、按 `memory.enabled`/`memory.provider` 造并注入 `MemoryRunner`；版本升 `0.9.0`（源码+pyproject+lock）
**REFACTOR：** INDEX 注入预算、resume 卫生编排、runner 构造抽小函数；保持绿
**验证：** `uv run pytest tests/test_prompt_system.py tests/test_cli.py tests/test_config.py tests/test_smoke.py -q` 全绿
**注意：** 启动注入一次、不热刷（保 v0.5 提示词缓存稳定）；两槽空时拼装无残渣、缓存断点稳定（N29）；零新增第三方依赖（pyproject diff 仅版本号）

## T107: 离线验收回归（全量 pytest + 无配置/无记忆/无指令冒烟 + 分层 import 断言 + ruff 收口）（N28/N29/N33）

**文件：** 全仓
**依赖：** T98–T106
**步骤（非 TDD，验证收口）：**
1. `uv run pytest -q` → v0.1–v0.8 全部 + v0.9 新增全绿、无告警（基线 905 → +N）
2. **分层现场检查（grep 取证）**：`prompt/instructions.py` 叶子——只 stdlib、零 provider/agent/registry import、不反向依赖同包 `system.py`；`memory` 包对 agent 编排层**零反向依赖**、provider 鸭子注入、`memory/runner` 自建 provider 不跨线程共享；`session.py` 不 import 后端 SDK、恢复溢出**复用 v0.8 `context` 包不重造**估算/压缩
3. `ruff format --check .` 通过、`ruff check .` 无告警
4. **无配置/无记忆/无指令冒烟**：`printf '/exit\n' | uv run wentian`（空 cwd、无 `WENTIAN.md`、无 memory 配置、无历史会话）→ 横幅示 v0.9.0、退出码 0、无 traceback、两槽空无残渣、行为同 v0.8
5. **离线端到端冒烟**：① 放三层 `WENTIAN.md`+`@include` 启动 → 断言注入「项目/自定义指令」可见于发给后端的 `system`；② 假 provider 聊一轮 `COMPLETED` 后台抽取 → 断言按类落盘 + `INDEX` 更新；下一会话启动读 `INDEX` → 断言「长期记忆」模块注入；③ 造坏行/尾部未配对/溢出会话 → 恢复时跳坏行 + 截断未配对 + Compactor 压一次；④ 造跨时间 `updated_at` 会话 → 恢复首轮注入时间提醒一次、不写回；⑤ `/sessions --all` 跨分区列举
6. **不破坏 v0.1–v0.8**：`runner=None`/两槽空时既有 loop/repl 测试零修改全绿（字节级回归）
7. `pyproject` diff 仅版本号、零新增依赖；checklist 离线项逐条取证
**验证：** 上述各项各留现场证据，记入 checklist；🌐👁 联网/人工项（真 provider 抽取一次、真终端跨会话「越用越懂你」体感）单列、不阻塞离线验收

## v0.9 执行顺序

```
波次一（独立先行，叶子）：
  T98（instructions 三层 + @include，无依赖）
波次二（会话 JSONL + 恢复，同 session.py 内部串行；与波次一/三并行）：
  T99（JSONL 追加重构，无依赖）
  → T100（cwd 分区 + --all，依赖 T99）
  → T101（恢复卫生：截断/提醒/溢出复用 Compactor，依赖 T99+T100）
  → T102（过期清理 prune_expired，依赖 T100）
波次三（自动记忆；与波次一/二并行）：
  T103（memory/store，无依赖）
  → T104（memory/extractor，依赖 T103）
  → T105（memory/runner + REPL 钩子，依赖 T103+T104）
波次四（装配 + 验收，依赖一+二+三）：
  T106（system 两槽真渲染 + cli 装配 + config + 版本 0.9.0，依赖 T98+T101+T103/T104/T105）
  → T107（全量回归收尾，依赖 T98–T106）
```

- **波次一可独立先行**：`instructions.py` 是叶子、文件不相交，最先派或与二、三并行。
- **波次二、三相对独立可并行**：波次二只动 `session.py`+`test_session.py`，波次三只动 `memory/` 包+各自测试，两组文件**完全不相交**，可并行派两个子 agent。
- **波次内部串行**：波次二 T99→T100→T101/T102 续接同一 `session.py`，串行保正确；波次三 T103→T104→T105 逐层依赖上一层产物（store→extractor→runner），串行。

# v0.10 任务（T108–T116：斜杠命令系统）

> 教学隔离规约同前：一任务一提交 `[T#/C#/F#/N#]`、一组件一文件、docstring 标记版本/组件/特性。TDD 红-绿-重构不豁免；每波次后规格/质量评审。
> **复用底线**：注册中心**镜像既有 `ToolRegistry`**（按序存、冲突 `raise`），不另起炉灶。命令包 `commands/` **纯包**（零 `rich`/零 `prompt_toolkit`/零后端 SDK），命令处理函数只依赖 `CommandContext` 协议、用**假 ctx** 离线驱动。补全器在 **ui 层**。归并后既有命令逐条回归、非命令路径零变化（N35）。

## v0.10 文件清单

| 文件 | 动作 | 说明 |
| --- | --- | --- |
| `src/wentian/commands/__init__.py` | 新建 | commands 纯包入口（导出 spec/registry/parser/context/builtins 公共符号） |
| `src/wentian/commands/spec.py` | 新建 | C85 `CommandSpec` dataclass + `CommandType` 枚举（LOCAL/UI_STATE/PROMPT），叶子 |
| `src/wentian/commands/parser.py` | 新建 | C88 `parse(line) -> ParsedCommand|None`（斜杠/空格切分/小写/裸斜杠早返回），叶子 |
| `src/wentian/commands/registry.py` | 新建 | C86 `CommandRegistry`（register/lookup/visible/completions + 冲突 raise），镜像 ToolRegistry，叶子 |
| `src/wentian/commands/context.py` | 新建 | C87 `CommandContext` Protocol（界面控制接口，仅 typing），叶子 |
| `src/wentian/commands/builtins.py` | 新建 | C89 `build_builtin_registry()` + 12 条 handler（只调 ctx.*，纯包） |
| `src/wentian/ui/completion.py` | 新建 | C90 `CommandCompleter(prompt_toolkit.Completer)`，ui 层 |
| `src/wentian/ui/input.py` | 改 | C90 `PromptSession` 注入 `completer=` + `complete_style=MULTI_COLUMN` |
| `src/wentian/repl.py` | 改 | C91 REPL 实现 `CommandContext`；`_dispatch_command` 重写「parse→registry→handler」；`status_line` 模式标记 `[DEFAULT]`/`[PLAN]`；新增 `clear_context` 等 ctx 方法；归并老命令 |
| `src/wentian/cli.py` | 改 | C92 `build_app` 构造 registry（冲突即 panic）+ 建 `CommandCompleter` 注入 `PromptInput` |
| `src/wentian/__init__.py` | 改 | 版本升 `0.10.0` |
| `pyproject.toml`、`uv.lock` | 改 | 版本 `0.10.0` 同步、零新增依赖 |
| `tests/test_commands_spec.py` | 新建 | T108 CommandSpec/CommandType |
| `tests/test_commands_parser.py` | 新建 | T109 解析器 |
| `tests/test_commands_registry.py` | 新建 | T110 注册/查找/可见/补全候选/冲突 raise |
| `tests/test_commands_context.py` | 新建 | T111 假 ctx 夹具（被 builtins 复用） |
| `tests/test_commands_builtins.py` | 新建 | T112 12 条 handler（假 ctx 驱动） |
| `tests/test_completion.py` | 新建 | T113 CommandCompleter（假 Document） |
| `tests/test_repl.py`（续）、`tests/test_cli.py`（续）、`tests/test_smoke.py`（续）、`tests/test_layering.py`（续） | 改 | T114/T115/T116 分发/状态栏/clear/归并回归/装配/分层/冒烟 |

## 波次一 · 叶子原语（并行）

## T108: C85 命令规格 spec.py：CommandSpec dataclass + CommandType 枚举（C85/F70/F72）

**文件：** `src/wentian/commands/__init__.py`、`src/wentian/commands/spec.py`、`tests/test_commands_spec.py`
**依赖：** 无（叶子，可独立先行；与 T109/T110/T111 并行，文件不相交）
**RED：**
1. 测试：`CommandType` 有且仅有 `LOCAL`/`UI_STATE`/`PROMPT` 三值（值为 `"local"`/`"ui_state"`/`"prompt"`）
2. 测试：`CommandSpec(name, summary, usage, type, handler)` 可构造，`aliases=()`/`arg_hint=""`/`hidden=False` 为默认；`frozen` 不可变（改字段抛 `FrozenInstanceError`）
3. 测试：`handler` 字段可持有 `Callable[[ctx, str], bool|None]`（构造时传一个假函数断言可调用）
4. 跑测试确认失败（模块/类未实现）
**GREEN：** 实现 `commands/spec.py`：`class CommandType(Enum)` 三值 + `@dataclass(frozen=True) CommandSpec`（字段见 plan.md C85，`handler` ctx 形参用字符串前向引用避免运行时 import context）；`commands/__init__.py` 导出
**REFACTOR：** 字段注释/排序整理；保持绿
**验证：** `uv run pytest tests/test_commands_spec.py -q` 全绿
**注意：** 叶子——只 `dataclasses`/`enum`/`typing`/`collections.abc.Callable`；零 rich/prompt_toolkit/provider import（N36）

## T109: C88 解析器 parser.py：斜杠前缀 + 空格切分 + 名转小写 + 裸斜杠/空白早返回（C88/F71）

**文件：** `src/wentian/commands/parser.py`、`tests/test_commands_parser.py`
**依赖：** 无（叶子，与 T108/T110/T111 并行）
**RED：**
1. 测试：`parse("/Help")` → `ParsedCommand(name="help", args="")`（名转小写、大小写不敏感）
2. 测试：`parse("/session resume abc-123")` → `name="session"`、`args="resume abc-123"`（第一个空格前为名、之后为参数 strip）
3. 测试：`parse("/x")` → `name="x"`、`args=""`；多余内部空格/制表在 args 内 strip 首尾
4. 测试：`parse("/")`、`parse("/   ")` → `None`（裸斜杠/纯空白体早返回、不进分发）
5. 跑测试确认失败（`parse`/`ParsedCommand` 缺失）
**GREEN：** 实现 `parse(line: str) -> ParsedCommand | None`：去首 `/`、`body.partition(" ")` 取名（`.lower()`）与参数（`.strip()`）；body 为空/纯空白 → `None`；`@dataclass(frozen=True) ParsedCommand`
**REFACTOR：** 空白判定抽小函数；保持绿
**验证：** `uv run pytest tests/test_commands_parser.py -q` 全绿
**注意：** 叶子——纯字符串处理、零业务 import；`line` 约定已 strip 且以 `/` 开头（调用方 REPL 保证）；空输入由 REPL `run()` 既有早返回兜（不在 parse 内重复）

## T110: C86 注册中心 registry.py：注册/查找/可见列举/补全候选 + 冲突 raise（C86/F70/N34/N37）

**文件：** `src/wentian/commands/registry.py`、`tests/test_commands_registry.py`
**依赖：** T108（`CommandSpec`）；与 T109/T111 并行
**RED：**
1. 测试：`register(spec)` 后 `lookup("name")` 命中、`lookup("alias")` 命中、`lookup("NAME")` 大小写不敏感命中
2. 测试：`visible()` 按**注册顺序**返回 `hidden=False` 的命令；`hidden=True` 不在内
3. 测试：`completions("se")` 返回可见命令中规范名以 `se` 开头的（按注册顺序）；隐藏命令不入候选；`completions("")` 返回全部可见；`completions("zzz")` 返回 `[]`
4. 测试：**冲突 raise**——再注册一条**同名**命令 → `register` 抛 `ValueError`；注册一条**别名与已有名/别名冲突**的命令 → 同样 `raise`（命名或别名任一撞即拒）
5. 测试：`lookup("nope")` → `None`
6. 跑测试确认失败（`CommandRegistry` 缺失）
**GREEN：** 实现 `CommandRegistry`：`_by_key: dict[str, CommandSpec]`（name + 各 alias 全作 key）+ `_order: list[CommandSpec]`；`register` 先查全部 key 冲突即 `raise ValueError`（镜像 `ToolRegistry`），再落 key + 追加 order；`lookup`（入参 `.lower()`）、`visible`、`all`、`completions`
**REFACTOR：** 冲突检测、前缀过滤抽小函数；保持绿
**验证：** `uv run pytest tests/test_commands_registry.py -q` 全绿
**注意：** 叶子——只 stdlib + import 同包 `spec`；非线程安全（启动期单线程构造、之后只读，同 ToolRegistry）；冲突 `raise` 在 build_app 启动触发 = panic（N37）

## T111: C87 界面控制接口 context.py：CommandContext Protocol + 假 ctx 测试夹具（C87/F73/N34）

**文件：** `src/wentian/commands/context.py`、`tests/test_commands_context.py`
**依赖：** 无（叶子，仅 Protocol；与 T108/T109/T110 并行）
**RED：**
1. 测试：定义一个**假 ctx**（dataclass 记录 `printed: list`、`sent: list`、`mode`、各方法调用计数）实现 `CommandContext` 协议——`isinstance(fake, CommandContext)`（`@runtime_checkable`）为真，证明协议方法集自洽可被实现
2. 测试：假 ctx 的 `print`/`send_user_message`/`set_mode` 等被调后状态正确（夹具自测，供 T112 复用）
3. 跑测试确认失败（`CommandContext` 缺失）
**GREEN：** 实现 `commands/context.py`：`@runtime_checkable class CommandContext(Protocol)`，方法签名见 plan.md C87（print/send_user_message/get_mode/set_mode/token_usage/status_line/memory_summary/visible_commands/clear_context/new_session/list_sessions/resume_session/switch_provider/compact_now）；`Mode`/`CommandSpec` 在 `TYPE_CHECKING` 下前向引用；测试侧实现 `FakeContext` 夹具
**REFACTOR：** 夹具方法整理；保持绿
**验证：** `uv run pytest tests/test_commands_context.py -q` 全绿
**注意：** 叶子——`typing.Protocol` + `TYPE_CHECKING` import，运行时零依赖；`FakeContext` 夹具落 `tests/`（或 conftest）供 T112 全部 builtin 测试复用

## 波次二 · 命令实现 + 补全（并行）

## T112: C89 内置命令 builtins.py：build_builtin_registry() + 12 条 handler（假 ctx 驱动）（C89/F76/F72）

**文件：** `src/wentian/commands/builtins.py`、`tests/test_commands_builtins.py`
**依赖：** T108（spec）、T110（registry）、T111（context + 假 ctx 夹具）
**RED：**
1. 测试：`build_builtin_registry()` 返回的 registry `visible()` 含 10 个可见命令 + 归并的 `/provider`、`/exit`（共 12）；类型分类正确（`/help`/`/status`/`/memory`/`/compact`/`/exit`=LOCAL；`/clear`/`/plan`/`/do`/`/permission`/`/session`/`/provider`=UI_STATE；`/review`=PROMPT）；别名登记（`?`/`h`→help、`cls`→clear、`mem`→memory、`perm`→permission、`sess`→session、`quit`/`q`→exit、`st`→status）
2. 测试（本地类）：`/help` handler 调 `ctx.print` 收到含全部 `ctx.visible_commands()` 的渲染体；`/status` 调 `ctx.status_line`/`ctx.token_usage` 并 print；`/memory` 调 `ctx.memory_summary` 并 print；均**不改状态**
3. 测试（界面类）：`/plan` 调 `ctx.set_mode(Mode.PLAN)`、`/do` 调 `ctx.set_mode(Mode.DEFAULT)`；尾随文字 `/plan 改造 X` → 另调 `ctx.send_user_message("改造 X")`；`/permission` 无参打印当前模式、`/permission acceptEdits` 调 `ctx.set_mode(acceptEdits)`；`/clear` 调 `ctx.clear_context()`；`/provider deepseek` 调 `ctx.switch_provider("deepseek")`
4. 测试（会话子命令）：`/session new` 调 `ctx.new_session()`；`/session list` 调 `ctx.list_sessions(all_projects=False)`、`/session list --all` → `all_projects=True`；`/session resume abc` 调 `ctx.resume_session("abc")`；无/错子命令 → 打印用法
5. 测试（提示词类）：`/review` 调 `ctx.send_user_message(...)`、文案含「审查」语义；**断言走的是 send_user_message 而非任何 provider 直调**（用假 ctx 记录 sent，断言无其它副作用）
6. 测试：`/exit` handler 返回 `True`（REPL 据此退出）；其余返回 `None`
7. 跑测试确认失败（`build_builtin_registry`/handler 缺失）
**GREEN：** 实现 `builtins.py`：12 条 `_h_*(ctx, args)` 自由函数（只调 `ctx.*`）+ `render_help(specs)`（产结构化/纯文本，Rich 表格留 REPL 侧）+ `build_builtin_registry()` 顺序 `register` 12 条；`Mode` 从 `permissions.decision` import、`parse_mode(args)` 映射字符串→Mode
**REFACTOR：** 子命令路由（`_session_sub`）、模式名解析（`parse_mode`）、help 渲染抽小函数；保持绿
**验证：** `uv run pytest tests/test_commands_builtins.py -q` 全绿（假 ctx 夹具驱动 12 条）
**注意：** 纯包——import 同包 spec/registry + permissions.decision（纯叶子）；**不 import rich/prompt_toolkit/repl 具体类**；handler 只调 ctx 协议方法（AC87）；提示词类只经 `send_user_message`（N34）

## T113: C90 Tab 补全 ui/completion.py + input.py 注入（C90/F75）

**文件：** `src/wentian/ui/completion.py`、`src/wentian/ui/input.py`、`tests/test_completion.py`
**依赖：** T110（registry.completions）；与 T112 并行（文件不相交）
**RED：**
1. 测试：构造假 `Document`（`text_before_cursor="/se"`）+ registry → `CommandCompleter(registry).get_completions(doc, evt)` 产出含 `/session`（前缀命中）的 `Completion`，`start_position == -len("se")`、带 `display_meta`（summary）
2. 测试：`text_before_cursor="/"` → 多候选列出全部可见命令；`"/zzz"` → 零候选；隐藏命令不现身
3. 测试：`text_before_cursor="/session "`（已有空格、在敲参数）→ **不补**（早返回空）；`"abc"`（不以 / 开头）→ 不补
4. 测试：别名前缀（如 `/se`）命中规范名 `session`（规范名补全，候选用规范名）
5. 测试：`input.py` 的 `PromptInput` 接受 `completer=` 注入、`PromptSession` 携带该 completer 与 `complete_style`（构造断言，不需真终端）
6. 跑测试确认失败（`CommandCompleter` 缺失 / input 未接 completer）
**GREEN：** 实现 `CommandCompleter(Completer)`：`get_completions` 取 `document.text_before_cursor`，非 `/` 开头或含空格 → return；prefix = 去斜杠小写；`for spec in registry.completions(prefix): yield Completion(spec.name, start_position=-len(prefix), display=f"/{spec.name}", display_meta=spec.summary)`；`input.py` `PromptInput.__init__` 增 `completer` 形参、塞进 `session_kwargs`（含 `complete_style=CompleteStyle.MULTI_COLUMN`）
**REFACTOR：** 前缀提取、候选构造抽小函数；保持绿
**验证：** `uv run pytest tests/test_completion.py -q` 全绿（假 Document 驱动）
**注意：** ui 层——import prompt_toolkit + 鸭子 registry（只调 `.completions`）；命令包对补全框架无感（N36）；Shift+Tab 仍是既有 `on_mode_cycle`（切权限模式）、与 Tab 不冲突；真终端菜单弹出留 👁（AC90）

## 波次三 · 集成

## T114: C91 REPL 实现 CommandContext + 分发重写 + 状态栏标记 + /clear + 归并老命令（C91/F73/F74/F76/N35）

**文件：** `src/wentian/repl.py`、`tests/test_repl.py`（续）
**依赖：** T112（builtins registry）
**RED：**
1. 测试：REPL **实现 `CommandContext`**——`isinstance(repl, CommandContext)` 为真；各协议方法可调（`print`/`send_user_message`=调 `_chat_once`/`get_mode`/`set_mode`/`clear_context`/`new_session`/`list_sessions`/`resume_session`/`switch_provider`/`compact_now`/`memory_summary`/`token_usage`/`status_line`/`visible_commands`）
2. 测试（分发）：注入 `commands=build_builtin_registry()` + 假 provider + 假 input——输入 `/help` 走命令分发、**假 provider 零调用**（AC89）；输入普通文本走 `_chat_once`（既有路径）；未命中 `/nope` → 打印「未知命令 + /help 引导」、provider 零调用
3. 测试（状态栏）：`status_line()` 左段为 `[DEFAULT]`；`/plan` 后含 `[PLAN]`、`/do` 后回 `[DEFAULT]`；`/permission acceptEdits` 与既有 Shift+Tab `cycle_mode` 后标记随之变（AC88）
4. 测试（/clear）：聊几轮后 `/clear` → `session.messages` 归零、**session id 不变**、覆写落盘为空、`_persisted_count`/指纹/`_last_round_usage` 复位（AC92）；与 `/session new`（另建新 id）区分
5. 测试（归并回归）：`/help`/`/provider <name>`/`/exit`/`/plan`/`/do`/`/compact` 经注册中心分发后行为与归并前一致（既有断言迁移/复用）；`/session new`≡旧 `/new`、`/session list [--all]`≡旧 `/sessions [--all]`、`/session resume <id>`≡旧 `/resume <id>`（AC91/N35）
6. 测试（回归）：`commands=None`（未注入）→ 回退既有硬编码分发或等价；**非命令文本路径与既有 repl 测试零修改保持绿**（N35）
7. 跑测试确认失败（REPL 未实现协议 / 分发未重写 / 状态栏旧式 / clear 缺失）
**GREEN：** repl `_dispatch_command` 重写为「`parse(line)` → None 引导 → `registry.lookup(name)` → 未命中 /help 引导 → `spec.handler(self, args)` 返回真值退出」；REPL 增/迁协议方法（既有 `_cmd_*` 逻辑迁为 `clear_context`/`new_session`/`list_sessions`/`resume_session`/`switch_provider`/`compact_now` 等）；`status_line` 左段改 `[{mode.name}]` 括号式；新增 `clear_context`（`messages=[]`+复位游标/指纹/usage+`store.save`+留 id）；`send_user_message`=`_chat_once`；构造增 `commands` 鸭子注入
**REFACTOR：** 协议方法分组、help 的 Rich 表格渲染（在 `print`/`_cmd_help` 侧）抽小函数；保持绿
**验证：** `uv run pytest tests/test_repl.py -q` 全绿（假 provider+假 input 驱动分发/状态栏/clear/归并）
**注意：** REPL 作装配/界面层实现协议（既有 registry/executor 鸭子惯例延续）；`send_user_message` 复用 `_chat_once`（提示词类一轮 AI）；归并不改语义（N35）；命令分发不进 AgentLoop/权限门（命令本地可信，不做命令级权限）

## 波次四 · 装配 + 验收

## T115: C92 cli.build_app 装配 registry + completer 注入 + 版本 0.10.0（C92/F70/N37/N38）

**文件：** `src/wentian/cli.py`、`src/wentian/__init__.py`、`pyproject.toml`、`uv.lock`、`tests/test_cli.py`（续）、`tests/test_smoke.py`（续）
**依赖：** T112（build_builtin_registry）、T113（CommandCompleter）、T114（REPL commands 注入）
**RED：**
1. 测试：`build_app` 后 REPL 持非空 `commands`（`visible()` 含 12 命令）；`PromptInput` 持 `CommandCompleter`（构造断言）
2. 测试：**启动 panic**——注入一个故意冲突（重名/重别名）的 registry 工厂 → `build_app` 启动阶段 `raise`（断言进程级失败、不静默吞）
3. 测试（smoke/版本）：版本字符串 `0.10.0`；无配置/无命令输入冒烟 → 行为与 v0.9 一致（既有冒烟通过、退出码 0、无 traceback）
4. 跑测试确认失败（装配未接 / 版本旧）
**GREEN：** `cli.build_app` 调 `build_builtin_registry()`（冲突即 `raise` = panic）→ 注入 REPL；建 `CommandCompleter(registry)` 注入 `PromptInput`；版本升 `0.10.0`（源码+pyproject+lock）
**REFACTOR：** registry/completer 构造编排抽小函数；保持绿
**验证：** `uv run pytest tests/test_cli.py tests/test_smoke.py -q` 全绿
**注意：** 命令系统默认启用（无开关、核心交互）；冲突 `raise` 在 build_app = panic（N37）；零新增第三方依赖（pyproject diff 仅版本号，N38）

## T116: 离线验收回归（全量 pytest + 无命令/文本路径回归 + 分层 import 断言 + 冒烟 + ruff 收口）（N34/N35/N38）

**文件：** 全仓、`tests/test_layering.py`（续）
**依赖：** T108–T115
**步骤（非 TDD，验证收口）：**
1. `uv run pytest -q` → v0.1–v0.9 全部 + v0.10 新增全绿、无告警（基线 1056 → +N）
2. **分层现场检查（grep/ast 取证）**：`commands/` 包零 `rich`/`prompt_toolkit`/后端 SDK import；`commands/builtins.py` 不 import `repl` 具体类（只 import 同包 + `permissions.decision`）；`CommandCompleter` 在 `ui/completion.py`（prompt_toolkit 限 ui 层）；`commands/{spec,parser,registry,context}` 为叶子
3. `ruff format --check .` 通过、`ruff check .` 无告警
4. **无配置冒烟**：`printf '/exit\n' | uv run wentian`（空 cwd、无配置）→ 横幅示 v0.10.0、退出码 0、无 traceback、`/exit` 经注册中心分发干净退出
5. **离线端到端冒烟**：① `/help` 列 12 命令；② 普通文本走 AI 一轮（假 provider）、`/status` 走命令零请求；③ `/plan`→`[PLAN]`、`/do`→`[DEFAULT]`、`/clear` 消息归零 id 不变；④ `/session new`/`list --all`/`resume <id>` 三子命令；⑤ `/review` 经 send_user_message 触发一轮（假 provider）
6. **不破坏 v0.1–v0.9**：非命令文本路径与既有 repl/agent_loop 测试零修改全绿（字节级回归，N35）
7. `pyproject` diff 仅版本号、零新增依赖；checklist 离线项逐条取证
**验证：** 上述各项各留现场证据，记入 checklist；🌐👁 项（真终端 Tab 单补/多菜单弹出观感、真跑十命令一轮）单列、不阻塞离线验收

## v0.10 执行顺序

```
波次一（叶子原语，四件文件不相交，可并行派子 agent）：
  T108（spec.py，无依赖）
  T109（parser.py，无依赖）
  T110（registry.py，依赖 T108）
  T111（context.py，无依赖）
波次二（命令实现 + 补全，可并行）：
  T112（builtins.py，依赖 T108+T110+T111）
  T113（ui/completion.py + input.py，依赖 T110）
波次三（集成，依赖二）：
  T114（repl.py 实现协议 + 分发重写 + 状态栏 + /clear + 归并，依赖 T112）
波次四（装配 + 验收，依赖三）：
  T115（cli.build_app 装配 + 版本 0.10.0，依赖 T112+T113+T114）
  → T116（全量回归收尾，依赖 T108–T115）
```

- **波次一可并行**：`spec`/`parser`/`registry`/`context` 四件文件不相交（`registry` 依 `spec` 的类型，但接口稳定可先约定）；最先派或与其它波次错峰。
- **波次二相对独立**：`builtins`（命令逻辑）与 `ui/completion`（补全）文件不相交，可并行派两个子 agent；都依赖波次一产物。
- **波次三、四串行**：`repl.py` 集成需波次二的 registry；`cli.py` 装配需 REPL 的 `commands` 注入点；T116 收尾依赖全部。
- **波次四依赖一+二+三全部产物**：`cli.build_app` 聚合指令注入（T98）、会话分区/恢复/Compactor 压缩（T100/T101）、MemoryRunner（T105）、两槽真渲染（T106 内），故最后串行；T107 全量回归 + ruff 收口封版。

---

# v0.11 任务（T126–T135：Skill 系统）

> 基线：v0.10.0 离线全绿（1198 测试）。对应 plan C100–C107、spec F84–F90 / AC105–AC115。**纯包零反向依赖**（`skills/` 零 agent/provider/repl/commands/rich/prompt_toolkit）、复用 v0.5 槽 / v0.8 decorator / v0.4 allowed_tools / v0.10 命令注册中心 / v0.4 AgentLoop。零新增第三方依赖。

## 文件清单（v0.11）

| 操作 | 文件 | 职责 |
| 新建 | `src/wentian/skills/__init__.py` | 包入口（导出 Skill/SkillMode/SkillRegistry/discover_skills/render_body）|
| 新建 | `src/wentian/skills/base.py` | `Skill` dataclass + `SkillMode` 枚举（C100）|
| 新建 | `src/wentian/skills/loader.py` | `discover_skills`/`parse_skill`/`render_body`（C101）|
| 新建 | `src/wentian/skills/registry.py` | `SkillRegistry`（C102）|
| 新建 | `src/wentian/skills/builtin/commit.md`、`review.md`、`test.md` | 内置三样板（C107，importlib.resources 打包）|
| 新建 | `src/wentian/tools/skill_tool.py` | `LoadSkillTool`（系统级加载工具，C103）|
| 改 | `src/wentian/prompt/system.py` | `PromptContext.available_skills` + `_render_active_skills` 渲菜单（C105）|
| 改 | `src/wentian/prompt/reminders.py` | `build_request_decorator` 注入激活正文（C106）|
| 改 | `src/wentian/config.py` | `SkillsConfig` + 解析（C107）|
| 改 | `src/wentian/repl.py` | `SkillActivator` + 每轮喂 decorator/allowed_tools + `/clear`//`session new` 清激活 + `_skill_handler`（C104/C107）|
| 改 | `src/wentian/cli.py` | build_app 发现 + 白名单校验 fail-fast + 装配 + Skill→命令 + `/skills`//`reload` + 版本 0.11.0（C107）|
| 改 | `src/wentian/__init__.py`、`pyproject.toml`、`uv.lock` | 版本 0.11.0 + builtin 包数据 include |
| 新建 | `tests/test_skills_base.py`、`test_skills_loader.py`、`test_skills_registry.py`、`test_skill_tool.py`、`test_skill_activator.py` | 各组件测试 |
| 改 | `tests/test_system_prompt.py`、`test_reminders.py`、`test_config.py`、`test_repl.py`、`test_cli.py`、`test_smoke.py`、`test_layering.py` | 续测 |

## T126: C100 `skills/base.py` — Skill 数据模型（F84）
**文件：** `src/wentian/skills/base.py`、`tests/test_skills_base.py`
**依赖：** 无
**RED：** 写测试：构造 `Skill(name, description, body)` → 默认 `mode==SkillMode.SHARED`、`history==0`、`allowed_tools is None`、`model is None`、`source=="builtin"`；`frozen` 改字段抛 `FrozenInstanceError`；`SkillMode` 有 `SHARED`/`ISOLATED` 两值。跑测试确认失败（模块不存在）。
**GREEN：** 写 `SkillMode(Enum)` + `Skill` frozen dataclass（字段同 plan C100）。
**REFACTOR：** docstring 标注字段语义；保持绿。
**验证：** `uv run pytest tests/test_skills_base.py -q` 全绿。

## T127: C101 `skills/loader.py` — 发现/解析/占位符（F84/F85）
**文件：** `src/wentian/skills/loader.py`、`tests/test_skills_loader.py`
**依赖：** T126
**RED：** 写测试（临时目录）：①`parse_skill` 给定 `---\nname: x\ndescription: d\nmode: isolated\nallowed_tools: [read_file]\nhistory: 2\n---\n正文$ARGUMENTS` → 解析出各字段 + body；②缺 `name` / 坏 YAML / 无 frontmatter → 返回 `None`；③`render_body("a $ARGUMENTS b $1 $2", "fix typo")` → `"a fix typo b fix typo"`，无对应位置参数→空串；④`discover_skills`：项目层与用户层同 `name` → 取项目层（高层覆盖）；坏文件 + 合法文件 → 坏的跳过、合法发现；⑤目录型 `x/SKILL.md` 与单文件 `x.md` 解析等价、目录 `tools/` 子目录不报错。跑测试确认失败。
**GREEN：** 实现三函数：手解析 `---` frontmatter（stdlib，行扫描简单 `key: value` + `[a, b]` 列表，**不引第三方 YAML**）；`discover_skills` 扫三层（内置经 `importlib.resources`）、高层覆盖、单文件 try/except 跳过；`render_body` 先替 `$1/$2`（注意贪婪、`$10` 不误伤——用边界/按序）再替 `$ARGUMENTS`。
**REFACTOR：** 抽 frontmatter 切分小函数；保持绿。
**验证：** `uv run pytest tests/test_skills_loader.py -q` 全绿。

## T128: C102 `skills/registry.py` — SkillRegistry（F85）
**文件：** `src/wentian/skills/registry.py`、`tests/test_skills_registry.py`
**依赖：** T126
**RED：** 写测试：注册两个 Skill → `get(name)` 命中、`list()` 按 name 排序、`menu()` 返回 `(name, description)` 元组；同 name 二次注册 → 覆盖（高层覆盖语义，后注册胜或带 source 优先级，按 loader 约定）。跑测试确认失败。
**GREEN：** `SkillRegistry`：内部 `dict[str, Skill]`，`get`/`list`（sorted）/`menu`/`add`（覆盖）。
**REFACTOR：** 保持绿。
**验证：** `uv run pytest tests/test_skills_registry.py -q` 全绿。

## T129: C103 `tools/skill_tool.py` — LoadSkillTool（F86）
**文件：** `src/wentian/tools/skill_tool.py`、`tests/test_skill_tool.py`
**依赖：** T126
**RED：** 写测试：注入**假 activator**（记录 `activate` 调用、返回固定串）→ `LoadSkillTool(activator).run({"name":"x","args":"y"})` 转调 `activate("x","y")` 并回传其串；`name` 缺失 → 结构化错误（不抛）；`tool.name=="load_skill"`、参数 schema 含 `name`（必填）/`args`（可选）、`category` 为只读。跑测试确认失败。
**GREEN：** `LoadSkillTool(Tool)` 持鸭子 `activator`，`run` 取 `name`/`args`、调 `activator.activate`、返回字符串；缺参回结构化错误（沿用既有工具错误风格）。**不 import repl/skills/agent 具体类**。
**REFACTOR：** 保持绿。
**验证：** `uv run pytest tests/test_skill_tool.py -q` 全绿。

## T130: C105 `prompt/system.py` — 可用 Skill 菜单（F86）
**文件：** `src/wentian/prompt/system.py`、`tests/test_system_prompt.py`（续）
**依赖：** 无（纯函数）
**RED：** 写测试：`PromptContext(..., available_skills=(("commit","暂存并提交"),("review","评审改动")))` → `build_system_prompt` 含「# 可用 Skill」+ 两行 name+desc；`available_skills=()` → 该模块不出现、拼装**无空行残渣**（与既有空槽测试同款断言）。跑测试确认失败（当前 `_render_active_skills` 恒空）。
**GREEN：** `PromptContext` 加 `available_skills: tuple[tuple[str,str],...] = ()`；`_render_active_skills` 非空→渲菜单标题 + 列表（含「用 `load_skill` 加载完整指令」一行）、空→`""`。
**REFACTOR：** 保持绿；确认既有 `active_skills` 字段处理（保留或并入，不破既有测试）。
**验证：** `uv run pytest tests/test_system_prompt.py -q` 全绿、既有空槽测试不破。

## T131: C106 `prompt/reminders.py` — 激活正文注入（F87）
**文件：** `src/wentian/prompt/reminders.py`、`tests/test_reminders.py`（续）
**依赖：** 无（纯函数）
**RED：** 写测试：`build_request_decorator(env, plan_mode=False, active_skill_bodies=lambda: [("commit","正文A"),("review","正文B")])` → 装饰后**最新 user 消息**含两段 `<system-reminder># 已激活 Skill: commit\n正文A`、`...review\n正文B`；**入参 messages 不被 mutate**（深拷断言）；`active_skill_bodies=None` 或返回 `[]` → 与既有 v0.8 行为字节级一致（既有 reminders 测试零修改保持绿）；**live 读**：两次调用装饰器之间改变源返回值 → 第二次注入新值。跑测试确认失败。
**GREEN：** `build_request_decorator` 增可选 `active_skill_bodies: Callable[[],list[tuple[str,str]]]|None=None`；在既有 env/switch 提醒拼装后，把各激活正文 reminder 追加到最新 user 消息（深拷不 mutate）。
**REFACTOR：** 抽「贴 reminder 到末 user」小工具；保持绿。
**验证：** `uv run pytest tests/test_reminders.py -q` 全绿、既有零修改。

## T132: C107a `config.py` — SkillsConfig（F85/N45）
**文件：** `src/wentian/config.py`、`tests/test_config.py`（续）
**依赖：** 无
**RED：** 写测试：`load_config` 无 `skills:` 块 → `config.skills.enabled is True`（默认）；`skills:\n  enabled: false` → `False`；缺块/非映射安全降级不抛。跑测试确认失败。
**GREEN：** `@dataclass(frozen=True) class SkillsConfig: enabled: bool = True`；进 `Config`（`field(default_factory=SkillsConfig)`）；`_parse_block(raw.get("skills"), SkillsConfig())`。
**REFACTOR：** 保持绿。
**验证：** `uv run pytest tests/test_config.py -q` 全绿。

## T133: C104 `SkillActivator`（repl 层）— 双模式激活编排（F87/F89）
**文件：** `src/wentian/repl.py`（或 repl 邻接模块）、`tests/test_skill_activator.py`
**依赖：** T126/T127/T128（+ 既有 AgentLoop/provider）
**RED：** 写测试（假 provider）：
1. **SHARED**：`activate("commit","fix")` → 返回含「已激活」+「commit」的串；`active_bodies()` 含 `("commit", 渲染后正文)`（`$ARGUMENTS` 已替成 `fix`）；`allowed_tools()` = 该 Skill 白名单 ∪ `{load_skill}`。
2. **多 Skill + 白名单规则**：再 `activate` 一个**无** `allowed_tools` 的 Skill → `allowed_tools()` 返回 `None`（不收窄）；两个都有白名单 → 并集 ∪ load_skill。
3. **空集**：未激活 → `allowed_tools() is None`、`active_bodies()==[]`。
4. **ISOLATED**：`activate` 一个 `mode=isolated, history=2` Skill（假 provider 返回固定助手文本）→ 返回值 == 子对话末条助手正文；`active_bodies()` 仍为空（不进激活集）；worker 线程 join 干净、无泄漏；子对话起始带主历史末 2 条。
5. **clear**：`clear()` 后激活集空。
跑测试确认失败。
**GREEN：** 实现 `SkillActivator`：持 `SkillRegistry` + provider/loop 工厂 + 取主 messages/system 的回调；`activate` 按 `skill.mode` 分流（SHARED 进集、ISOLATED worker 线程 `asyncio.run` 嵌套 `AgentLoop`、收末条助手正文）；`active_bodies`/`allowed_tools`（并集 + load_skill 恒含 + 任一不限→None）/`clear`。
**REFACTOR：** 抽「跑子对话」私有方法；保持绿、ruff 过。
**验证：** `uv run pytest tests/test_skill_activator.py -q` 全绿。

## T134: C107b 装配 `cli.build_app` + `repl` 接线 + Skill→命令 + `/skills` + 内置 + 版本（F88/F90/N45/N48）
**文件：** `src/wentian/cli.py`、`src/wentian/repl.py`、`src/wentian/skills/builtin/*.md`、`src/wentian/__init__.py`、`pyproject.toml`、`uv.lock`、`tests/test_cli.py`（续）、`tests/test_repl.py`（续）、`tests/test_smoke.py`（续）
**依赖：** T126–T133
**RED：**
1. 测试：`build_app` 后 `PromptContext.available_skills` 含已发现 Skill 菜单；工具 registry 含 `load_skill`；命令 registry 含各 `/<skill>`（内置三样板 → `/commit`//`test` 新增、`/review` 被 Skill 替换）。
2. 测试（**fail-fast**）：注入一个 `allowed_tools:[no_such_tool]` 的 Skill → `build_app` 启动 `raise`、报错含 Skill 名 + 工具名；引用某 MCP 工具的 Skill 在该 Server 接入后**通过**（校验排 MCP 之后）。
3. 测试（**冲突策略**）：`name=review` Skill → 替换 `/review`；`name=exit` Skill → 斜杠注册跳过 + 告警、`load_skill("exit")` 仍可达、**启动不 panic**。
4. 测试：`/skills` 列举零 provider 调用；`/skills reload` 改文件后重扫生效、reload 引入错工具 Skill → 保旧不崩。
5. 测试：激活某 Skill 后 `/clear` 与 `/session new` → 激活集空。
6. 测试（回退/版本/冒烟）：`skills.enabled:false`/无 skills 目录 → `available_skills` 空、回退 v0.10 行为；版本 `0.11.0`；无配置冒烟退出码 0、无 traceback。
跑测试确认失败。
**GREEN：** 按 plan C107 装配 `cli.build_app`（发现→白名单校验 fail-fast 排 MCP 后→注菜单→注册 load_skill→建 SkillActivator→Skill 包成 PROMPT CommandSpec 注册 with 冲突策略→`/skills`//`skills reload`→版本 0.11.0）；`repl` 每轮喂 `active_bodies`/`allowed_tools`、`/clear`//`session new` 清激活集、`_skill_handler`；写内置 `commit`/`review`/`test` 三 `.md`（commit 含无 `Co-Authored-By`/不主动 push 约束）；`pyproject` include builtin 包数据。
**REFACTOR：** 装配编排抽小函数（`_assemble_skills`）；保持绿、ruff 过。
**验证：** `uv run pytest tests/test_cli.py tests/test_repl.py tests/test_smoke.py -q` 全绿。

## T135: 离线验收回归（全量 pytest + 分层 import 断言 + 冒烟 + ruff 收口）（N44/N45/N46/N48）
**文件：** 全仓、`tests/test_layering.py`（续）
**依赖：** T126–T134
**步骤（非 TDD，验证收口）：**
1. `uv run pytest -q` → v0.1–v0.10 全部 + v0.11 新增全绿、无新告警（基线 1198 → +N）。
2. **分层现场检查（grep/ast 取证）**：`skills/` 包零 `rich`/`prompt_toolkit`/后端 SDK/`agent`/`repl`/`commands` import；`tools/skill_tool.py` 不 import `repl` 具体类（只 import `tools.base` + 鸭子 activator）；`skills/{base,registry}` 为叶子、`loader` 仅 stdlib。
3. `ruff format --check .` 通过、`ruff check .` 无告警。
4. **无配置冒烟**：`printf '/exit\n' | uv run wentian`（空 cwd、无配置、无 skills 目录）→ 横幅示 v0.11.0、退出码 0、无 traceback、回退行为同 v0.10。
5. **离线端到端冒烟**：① `/skills` 列内置三样板；② `load_skill` 工具在工具声明里；③ 激活一个 shared Skill（假 provider）→ 下一轮 provider 收到的 messages 含正文 reminder、store 落盘不含；④ `/clear` 清激活集；⑤ isolated Skill 经 `load_skill`（假 provider）→ 工具结果为子对话末条助手正文。
6. **不破坏 v0.1–v0.10**：无 skills 路径与既有 repl/agent_loop/system_prompt/reminders 测试零修改全绿（字节级回归，N45）。
7. `pyproject` diff 仅版本号 + builtin include、零新增依赖；checklist 离线项逐条取证。
**验证：** 各项留现场证据，记入 checklist；🌐👁 项（真 provider 激活跑一轮观察正文生效、真终端 `/skills` 观感与 isolated 回流观感）单列、不阻塞离线验收。

## v0.11 执行顺序

```
波次一（叶子原语，文件不相交，可并行派子 agent）：
  T126（base.py，无依赖）
  T127（loader.py，依赖 T126）
  T128（registry.py，依赖 T126）
波次二（工具 + 系统提示 + 注入 + 配置，文件不相交，可并行）：
  T129（tools/skill_tool.py，依赖 T126）
  T130（prompt/system.py 菜单，无依赖）
  T131（prompt/reminders.py 注入，无依赖）
  T132（config.py SkillsConfig，无依赖）
波次三（激活器编排，依赖一）：
  T133（SkillActivator，依赖 T126/T127/T128 + AgentLoop）
波次四（装配 + 验收，依赖一+二+三）：
  T134（cli/repl 装配 + Skill→命令 + /skills + 内置 + 版本，依赖 T126–T133）
  → T135（全量回归收尾，依赖 T126–T134）
```

- **波次一可并行**：`base`/`loader`/`registry` 三件文件不相交（`loader`/`registry` 依 `base` 类型，接口稳定可先约定）。
- **波次二相对独立**：`skill_tool`（工具）/`system`（菜单）/`reminders`（注入）/`config`（配置）四件文件不相交、可并行派子 agent；`skill_tool` 依 `base`，其余纯函数改既有文件需注意不互踩（分属不同文件，安全）。
- **波次三串行**：`SkillActivator` 集成需 loader/registry + AgentLoop。
- **波次四依赖全部**：`cli.build_app` 聚合发现/校验/装配/Skill→命令/内置/版本，故最后串行；T135 全量回归 + 分层断言 + ruff 收口封版。

# v0.12 任务（T117–T125：Hook 生命周期系统）

> 教学隔离规约同前：一任务一提交 `[T#/C#/F#/N#]`、一组件一文件、docstring 标记版本/组件/特性。TDD 红-绿-重构不豁免；每波次后规格/质量评审。
> **复用底线**：匹配原语 `textmatch.match_one` 复用权限规则匹配语义（精确+glob 同心智）并扩展 `!`反向 / `/re/`正则；引擎注入**鸭子可选**（仿 `permission_gate`/`compactor`/`memory_runner`/`commands`：None ⇒ 回退 v0.11）。`hooks/` **纯包**（零 `rich`/零 `prompt_toolkit`/零后端 SDK），引擎/动作零 import `agent`/`repl`/`providers`/`tools`。HTTP 用 stdlib `urllib`、子进程 `subprocess`、并发 `threading`，零新增依赖。**失败软化铁律**：任一 hook 失败只记日志、绝不中断主流程。

## v0.12 文件清单

| 文件 | 动作 | 说明 |
| --- | --- | --- |
| `src/wentian/textmatch.py` | 新建 | C93 `match_one(pattern, value)` 四模式匹配，顶层叶子 |
| `src/wentian/hooks/__init__.py` | 新建 | hooks 纯包入口（导出 spec/conditions/config/engine 公共符号） |
| `src/wentian/hooks/spec.py` | 新建 | C94 `HookEvent`(10)+`Clause`/`Condition`+四 Action+`HookRule`+`INTERCEPT_EVENTS`，叶子 |
| `src/wentian/hooks/conditions.py` | 新建 | C95 `evaluate(condition, context)->bool`（all/any+match_one），叶子 |
| `src/wentian/hooks/config.py` | 新建 | C96 `parse_hooks(raw)->list[HookRule]`+`HookConfigError` 集中校验，近叶子 |
| `src/wentian/hooks/actions.py` | 新建 | C97 run_shell/inject_prompt/call_http/run_subagent（占位），各失败软化 |
| `src/wentian/hooks/engine.py` | 新建 | C98 `HookEngine`（fire/pretool/drain_injections/close + once/background/日志） |
| `src/wentian/config.py` | 改 | C99 `Config.hooks` + `load_config` 两层叠加经 `parse_hooks` |
| `src/wentian/repl.py` | 改 | C99 十事件缝触发 + PreToolUse 组合进 gate + request_decorator 冲 drain_injections |
| `src/wentian/context/compactor.py` | 改 | C99 压缩前 `on_pre_compact` 鸭子回调（PreCompact 缝，可选） |
| `src/wentian/cli.py` | 改 | C99 `build_app` 按 `config.hooks` 构造 `HookEngine` 注入 REPL（空 ⇒ 不注入） |
| `src/wentian/__init__.py`、`pyproject.toml`、`uv.lock` | 改 | 版本号（标 v0.12；实际 semver bump 装配期交用户，见 N43）、零新增依赖 |
| `tests/test_textmatch.py` | 新建 | T117 四模式 |
| `tests/test_hooks_spec.py` | 新建 | T118 枚举/Action/HookRule |
| `tests/test_hooks_conditions.py` | 新建 | T119 求值器 all/any/缺字段 |
| `tests/test_hooks_config.py` | 新建 | T120 解析 + 集中校验 |
| `tests/test_hooks_actions.py` | 新建 | T121 四动作（临时脚本 + 假 http server） |
| `tests/test_hooks_engine.py` | 新建 | T122 触发/拦截/执行控制/软化（假 ctx 夹具） |
| `tests/test_config.py`（续）、`tests/test_repl.py`（续）、`tests/test_compactor.py`（续）、`tests/test_cli.py`（续）、`tests/test_smoke.py`（续）、`tests/test_layering.py`（续） | 改 | T123/T124/T125 两层叠加/缝触发/拦截组合/注入/装配/分层/冒烟 |

## 波次一 · 叶子原语（并行）

## T117: C93 匹配叶子 textmatch.py：四模式 match_one（C93/F80）

**文件：** `src/wentian/textmatch.py`、`tests/test_textmatch.py`
**依赖：** 无（顶层叶子，可独立先行；与 T118 并行）
**RED：**
1. 测试：精确——`match_one("Bash","Bash")` 真、`match_one("Bash","Read")` 假
2. 测试：反向——`match_one("!Bash","Read")` 真、`match_one("!Bash","Bash")` 假（递归内层）
3. 测试：正则——`match_one("/rm\\s+-rf/","rm  -rf /")` 真、`match_one("/^git/","npm i")` 假；`re.search` 语义（非全匹配）
4. 测试：glob——`match_one("git *","git push")` 真、`match_one("git *","npm i")` 假；`?`/`[` 通配
5. 测试：非法正则（如 `/[/`）→ 不抛、返回 False；空 pattern 仅匹配空串
6. 跑测试确认失败（`match_one` 缺失）
**GREEN：** 实现 `match_one`：`!` 前缀 → `not match_one(pat[1:],value)`；首尾 `/` 且 len≥2 → `re.error` 捕获的 `re.search`；含 `*?[` → `fnmatch.fnmatchcase`；否则 `==`
**REFACTOR：** 模式判定分支抽小函数；保持绿
**验证：** `uv run pytest tests/test_textmatch.py -q` 全绿
**注意：** 顶层叶子——只 `re`/`fnmatch`、零业务 import；权限 `rules.py` 本版不改（N41）

## T118: C94 规则模型 hooks/spec.py：HookEvent + Action + HookRule（C94/F77/F78/F81/F82）

**文件：** `src/wentian/hooks/__init__.py`、`src/wentian/hooks/spec.py`、`tests/test_hooks_spec.py`
**依赖：** 无（叶子，与 T117 并行）
**RED：**
1. 测试：`HookEvent` 十值且 value 字符串正确（SessionStart…Notification）；`INTERCEPT_EVENTS == {HookEvent.PRE_TOOL_USE}`
2. 测试：`Match` 二值（all/any）；`Clause(field,pattern)`、`Condition(match=ALL, clauses=())` 默认 + frozen
3. 测试：四 Action（Shell/Prompt/Http/SubAgent）构造 + 必填/默认字段；`HookRule(event,action)` 默认 `condition=None`/`once=False`/`background=False`、frozen
4. 跑测试确认失败
**GREEN：** 实现 `hooks/spec.py`：枚举 + dataclass（见 plan C94）；`Action` 联合别名；`hooks/__init__.py` 导出
**REFACTOR：** 字段注释整理；保持绿
**验证：** `uv run pytest tests/test_hooks_spec.py -q` 全绿
**注意：** 叶子——`dataclasses`/`enum`/`typing`；零业务 import（N41）

## T119: C95 条件求值 hooks/conditions.py：evaluate all/any（C95/F80）

**文件：** `src/wentian/hooks/conditions.py`、`tests/test_hooks_conditions.py`
**依赖：** T117（match_one）、T118（Condition/Clause）；与 T120 并行
**RED：**
1. 测试：`evaluate(None, ctx)` → True（无条件恒触发）；`Condition(clauses=())` → True
2. 测试：`match=ALL` 两子句全真 → True、一假 → False
3. 测试：`match=ANY` 一真 → True、全假 → False
4. 测试：字段缺失——`Clause("command","/rm/")` 而 ctx 无 command → 按空串参与（`match_one("/rm/","")` False）；`!Bash` 对缺失字段 → True
5. 跑测试确认失败
**GREEN：** 实现 `evaluate`：None/空 clauses → True；逐子句 `match_one(c.pattern, str(ctx.get(c.field,"")))`，按 `all()`/`any()` 归约
**REFACTOR：** 归约逻辑收敛；保持绿
**验证：** `uv run pytest tests/test_hooks_conditions.py -q` 全绿
**注意：** 叶子——import `textmatch` + 同包 `spec`（N41）

## T120: C96 配置加载 hooks/config.py：parse_hooks + 集中校验（C96/F77/F82）

**文件：** `src/wentian/hooks/config.py`、`tests/test_hooks_config.py`
**依赖：** T118（HookRule/Action/HookEvent）；与 T119 并行
**RED：**
1. 测试：合法多规则 raw（list[dict]）→ `list[HookRule]`，event/action/if/once/background 解析齐备；缺 `if` → `condition=None`
2. 测试：缺块（None / 非 list）→ `[]`（安全降级）
3. 测试（校验 raise）：非法 event 名 → `HookConfigError`；`action.type` 缺 / 非四类 → raise；shell 缺 `command`、http 缺 `url`、prompt 缺 `text` → raise
4. 测试（校验 raise）：`event=PreToolUse` 且 `background:true` → raise（拦截禁异步）；`timeout<=0` → raise；`if.match` 非 all/any → raise
5. 测试：报错信息带条目下标 / 字段名（定位）
6. 跑测试确认失败
**GREEN：** 实现 `parse_hooks`：逐条 dict → 解析 event（名→枚举）、action（type→对应 dataclass + 必填校验）、condition（match+clauses）、once/background；逐项校验违规 `raise HookConfigError(f"hooks[{i}]: …")`；缺块 →[]
**REFACTOR：** action 解析、校验抽小函数；保持绿
**验证：** `uv run pytest tests/test_hooks_config.py -q` 全绿
**注意：** 近叶子——import 同包 `spec`，**自带 `HookConfigError`**（不 import `wentian.config`、避免环，N41）

## 波次二 · 动作 + 引擎

## T121: C97 动作执行 hooks/actions.py：四动作 + 失败软化（C97/F81/F83）

**文件：** `src/wentian/hooks/actions.py`、`tests/test_hooks_actions.py`
**依赖：** T118（Action 类型）；与 T119/T120 可并行（文件不相交）
**RED：**
1. 测试（shell）：临时脚本 `cat`（回显 stdin）→ 断言收到 `json.dumps(context)`；脚本读 `$WENTIAN_HOOK_TOOL_NAME` → 断言 env 注入；脚本 `exit 2` → 返回 exit_code=2 + stderr
2. 测试（shell 超时）：`sleep 5` + `timeout=1` → 终止、标记 timed_out、不抛
3. 测试（prompt）：`PromptAction("hi {tool_name}")` + ctx → 返回 `"hi Bash"`；缺键 `{nope}` 保留字面不抛
4. 测试（http）：stdlib `http.server` 起本地假 server → `call_http` POST 收到 JSON 体 + Content-Type；返回状态码；连不上的 url → None（软化、不抛）
5. 测试（subagent）：`run_subagent` 不抛、记一条「未实现」日志
6. 测试（软化）：各动作内部异常被捕获、返回结构化结果或 None、不冒泡
7. 跑测试确认失败
**GREEN：** 实现四执行器（见 plan C97）：`subprocess.run(shell=True, input=json, env=…, timeout=…, capture_output=True)`；`text.format_map(SafeDict)`；`urllib.request.Request(url, data=json, method=…)` + `urlopen(timeout=…)`；subagent 日志占位；全包 try/except 软化
**REFACTOR：** env 构造、SafeDict、结果类型抽小函数；保持绿
**验证：** `uv run pytest tests/test_hooks_actions.py -q` 全绿
**注意：** 近叶子——`subprocess`/`urllib`/`json`/`os` + 同包 `spec`；零 rich/ptk/provider/agent import；零新增依赖（N41/N43）；http 测试用本地假 server 不联网（N39）

## T122: C98 引擎 hooks/engine.py：fire/pretool/drain + 执行控制 + 软化（C98/F78/F79/F82/F83）

**文件：** `src/wentian/hooks/engine.py`、`tests/test_hooks_engine.py`
**依赖：** T118（spec）、T119（conditions.evaluate）、T121（actions）
**RED：**
1. 测试（fire 非拦截）：注册 SessionStart 规则（shell 动作）→ `fire(SessionStart, ctx)` 跑该动作；条件不命中 → 跳过；`prompt` 动作产出进 `_pending`、`drain_injections()` 取出并清空
2. 测试（once）：`once=True` 规则 `fire` 两次 → 仅第一次执行（内存集记账）
3. 测试（background）：`background=True` 动作投 daemon 线程、`fire` 立即返回不阻塞（用慢动作 + 计时或事件断言主线程不等）
4. 测试（pretool 拦截）：PreToolUse + shell `exit 2` → `pretool(ctx)` 返回拒绝原因（stderr）；`exit 0` → None；多规则按声明顺序、首个 exit2 短路
5. 测试（pretool fail-open）：shell 脚本缺失 / 超时 / 退出码非 0 非 2 → 记日志、返回 None（fail-open，不拦）
6. 测试（软化）：任一动作抛异常 → 引擎 try/except 吞进日志、`fire`/`pretool` 不冒泡
7. 测试（close）：后台线程 `close()` 短 join 不卡、无泄漏
8. 跑测试确认失败
**GREEN：** 实现 `HookEngine`：`_by_event: dict[HookEvent,list[HookRule]]`；`fire`（选 evaluate 命中 + once 未触发 → 同步/后台执行动作、prompt 入 `_pending`、全程软化）；`pretool`（顺序跑 PreToolUse 命中规则、shell exit2 → 原因短路、失败 fail-open）；`drain_injections`；`_fired_once: set`；daemon 线程；`close`；logger（缺省落 hooks.log）
**REFACTOR：** 动作分派、软化包装、once 记账抽小函数；保持绿
**验证：** `uv run pytest tests/test_hooks_engine.py -q` 全绿（假 ctx 夹具驱动）
**注意：** import 同包 `spec`/`conditions`/`actions`；**零** import `agent`/`repl`/`providers`/`tools`（事件上下文纯 dict 由装配层喂，N41）；失败软化铁律（N42）

## 波次三 · 配置接入 + 装配

## T123: C99 config.py 接入 hooks：Config.hooks + 两层叠加（C99/F77）

**文件：** `src/wentian/config.py`、`tests/test_config.py`（续）
**依赖：** T118（HookRule）、T120（parse_hooks）
**RED：**
1. 测试：单文件含 `hooks:` → `Config.hooks` 为解析后的 `list[HookRule]`；无 `hooks:` → `[]`
2. 测试（两层叠加）：用户级 + 项目级各有 `hooks` 列表 → `Config.hooks` = 用户 + 项目**拼接**（都在、非覆盖；与 providers 同名覆盖语义区分）
3. 测试：`hooks:` 非法（如 PreToolUse+background）→ `load_config` 透传 `HookConfigError`（启动失败）
4. 跑测试确认失败
**GREEN：** `Config` 加 `hooks: list[HookRule] = field(default_factory=list)`；`_build_config_from_raw` 调 `parse_hooks(raw.get("hooks"))`；`load_config` 两层模式下**分别**取用户 / 项目原始 `hooks` 列表拼接（不进 `_deep_merge`、规则累积）
**REFACTOR：** 两层 hooks 拼接抽小函数；保持绿
**验证：** `uv run pytest tests/test_config.py -q` 全绿
**注意：** 单向 config→`hooks.config` import（hooks 不 import config、无环）；无 `hooks:` 时 `Config.hooks==[]`、与 v0.11 等价（N40）

## T124: C99 装配 repl/compactor/cli：缝触发 + 拦截组合 + 注入 + 构造（C99/F78/F79/F83）

**文件：** `src/wentian/repl.py`、`src/wentian/context/compactor.py`、`src/wentian/cli.py`、`tests/test_repl.py`（续）、`tests/test_compactor.py`（续）、`tests/test_cli.py`（续）
**依赖：** T122（HookEngine）、T123（Config.hooks）
**RED：**
1. 测试（缝触发）：注入**假引擎**（记录 `fire(event,ctx)` 调用）+ 假 provider 跑一轮带工具 → 断言 SessionStart/UserPromptSubmit/RoundStart/PostToolUse/RoundEnd/Stop/SessionEnd 各被 `fire`、上下文字段齐备（prompt/tool_name/stop_reason…）
2. 测试（PreToolUse 组合）：假引擎 `pretool` 返回原因 → 工具被拦、回灌 outcome（`is_error=True`）、**不进 permission_gate / executor**；`pretool` 返回 None → 原样落既有 `permission_gate`（既有行为不变）
3. 测试（Notification）：权限门 ASK 时 `fire(Notification, {kind:"permission_ask",…})`
4. 测试（PreCompact）：`compactor.compact()` 摘要前调 `on_pre_compact(trigger)` 钩子（manual + auto 各一次）；钩子 None ⇒ 不调、与既有等价
5. 测试（注入）：假引擎 `drain_injections` 返回文本 → 下一轮 `request_decorator` 的 outgoing 含该文（`<system-reminder>`）、**原 messages 不变**、不持久化
6. 测试（cli 构造）：`build_app` 有 `config.hooks` → REPL 持非空 `hooks` 引擎；无 hooks → `hooks=None`、不注入
7. 测试（回退）：`hooks=None` 的 REPL 所有缝点空操作、不抛；既有 repl/compactor 测试零修改保持绿
8. 跑测试确认失败
**GREEN：** repl 在各缝插 `engine.fire`（None 守卫）；`_build_gate` 组合 `engine.pretool` 在 `permission_gate` 之前（拦 → 合成拒绝 outcome 短路）；`request_decorator` 并入 `drain_injections`；compactor 加 `on_pre_compact` 鸭子形参、摘要前调用；`cli.build_app` 按 `config.hooks` 构造 `HookEngine` 注入（空 ⇒ None）；hook 日志路径
**REFACTOR：** 事件上下文构造（`_tool_ctx`/`_round_ctx` 等）、gate 组合、注入冲入抽小函数；保持绿
**验证：** `uv run pytest tests/test_repl.py tests/test_compactor.py tests/test_cli.py -q` 全绿
**注意：** 装配/界面层 import `hooks.engine`、构造事件上下文 dict（鸭子）；不改 AgentLoop 契约 / 不改 gate 签名（N40）；失败软化在引擎侧（缝调用本身也 None 守卫）；compactor 钩子仿 loop 的 `pre_round_compact` 鸭子可选

## 波次四 · 验收

## T125: 离线验收回归（全量 pytest + 无 hooks 回归 + 分层 import 断言 + 冒烟 + ruff 收口）（N39/N40/N43）

**文件：** 全仓、`tests/test_layering.py`（续）
**依赖：** T117–T124
**步骤（非 TDD，验证收口）：**
1. `uv run pytest -q` → v0.1–v0.11 全部 + v0.12 新增全绿、无告警（基线 = c32c091 当前全量 → +N）
2. **分层现场检查（grep/ast 取证）**：`hooks/` 包零 `rich`/`prompt_toolkit`/后端 SDK import；`hooks/engine.py`/`hooks/actions.py` 零 import `wentian.agent`/`repl`/`providers`/`tools`；`textmatch.py` 叶子（只 `re`/`fnmatch`）；`config.py`→`hooks.config` 单向无环
3. `ruff format --check .` 通过、`ruff check .` 无告警
4. **无 hooks 冒烟**：`printf '/exit\n' | uv run wentian`（空 cwd、无 `hooks:` 配置）→ 退出码 0、无 traceback、行为与 v0.11 一致（引擎不注入、缝点空操作）
5. **离线端到端冒烟**：① 配一条 PreToolUse 拦 `rm -rf` 的 shell `exit 2` 规则 → 假 provider 触发危险命令 → 断言被拦、原因回灌；② UserPromptSubmit prompt 注入 → 下轮 `<system-reminder>` 含注入文；③ PostToolUse http（本地假 server）收到审计 JSON；④ once 规则只触发一次
6. **不破坏 v0.1–v0.11**：无 hooks / `hooks=None` 路径与既有 repl/agent_loop/compactor 测试零修改全绿（字节级回归，N40）
7. 版本号同步（标 v0.12，实际 semver bump 交用户拍板，见 N43）；`pyproject` diff 零新增依赖；checklist 离线项逐条取证
**验证：** 上述各项各留现场证据，记入 checklist；🌐👁 项（真终端真 shell 拦截 / 真 http 审计 / 真桌面通知）单列、不阻塞离线验收

## v0.12 执行顺序

```
波次一（叶子原语，文件不相交，可并行派子 agent）：
  T117（textmatch.py，无依赖）
  T118（hooks/spec.py，无依赖）
  T119（hooks/conditions.py，依赖 T117+T118）
  T120（hooks/config.py，依赖 T118）
波次二（动作 + 引擎）：
  T121（hooks/actions.py，依赖 T118；与 T119/T120 文件不相交可并行）
  T122（hooks/engine.py，依赖 T118+T119+T121）
波次三（配置接入 + 装配）：
  T123（config.py 接入 hooks，依赖 T118+T120）
  T124（repl/compactor/cli 装配，依赖 T122+T123）
波次四（验收）：
  → T125（全量回归收尾，依赖 T117–T124）
```

- **波次一可并行**：`textmatch`/`spec` 无依赖先行；`conditions`（依 textmatch+spec）、`config`（依 spec）接口稳定可错峰派；四件文件不相交。
- **波次二相对独立**：`actions`（依 spec）与 `conditions`/`config` 文件不相交可并行；`engine` 聚合 spec/conditions/actions，最后串。
- **波次三串行**：`config.py` 接入需 `parse_hooks`；`repl/compactor/cli` 装配需 `HookEngine` + `Config.hooks` 注入点。
- **波次四依赖全部**：T125 全量回归 + 分层 + 无 hooks 冒烟 + ruff 收口封版；真终端拦截 / http / 通知留 🌐👁。

---

# v0.13 任务（T136–T147：子 Agent 委派）

> 教学隔离规约同前：一任务一提交 `[T#/C#/F#/N#]`、一组件一文件、docstring 标记版本/组件/特性。TDD 红-绿-重构不豁免；每波次后规格/质量评审。
> **复用底线**：零重造、零新增第三方依赖。复用 v0.4 `AgentLoop`（子 Agent 独立实例）、v0.x `create_provider`/`ProviderConfig`（全新 provider 实例、并发安全）、v0.6 `PermissionPipeline`/`build_permission_gate`（子 Agent 独立 Mode + 独立门）、v0.5/v0.8 `request_decorator` 注入通道（回灌缓冲）、v0.11 共享 frontmatter 原语（skills + agents 共用解析）、v0.12 `HookEngine`（`SubAgentAction` 接通真起子 Agent）、v0.3/v0.4 `Tool` 基类 + `allowed_tools` 过滤。`agents/` 纯叶子分层（`spec`/`filter` stdlib only；`loader` 叶子；`runner` 不依 `repl`/`cli`；`tool` 鸭子注入）；装配缝在 `cli`/`repl`。线程用 stdlib `threading`，零新增第三方依赖。

## v0.13 文件清单

| 操作 | 文件 | 职责 |
| --- | --- | --- |
| 新建 | `src/wentian/agents/__init__.py` | agents 包入口（导出 AgentDef/AgentRegistry/AgentType/TaskStatus/BackgroundTask/discover_agents/resolve_allowed_tools/BackgroundTaskManager/AgentTool） |
| 新建 | `src/wentian/agents/spec.py` | C108：`AgentType(Enum)`、`TaskStatus(Enum)`、`AgentDef`（frozen dataclass）、`BackgroundTask` dataclass；stdlib only 叶子 |
| 新建 | `src/wentian/frontmatter.py` | C109：共享 frontmatter 解析原语（`_split_frontmatter`/`parse_frontmatter`）；从 v0.11 `skills/loader.py` 抽取，叶子 stdlib only |
| 改 | `src/wentian/skills/loader.py` | C109：改用 `wentian.frontmatter` 原语；skills 行为不变、v0.11 测试零回归 |
| 新建 | `src/wentian/agents/filter.py` | C111：`resolve_allowed_tools(all_tools, role_allow, role_deny, *, background, globally_forbidden)→frozenset[str]`；纯函数叶子，仅依 `spec`/`decision.Category` |
| 新建 | `src/wentian/agents/loader.py` | C110：`parse_agent(text, *, name_hint, source)→AgentDef|None`、`discover_agents(project_dir, user_dir, *, builtin, plugin_dir)→AgentRegistry`；四源发现/覆盖/跳过；叶子，依共享 frontmatter 原语 + spec |
| 新建 | `src/wentian/agents/runner.py` | C112：`run_subagent(agent_def_or_fork, prompt, *, base_provider_cfg, provider_factory, registry, executor, pipeline, settings, model_aliases, parent_messages, depth)→SubAgentResult`；依 `agent.loop`/`providers`/`permissions`，不依 `repl`/`cli` |
| 新建 | `src/wentian/agents/manager.py` | C113：`BackgroundTaskManager`（`submit`/`get`/`list`/`drain_completions`/`close`）；三种进后台 + Fork 恒后台 + 超时转后台（可注入时钟）；依 runner |
| 新建 | `src/wentian/agents/tool.py` | C114：`AgentTool(Tool)`；name=`Agent`、schema(type/agent_type/prompt/background)；definition/fork 分流 + 嵌套深度检查；鸭子注入 registry/runner/manager |
| 改 | `src/wentian/config.py` | C115：`AgentsConfig(enabled, model_aliases, default_max_turns, foreground_timeout_s, background_allow)` + `_parse_agents`；`Config.agents` 字段；`__all__` 导出 |
| 改 | `src/wentian/commands/builtins.py` | C116：`/agents` 命令（LOCAL 类；无参列出后台任务、`/agents <id>` 查看结果全文）；鸭子 manager 句柄 |
| 改 | `src/wentian/cli.py` | C117：`build_app` 装配 `discover_agents`→AgentRegistry、`BackgroundTaskManager`、注册 `AgentTool`、`/agents` 命令；`manager.drain_completions` 接入 `request_decorator` 链；无 `agents` 配置 ⇒ 各注入 None/空、Agent 工具仍注册 |
| 改 | `src/wentian/repl.py` | C117：暴露 manager 句柄给 `/agents`；REPL 状态行反映后台任务计数 |
| 改 | `src/wentian/hooks/actions.py` | C117/F102：`SubAgentAction` 接通真起子 Agent（经 manager/runner）；失败仍软化 |
| 新建 | `tests/test_agents_spec.py` | T136：AgentType/TaskStatus/AgentDef/BackgroundTask 单测 |
| 新建 | `tests/test_frontmatter.py` | T137：共享 frontmatter 原语单测 + skills 行为零回归断言 |
| 新建 | `tests/test_agents_filter.py` | T138：三层过滤集合运算 + 嵌套防护 + 后台白名单单测 |
| 新建 | `tests/test_agents_loader.py` | T139：四源发现/覆盖/跳过单测（临时目录） |
| 新建 | `tests/test_agents_runner.py` | T141：runner 假 provider 端到端 + 错误转结构化单测 |
| 新建 | `tests/test_agents_manager.py` | T142：BackgroundTaskManager 三种进后台 + 回灌 + 线程不泄漏单测 |
| 新建 | `tests/test_agents_tool.py` | T143：AgentTool schema + 分流 + 嵌套拦截单测 |
| 改 | `tests/test_config.py` | T140：AgentsConfig 解析 + model 别名映射单测 |
| 改 | `tests/test_commands_builtins.py` | T144：`/agents` 命令列出/查看单测 |
| 改 | `tests/test_cli.py`、`tests/test_repl.py` | T145：装配接线断言 + 无 `agents` 配置回退断言 |
| 改 | `tests/test_hooks_actions.py` | T146：SubAgentAction 接通 + 失败软化断言 |
| 改 | `tests/test_layering.py`、`tests/test_smoke.py` | T147：分层 import 边界断言 + 无配置冒烟 + 全量回归 |

---

## 波次一 · 叶子原语（T136∥T137，T138 依 T136）

## T136: C108 `agents/spec.py` — 数据模型（F91/F92/F98）

**文件：** `src/wentian/agents/spec.py`、`src/wentian/agents/__init__.py`、`tests/test_agents_spec.py`
**依赖：** 无（stdlib only 叶子，可独立先行）
**RED：**
1. 写测试：`AgentType` 有 `DEFINITION`/`FORK` 两值；`TaskStatus` 有 `RUNNING`/`DONE`/`FAILED` 三值。
2. 写测试：`AgentDef(name="coder", description="写代码", body="你是…", tools=("read_file",), disallowed_tools=(), model="inherit", max_turns=None, permission_mode=None, source="builtin")` 可构造、frozen（改字段抛 `FrozenInstanceError`）；`tools=None` 合法（无白名单约束）。
3. 写测试：`BackgroundTask(id="1", kind=AgentType.DEFINITION, label="coder", status=TaskStatus.RUNNING, result=None, usage={}, prompt="帮我写…", created_at=0.0)` 可构造；`status` 可变（非 frozen）；`result`/`usage` 可后续赋值。
4. 跑测试确认失败（模块不存在）。
**GREEN：** 实现 `agents/spec.py`：`AgentType(Enum)`、`TaskStatus(Enum)`；`@dataclass(frozen=True) AgentDef` 含 name/description/body/tools/disallowed_tools/model/max_turns/permission_mode/source 字段；`@dataclass BackgroundTask` 含 id/kind/label/status/result/usage/prompt/created_at 字段（可变）。`agents/__init__.py` 导出。
**REFACTOR：** docstring 标注 `v0.13 · C108 · F91/F92/F98`；字段类型标注收紧；保持绿。
**验证：** `uv run pytest tests/test_agents_spec.py -q` 全绿。

---

## T137: C109 `frontmatter.py` — 共享 frontmatter 原语抽取（F93/N55/AC118）

**文件：** `src/wentian/frontmatter.py`、`src/wentian/skills/loader.py`（改 import）、`tests/test_frontmatter.py`
**依赖：** 无（触 v0.11 文件；需跑 skills 全测零回归）
**RED（双重 RED：先证既有行为不变 + 新原语单测）：**
1. 写测试（**skills 行为保护**）：不改任何实现代码，先跑 `uv run pytest tests/test_skills_loader.py -q` 并记录基线通过数（应全绿），作为本任务 RED 阶段的回归基线证明——此时 `frontmatter.py` 尚不存在，skills 仍用自身私有实现。
2. 写新测试（`tests/test_frontmatter.py`）：`parse_frontmatter("---\nname: x\ndescription: d\ntools: [read_file, write_file]\n---\n正文内容")` → 返回 `({"name":"x","description":"d","tools":["read_file","write_file"]}, "正文内容")`；缺 frontmatter 或无 `---` → 返回 `({}, 原文)`；坏 YAML / 非映射 → 返回 `({}, body)`（跳过不抛）；list 值（`[a, b]`）解析为 Python list；嵌套 key: value 行解析为 str。
3. 跑 `tests/test_frontmatter.py` 确认失败（`frontmatter.py` 不存在）。
**GREEN：**
1. 创建 `src/wentian/frontmatter.py`：从 `skills/loader.py` 抽取 `_split_frontmatter`/`_parse_frontmatter` 逻辑为公开函数 `parse_frontmatter(text)→tuple[dict,str]`（stdlib 行扫描，不引第三方 YAML）。
2. 改 `skills/loader.py`：删除私有 frontmatter 函数、改 import `from wentian.frontmatter import parse_frontmatter`，其余 skills 逻辑不变。
**REFACTOR：** `frontmatter.py` docstring 标注 `v0.13 · C109 · F93/N55`；保持绿、保持 ruff。
**验证：**
- `uv run pytest tests/test_frontmatter.py -q` 全绿（原语单测）。
- `uv run pytest tests/test_skills_loader.py tests/test_skill_tool.py tests/test_skill_activator.py -q` 全绿（**v0.11 skills 零回归**，证明抽取后 skills 行为不变）。

---

## T138: C111 `agents/filter.py` — 三层过滤 + 嵌套防护（F97/N52/AC122）

**文件：** `src/wentian/agents/filter.py`、`tests/test_agents_filter.py`
**依赖：** T136（AgentDef/AgentType 中 source；filter 仅依 spec 与 decision.Category）
**RED：**
1. 写测试：`resolve_allowed_tools({"Read","Write","Bash","Agent"}, role_allow=None, role_deny=(), background=False)` → 结果不含 `"Agent"`（全局禁止），其余三个在；`globally_forbidden` 默认为 `frozenset({"Agent"})`。
2. 写测试：`role_allow=("Read","Write")` → 结果 = `{"Read","Write"}` ∩ 全集（白名单收窄）；`role_allow=None` → 不收窄（全集）；`role_deny=("Bash",)` → 结果无 `"Bash"`（黑名单减去）。
3. 写测试：`background=True` 时，`FILE_WRITE` 类和 `COMMAND_EXEC` 类工具被过滤；只剩 `READ_ONLY` 类和显式后台白名单（`background_allow` 参数）允许的工具（断言：Bash/Write 不在结果、Read 在）。
4. 写测试：集合运算顺序验证——黑名单扣除应在全局禁止之后（即使角色黑名单不含 `Agent`，`Agent` 仍被过滤）；白名单不能「救回」被全局禁止的工具（`role_allow=("Agent","Read")` → `Agent` 仍不在结果）。
5. 写测试：**嵌套防护**——`globally_forbidden=frozenset({"Agent","AnotherTool"})` 中多个工具均不在结果。
6. 跑测试确认失败（模块不存在）。
**GREEN：** 实现 `resolve_allowed_tools`：纯函数集合运算：① `base = all_tools - globally_forbidden`；② 若 `role_allow is not None`：`base = base ∩ frozenset(role_allow)`；③ `base = base - frozenset(role_deny)`；④ 若 `background`：`base = base ∩ (frozenset(background_allow) | {t for t in base if _is_read_only(t)})`，其中 `_is_read_only` 按 `decision.Category` 判定。返回 `frozenset(base)`。
**REFACTOR：** 函数签名加完整类型标注；docstring 标注 `v0.13 · C111 · F97/N52`；保持绿。
**验证：** `uv run pytest tests/test_agents_filter.py -q` 全绿。

---

## 波次二 · 加载 + 执行（T139，T140 并行 → T141 串行）

## T139: C110 `agents/loader.py` — 四源发现/覆盖/跳过（F92/F93/AC117）

**文件：** `src/wentian/agents/loader.py`、`tests/test_agents_loader.py`
**依赖：** T136（AgentDef/AgentType）、T137（共享 frontmatter 原语 `parse_frontmatter`）
**RED：**
1. 写测试（`parse_agent`）：给定合法 Markdown + frontmatter（name/description/tools/model/max-turns/permission-mode）→ 返回 `AgentDef`，所有字段正确；`max-turns=5` → `max_turns=5`（int）；`model` 缺省 → `"inherit"`。
2. 写测试（`parse_agent` 容错）：缺 `name` 字段 → 返回 None；坏 frontmatter（yaml 解析错误）→ 返回 None；空文件 → 返回 None。
3. 写测试（`discover_agents`，用 `tmp_path` 构造四层目录）：同 `name=coder` 的角色在项目层和用户层均存在 → 取项目层（高层整体覆盖）；用户层有 `name=analyst`、内置有 `name=reviewer` → 三个都在 registry；`name_hint` 取自文件名（无 frontmatter name 时跳过）。
4. 写测试（容错）：某文件 frontmatter 坏 / 缺 name → 被静默跳过、不阻断其余文件发现；registry 仍含其余合法角色。
5. 写测试（`AgentRegistry`）：`get("coder")` 命中返回 AgentDef；`get("不存在")` 返回 None；`list()` 返回所有 AgentDef 排序列表。
6. 跑测试确认失败（模块不存在）。
**GREEN：** 实现 `parse_agent`（调 `parse_frontmatter`、提取字段、建 AgentDef）；`AgentRegistry`（dict + get/list/add 覆盖）；`discover_agents`（按项目→用户→内置→插件顺序扫目录，`importlib.resources` 取内置，高层覆盖，单文件 try/except 跳过）。
**REFACTOR：** 四源扫描抽 `_load_layer` 小函数；docstring 标注 `v0.13 · C110 · F92/F93`；保持绿。
**验证：** `uv run pytest tests/test_agents_loader.py -q` 全绿。

---

## T140: C115 `config.py` AgentsConfig + model 别名映射（F100/AC125）

**文件：** `src/wentian/config.py`、`tests/test_config.py`（续）
**依赖：** 无（改既有文件、纯函数，可与 T139 并行）
**RED：**
1. 写测试：无 `agents:` 块 → `config.agents.enabled is True`（默认开）；`agents:\n  enabled: false` → `False`；`agents:\n  default_max_turns: 20` → `default_max_turns=20`；缺块/非映射安全降级不抛、返回默认 `AgentsConfig()`。
2. 写测试（model 别名映射）：`AgentsConfig()` 默认 `model_aliases` 包含 `{"haiku":"claude-haiku-4-5","sonnet":"claude-sonnet-4-6","opus":"claude-opus-4-8","inherit":"__inherit__"}`（或等价约定的 inherit 标记）。
3. 写测试：`agents:\n  model_aliases:\n    haiku: my-haiku-model` → `config.agents.model_aliases["haiku"] == "my-haiku-model"`（用户可自定义覆盖）；未指定键保留默认。
4. 写测试：`foreground_timeout_s` 默认值大于 0（如 30.0）；`background_allow` 默认含至少一个 READ_ONLY 类工具名。
5. 跑测试确认失败（`AgentsConfig` 缺失）。
**GREEN：** 在 `config.py` 加 `@dataclass(frozen=True) AgentsConfig` 含 enabled/model_aliases/default_max_turns/foreground_timeout_s/background_allow 字段（合理默认值）；实现 `_parse_agents(raw)→AgentsConfig`（仿 `_parse_skills`/`MemoryConfig` 模式，缺块→全默认）；`Config` 加 `agents: AgentsConfig = field(default_factory=AgentsConfig)`；`__all__` 导出 `AgentsConfig`。
**REFACTOR：** `_parse_agents` 抽小函数；docstring 标注 `v0.13 · C115 · F100`；保持绿。
**验证：** `uv run pytest tests/test_config.py -q` 全绿（含已有断言，无回归）。

---

## T141: C112 `agents/runner.py` — 隔离 AgentLoop 跑到底（F94/F95/F96/F100/N49/N53/N54/AC119/AC121/AC125）

**文件：** `src/wentian/agents/runner.py`、`tests/test_agents_runner.py`
**依赖：** T136（AgentDef/AgentType）、T138（filter.resolve_allowed_tools）、T140（AgentsConfig.model_aliases）
**RED：**
1. 写测试（**假 provider** 端到端）：构造假 provider（返固定文本 `"分析完毕"`，无 tool_calls）、构造一个 `AgentDef(name="analyst", …, model="inherit")`；调 `run_subagent(agent_def, "帮我分析", base_provider_cfg=fake_cfg, provider_factory=fake_factory, …, depth=0)` → 返回 `SubAgentResult(text="分析完毕", usage={…}, stop_reason="COMPLETED")`；**不联网**。
2. 写测试（**model 覆盖生效**）：`AgentDef(model="haiku")` → runner 调 `dataclasses.replace(base_cfg, model="claude-haiku-4-5")`（model_aliases 映射），断言 fake_factory 收到的 cfg.model 正确；`model="inherit"` → cfg.model 不变（取父 model）。
3. 写测试（**别名不可解析**）：`AgentDef(model="unknown-alias")` → `run_subagent` 返回结构化报错 `SubAgentResult(text="别名 unknown-alias 不可解析…", stop_reason="ERROR")`，不空起 AgentLoop。
4. 写测试（**fork 式继承历史**）：`parent_messages=[{"role":"user","content":"你好"}]` + `AgentType.FORK` → 子 AgentLoop 的起始消息含父历史（断言传入 loop 的 messages 前缀匹配）；子允许集不含 `"Agent"` 工具。
5. 写测试（**错误停机转结构化**）：假 provider 让 AgentLoop 抛 MAX_ROUNDS（注入 `max_rounds=1` 且 provider 每轮返 tool_call）→ `run_subagent` 返回 `SubAgentResult(stop_reason="MAX_ROUNDS", text="因 MAX_ROUNDS 停止…")`，不崩主流程。
6. 写测试（**状态隔离**）：串行调用两次 `run_subagent`，各用独立假 provider 实例 → 各自结果互不干扰（无共享可变状态）。
7. 跑测试确认失败（模块不存在）。
**GREEN：** 实现 `runner.py`：`SubAgentResult(text, usage, stop_reason)` dataclass；`run_subagent` 函数：① 映射 model 别名（不可解析 → 提前返报错）；② 造全新 provider 实例（`dataclasses.replace(base_cfg, model=mapped_model)`+`provider_factory`）；③ 建独立 `Mode`/独立 `permission_gate` / `resolve_allowed_tools`（全局禁 `Agent`）；④ 装配独立 `AgentLoop`（注入独立 provider/executor/pipeline/max_turns）；⑤ 在 worker 线程 `asyncio.run(loop.run(messages, system))` 跑到底；⑥ 取末条助手正文 → `SubAgentResult`；⑦ 捕获所有异常 → 转结构化结果（软化，N54）。
**REFACTOR：** model 映射、loop 装配、结果抽取各抽私有函数；docstring 标注 `v0.13 · C112 · F94/F95/F96/F100`；保持绿。
**验证：** `uv run pytest tests/test_agents_runner.py -q` 全绿（全程假 provider，不联网）。

---

## 波次三 · 后台管理器 + 工具

## T142: C113 `agents/manager.py` — BackgroundTaskManager（F98/F99/N53/AC123/AC124）

**文件：** `src/wentian/agents/manager.py`、`tests/test_agents_manager.py`
**依赖：** T141（runner.run_subagent/SubAgentResult）
**RED：**
1. 写测试（**三种进后台**）：① 显式：`manager.submit(agent_def, "任务", background=True)` 立即返回 task_id、不阻塞；② 超时自动（**可注入时钟**）：前台运行超过 `foreground_timeout_s` → manager 把任务转后台（用假时钟注入推进超时）；③ 手动切：`manager.push_to_background(task_id)` 把运行中前台任务推后台（工具返「已转后台」）。
2. 写测试（**Fork 恒后台**）：`AgentType.FORK` 类型 `submit(…)` → 无论 `background` 参数为何值，任务均走后台（断言立即返回 id、不阻塞）。
3. 写测试（**status/result/usage 记录**）：submit 后 `get(id).status == TaskStatus.RUNNING`；假 runner 完成后 `get(id).status == TaskStatus.DONE`、`get(id).result == "分析完毕"`；失败后 `status == TaskStatus.FAILED`、result 含错误信息。
4. 写测试（**drain_completions 回灌**）：两个后台任务完成后 `drain_completions()` 返回含两段「id=X 已完成：结果」的字符串，并清空回灌缓冲；再次调用返回空串（缓冲已清）。
5. 写测试（**线程不泄漏**）：`submit` 后 `close()` 短 join（1s 内完成）→ `threading.active_count()` 不增（daemon 线程退出干净）；测试结束无线程残留。
6. 写测试（**list**）：`list()` 返回当前全部任务列表（含运行中+完成+失败），按创建时间排序；空时返回 `[]`。
7. 跑测试确认失败（模块不存在）。
**GREEN：** 实现 `BackgroundTaskManager`：`_tasks: dict[str, BackgroundTask]`、`_completion_buffer: list[str]`、`_lock: threading.Lock`；`submit` 分 fork/explicit/timeout 三路进后台（daemon 线程 `threading.Thread(daemon=True)` 跑 `run_subagent`，完成写回 status/result/usage + 追加回灌缓冲）；`drain_completions` 加锁取走缓冲并清空；`close` 对所有线程短 `join(timeout=…)`；可注入 `clock=time.time`（测试替换）。
**REFACTOR：** daemon 线程包装、回灌格式化抽私有函数；docstring 标注 `v0.13 · C113 · F98/F99`；保持绿。
**验证：** `uv run pytest tests/test_agents_manager.py -q` 全绿（全程假 runner，不联网）。

---

## T143: C114 `agents/tool.py` — AgentTool 分流 + 嵌套拦截（F91/F94/F95/F97/N52/AC116/AC119/AC120/AC122）

**文件：** `src/wentian/agents/tool.py`、`tests/test_agents_tool.py`
**依赖：** T141（runner）、T142（manager）、T138（filter）
**RED：**
1. 写测试（**schema**）：`AgentTool(…).name == "Agent"`；`spec().parameters` 含 `type`（必填，enum definition/fork）、`agent_type`（可选）、`prompt`（必填）、`background`（可选 bool）；`category == Category.COMMAND_EXEC`。
2. 写测试（**definition 前台路径**）：注入**假 runner**（返 `SubAgentResult(text="结果", stop_reason="COMPLETED")`）和假 manager（不调用）；`tool.run({"type":"definition","agent_type":"coder","prompt":"帮我写"})` → 返回 `"结果"`；假 runner 被调用一次、假 manager 未被调用。
3. 写测试（**background 路径**）：`tool.run({"type":"definition","agent_type":"coder","prompt":"写","background":True})` → 假 manager `submit` 被调用、返回 `"任务 id=X 已起"` 类字符串；假 runner 未被同步调用。
4. 写测试（**fork 路径恒后台**）：`tool.run({"type":"fork","prompt":"继续"})` → manager `submit` 被调用（AgentType.FORK）、立即返回「已起」；runner 未被同步调用。
5. 写测试（**嵌套拦截**）：`tool.run({…}, depth=1)` → 直接返回错误文本「子 Agent 禁止嵌套调用 Agent 工具」，**不调**runner/manager。
6. 写测试（**无角色优雅报错**）：`agent_type="不存在的角色"` + registry 无此名 → 返回错误文本「无此角色」，不崩（N50）。
7. 跑测试确认失败（模块不存在）。
**GREEN：** 实现 `AgentTool(Tool)`：鸭子持有 `registry`/`runner`/`manager`/`cfg: AgentsConfig`；`run(args, *, depth=0)`：① 深度≥1 → 直接返报错；② 按 `args["type"]` 分流——`definition`：从 registry 取角色（无则报错）、按 `background` 或 `foreground_timeout_s` 决定同步 runner 还是 manager.submit；`fork`：恒走 manager.submit（AgentType.FORK）；③ 前台同步返 `SubAgentResult.text`；后台返「任务 id=X 已起」。
**REFACTOR：** 分流逻辑抽 `_run_definition`/`_run_fork` 私有方法；docstring 标注 `v0.13 · C114 · F91/F94/F95`；保持绿。
**验证：** `uv run pytest tests/test_agents_tool.py -q` 全绿。

---

## 波次四 · 装配 + 命令 + hook（串行）

## T144: C116 `/agents` 命令（builtins）（F101/AC126）

**文件：** `src/wentian/commands/builtins.py`、`tests/test_commands_builtins.py`（续）
**依赖：** T142（BackgroundTaskManager）
**RED：**
1. 写测试（用**假 manager** 注入）：假 manager 含三条任务（1 个 RUNNING、1 个 DONE、1 个 FAILED）；调用 `/agents`（无参）→ 输出含三条任务的 id/label/status 及 DONE 任务的 token 用量。
2. 写测试：`/agents <done_id>` → 输出含该任务的 result 全文（完整正文，不截断）+ 用量。
3. 写测试：`/agents <running_id>` → 输出提示「任务运行中，result 尚不可用」（不崩）。
4. 写测试：`/agents 不存在的id` → 输出「未找到任务」错误（不崩）。
5. 写测试：`manager=None`（无 agents 配置回退态）→ 命令输出友好提示「agents 未启用」（不崩，N50）。
6. 跑测试确认失败（`/agents` 命令不存在）。
**GREEN：** 在 `build_builtin_registry` 中注册 `/agents`（LOCAL 类）；handler 持鸭子 `manager` 句柄（None 守卫）；无参 → `manager.list()` 格式化输出；有参 → `manager.get(id)` 查询输出 result；None → 友好提示。
**REFACTOR：** 输出格式化抽 `_format_task`/`_format_task_detail` 小函数；docstring 标注 `v0.13 · C116 · F101`；保持绿。
**验证：** `uv run pytest tests/test_commands_builtins.py -q` 全绿（含原有命令回归）。

---

## T145: C117 `cli.build_app` + `repl` 装配接线（F91/F93/F98/F99/N50/AC116/AC121/AC124/AC128）

**文件：** `src/wentian/cli.py`、`src/wentian/repl.py`、`tests/test_cli.py`（续）、`tests/test_repl.py`（续）
**依赖：** T139（discover_agents）、T140（AgentsConfig）、T141（runner）、T142（manager）、T143（AgentTool）、T144（/agents 命令）
**RED：**
1. 写测试（**装配接线**）：`build_app` 后工具注册表含 `"Agent"` 工具（+1，N50 有意新默认）；命令注册表含 `/agents`；REPL 持有 `manager` 句柄（非 None，当 `agents` 配置默认开启时）。
2. 写测试（**drain_completions 接入 request_decorator**）：构造 REPL + 假 manager（`drain_completions` 返回 `"id=1 已完成：做好了"`）；运行一轮对话 → 该轮 `request_decorator` 输出的 messages 含 `<system-reminder>` 包裹的回灌文本；原 messages 列表不被 mutate；回灌内容不进持久化（session 存储不含）。
3. 写测试（**无 `agents` 配置回退**）：`config.agents.enabled=False` → `manager=None`、`registry=None`；工具注册表仍含 `"Agent"` 工具（N50：fork-only 可用）；`/agents` 命令返「agents 未启用」；`Agent` 工具 `type=definition` 且无角色 → 优雅返「无此角色」（不崩）。
4. 写测试（**AgentTool 注入 registry/manager/runner/cfg**）：断言 AgentTool 持有的 registry、manager、cfg 与 build_app 装配的一致（鸭子注入正确，不硬依赖 REPL 具体类）。
5. 跑测试确认失败（装配不存在 / 接线缺失）。
**GREEN：** `cli.build_app` 中：`discover_agents(project_dir, user_dir, builtin=…)→registry`；`BackgroundTaskManager(runner=…, cfg=cfg.agents)`；`AgentTool(registry, runner_fn, manager, cfg.agents)` 注册进工具注册表；注册 `/agents` 命令（注入 manager）；把 `manager.drain_completions` 接入 `request_decorator` 链（在既有 hooks 注入后追加）；`agents.enabled=False` ⇒ manager=None 但 AgentTool 仍注册。`repl.py` 暴露 `manager` 句柄。
**REFACTOR：** 装配编排抽 `_assemble_agents` 函数；docstring 标注 `v0.13 · C117 · F91/F93/F98/F99`；保持绿。
**验证：** `uv run pytest tests/test_cli.py tests/test_repl.py -q` 全绿。

---

## T146: C117/F102 `SubAgentAction` 接通 HookEngine（F102/N54/AC127）

**文件：** `src/wentian/hooks/actions.py`、`tests/test_hooks_actions.py`（续）
**依赖：** T142（BackgroundTaskManager）、T145（manager 装配就位）
**RED：**
1. 写测试（**SubAgentAction 真起后台**）：构造 `SubAgentAction(agent_type="coder", prompt="帮我审代码")` + 注入**假 manager**（记录 `submit` 调用）；触发 `run_subagent_action(action, ctx, manager=fake_manager)` → 假 manager 的 `submit` 被调用、`kind=AgentType.DEFINITION`、`background=True`（fire-and-forget）；函数立即返回（不阻塞）。
2. 写测试（**结果回灌路径**）：假 manager `submit` 返回 `id="bg-1"` → action 返回含 `"id=bg-1"` 的结构化结果（供引擎日志用）；后续通过 manager `drain_completions` 回灌（此路径在 T145 已覆盖，这里仅断言 action 侧不重复写消息）。
3. 写测试（**失败软化**）：假 manager `submit` 抛 `RuntimeError` → `run_subagent_action` 不冒泡、记日志（v0.12 软化铁律）、返回含「失败」的结构化结果；`HookEngine.fire` 不因此崩主流程。
4. 写测试（**manager=None 回退**）：`manager=None` 时 `run_subagent_action` 记「subagent 未启用」日志、返回占位结果（不抛，向后兼容 v0.12 占位行为）。
5. 跑测试确认失败（`SubAgentAction` 仍为占位不执行真起）。
**GREEN：** 改 `hooks/actions.py`：`run_subagent_action(action, ctx, manager)` 取代原「未实现」占位——`manager is None` → 日志占位返回；`manager is not None` → `manager.submit(AgentDef_or_inline, prompt, background=True)` fire-and-forget；全包 try/except 软化；HookEngine 的 `SubAgentAction` 分支调此函数（传入装配时注入的 manager 句柄）。
**REFACTOR：** 软化包装复用既有 `_safe_run` 模式；docstring 标注 `v0.13 · C117/F102`；保持绿。
**验证：** `uv run pytest tests/test_hooks_actions.py -q` 全绿（含 v0.12 原有四动作回归）。

---

## 波次五 · 收口

## T147: 全量回归 + 分层 import 断言 + 无配置冒烟 + ruff 收口（N49/N50/N51/N52/N55/N56/AC116/AC128）

**文件：** 全仓、`tests/test_layering.py`（续）、`tests/test_smoke.py`（续）
**依赖：** T136–T146（全部）
**步骤（非 TDD，验证收口）：**
1. `uv run pytest -q` → v0.1–v0.12 全部 + v0.13 新增全绿、无告警（基线 1509 测试 → +N）。
2. **分层 import 断言**（`tests/test_layering.py` 续，grep/ast 取证，N51）：
   - `agents/spec.py`、`agents/filter.py`：零 `rich`/`prompt_toolkit`/后端 SDK/`repl`/`cli`/`providers` import（`filter` 只依 `spec`/`decision.Category`）。
   - `agents/loader.py`：只依 `frontmatter`/`spec` + stdlib，零 `repl`/`cli` import。
   - `agents/runner.py`：零 `repl`/`cli` import（只 import `agent.loop`/`providers`/`permissions`）。
   - `agents/manager.py`：零 `repl`/`cli` import。
   - `agents/tool.py`：**鸭子注入**句柄，无硬依赖 `repl` 具体类。
   - `frontmatter.py`：顶层叶子，零业务 import（只 stdlib）。
3. `ruff format --check .` 通过、`ruff check .` 无告警（N56）。
4. **无 `agents` 配置冒烟**（N50）：`printf '/agents\n/exit\n' | uv run wentian`（空 cwd、无 agents 配置）→ 退出码 0、无 traceback；`Agent` 工具在工具列表；`/agents` 输出「agents 未启用」；`type=fork` 路径照常（无角色不崩）。
5. **嵌套防护现场证据**（N52）：构建测试断言 `AgentTool(depth=1).run({…})` 返回拦截错误文本；`test_agents_filter.py` 中确认 `Agent` 工具永不在任何子 Agent 允许集（全局 `globally_forbidden` 断言）。
6. **全量 v0.11 skills 回归**：`uv run pytest tests/test_skills_loader.py tests/test_skill_tool.py tests/test_skill_activator.py -q` 全绿（AC118：共享 frontmatter 原语抽取后 skills 行为不变）。
7. **v0.12 hooks 回归**：`uv run pytest tests/test_hooks_actions.py tests/test_hooks_engine.py -q` 全绿（SubAgentAction 接通后 v0.12 软化行为不变）。
8. checklist 离线项逐条取证；🌐👁 端到端（AC129：真 API key 真起子 Agent/后台 Fork/嵌套被拦/上下文隔离）单列、不阻塞离线验收。
**验证：** `grep -c 'T13[6-9]\|T14[0-7]' spec/task.md` 显示 12 个任务全在；`uv run pytest -q` 全绿；ruff 双过；无配置冒烟退出码 0。

---

## v0.13 执行顺序

```
波次一（叶子原语，T136∥T137 可并行；T138 依 T136）：
  T136（agents/spec.py，无依赖）
  T137（frontmatter.py 共享原语抽取，无依赖；触 v0.11 skills，需 skills 零回归）
  → T138（agents/filter.py，依 T136）

波次二（加载 + 配置并行 → 执行串行）：
  T139（agents/loader.py，依 T136+T137）
  T140（config.py AgentsConfig，无依赖，可与 T139 并行）
  → T141（agents/runner.py，依 T136+T138+T140）

波次三（后台 + 工具，串行）：
  T141 → T142（agents/manager.py，依 T141）
         T142 → T143（agents/tool.py，依 T141+T142+T138）

波次四（装配 + 命令 + hook，串行）：
  T142 → T144（/agents 命令，依 T142）
  T139+T140+T141+T142+T143+T144 → T145（cli/repl 装配，依全波次一二三+T144）
  T142+T145 → T146（SubAgentAction 接通，依 T142+T145）

波次五（收口）：
  → T147（全量回归 + 分层断言 + 冒烟 + ruff，依 T136–T146 全部）
```

关键串行约束：
- `T137`（共享原语）必须先于 `T139`（agents/loader 调 `parse_frontmatter`）；
- `T141`（runner）必须先于 `T142`（manager 调 runner）；
- `T145`（cli 装配）是最后集成点，依全部组件就绪；
- `T147`（收口）依全部，最后串行。
