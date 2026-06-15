# WentianCode（文天）Plan（v0.1 + v0.2）

> 基于已批准的 `spec.md`。v0.1 部分（F1–F12）已实现并验收；文末为 v0.2（F13–F18）新增设计。本文档定义"怎么做"：架构、接口、数据结构、模块交互与测试策略。
> SDK 事实来源：`claude-api` skill（2026-06-10 确认）——`claude-opus-4-8` 为精确 model id；adaptive thinking 需 `thinking={"type":"adaptive","display":"summarized"}` 才返回思考文本（默认 omitted 为空）；流式用 `client.messages.stream()`；Opus 4.8 不接受 `temperature/top_p/top_k` 与 `budget_tokens`。

## 架构概览

五个相互解耦的层（满足 N5），依赖方向自上而下单向：

```
cli.py（入口/装配）
   │ 读配置、选 provider、建/载会话、启动 REPL
   ▼
repl.py（界面层：REPL 循环 + 斜杠命令）──► render.py（渲染层：流式 Markdown / thinking 展示）
   │ 把用户输入交给会话，把事件流交给渲染
   ▼
session.py（会话层：消息历史 + 持久化）
   │ 携带历史调用 provider
   ▼
providers/（后端层：统一接口 + anthropic / openai_compat 两实现 + factory）
   ▲
config.py（配置层：YAML 解析、校验、provider 选择）──被 cli.py 与 factory 使用
```

- **F11 的落点**：`providers/base.py` 定义抽象接口与统一事件类型；repl/session/render 只依赖 base，不 import 具体实现。新增后端 = 新增一个 providers 子模块 + factory 注册一行。
- **F12 的落点**：渲染独立成 `render.py`，REPL 不直接操作 Rich 细节；FakeProvider + Rich 录制模式可离线测渲染。

## 核心数据结构

### 统一流式事件（providers/base.py）

```python
@dataclass
class ThinkingDelta:
    text: str          # 思考增量文本

@dataclass
class TextDelta:
    text: str          # 正文增量文本

@dataclass
class Done:
    usage: Usage | None   # 输入/输出 token 数（拿不到则 None）

StreamEvent = ThinkingDelta | TextDelta | Done
```

### Provider 抽象接口（providers/base.py）

```python
class Provider(ABC):
    name: str          # 配置里的 provider 名，供 /provider 显示

    @abstractmethod
    def stream(self, messages: list[Message], *, system: str | None = None
               ) -> Iterator[StreamEvent]:
        """携带完整历史发起一次流式请求，按序产出事件，最后必产出 Done。"""
```

`Message = {"role": "user" | "assistant", "content": str}`（与会话层共用的纯字典，不引 SDK 类型）。

### 配置（config.py）

```python
@dataclass
class ProviderConfig:
    name: str
    protocol: Literal["anthropic", "openai"]
    model: str
    api_key: str
    base_url: str | None = None      # anthropic 官方可省略
    thinking: bool = False           # 仅 anthropic 协议有效

@dataclass
class Config:
    providers: dict[str, ProviderConfig]
    default: str                     # 必须是 providers 的 key
```

校验失败（缺字段、default 不存在、protocol 非法）抛 `ConfigError`，cli 捕获后友好报错退出。

### 会话（session.py）

```python
@dataclass
class Session:
    id: str                          # 形如 20260610-143052-a1b2（时间戳+短随机）
    created_at: str                  # ISO 8601
    updated_at: str
    provider: str                    # 最近使用的 provider 名
    messages: list[Message]
```

持久化为 JSON：`~/.local/share/wentian/sessions/<id>.json`（XDG，N4）。每轮 assistant 回复完成后整体覆写落盘（F9）。`SessionStore` 提供 `create() / load(id) / load_latest() / list() / save(session)`；`list()` 返回 (id, updated_at, 首条用户消息摘要) 供 `/sessions` 展示。

## 模块设计

### config.py
- **职责**：读 `~/.config/wentian/config.yaml`（路径可注入便于测试），解析为 `Config`，校验，按名取 `ProviderConfig`。
- **对外**：`load_config(path=None) -> Config`、`Config.get(name=None) -> ProviderConfig`（None → default）。
- **依赖**：pyyaml。不依赖任何其他自有模块。

### providers/anthropic.py
- **职责**：`AnthropicProvider(ProviderConfig)`。用 `anthropic.Anthropic(api_key=..., base_url=...)`；`client.messages.stream(model=..., max_tokens=64000, messages=..., system=...)`；配置 `thinking: true` 时加 `thinking={"type": "adaptive", "display": "summarized"}`（不开则**省略** thinking 参数）。
- **事件映射**：迭代 stream 事件，`content_block_delta` 的 `thinking_delta` → `ThinkingDelta`，`text_delta` → `TextDelta`；结束后 `get_final_message().usage` → `Done`。
- **不发送** `temperature/top_p/top_k/budget_tokens`（Opus 4.8 会 400）。

### providers/openai_compat.py
- **职责**：`OpenAICompatProvider(ProviderConfig)`。用 `openai.OpenAI(api_key=..., base_url=...)`；`chat.completions.create(model=..., messages=..., stream=True)`。
- **事件映射**：`delta.content` → `TextDelta`；`delta.reasoning_content`（DeepSeek 等国产模型的思考字段，用 `getattr` 容错）→ `ThinkingDelta`；流尽 → `Done(usage=None)`（chunk 带 usage 则取）。system 消息以 `{"role":"system"}` 注入消息列表首位。

### providers/factory.py
- **职责**：`create_provider(cfg: ProviderConfig) -> Provider`，按 `protocol` 字段分发。注册表为模块级 dict，新协议加一行（F11）。

### session.py
- **职责**：见数据结构。原子写（先写临时文件再 rename），损坏的 JSON 文件在 `list()` 时跳过并警告。

### render.py
- **职责**：把事件流变成终端输出，是 F3/F8/F12 的唯一实现点。
- **对外**：`Renderer(console: Console)`，方法 `render_stream(events: Iterator[StreamEvent]) -> str`（返回累积的正文文本，供会话层入史）。
- **thinking 展示（F8）**：首个 `ThinkingDelta` 先打印 `🤔 思考中…` 前缀，之后以暗色斜体逐字 print（纯文本，不进 Markdown）。
- **Markdown 流式（F12）**：正文累积进缓冲；用 `rich.live.Live`（`transient=True`，约 10 fps 节流）实时重渲 `Markdown(buffer)` 实现"边流边渲"；流结束后关闭 Live，向 scrollback 完整 `console.print(Markdown(full_text))` 一次定格。已知取舍：超过终端高度的内容在 Live 阶段只展示尾部视口，最终定格输出完整——满足 AC10 的"逐步出现 + 最终渲染"。
- **可测性**：Console 可注入（`Console(record=True, width=80)`），离线断言输出含格式化特征、不含裸 ``` 围栏。

### repl.py
- **职责**：REPL 主循环（F1/F2/F10）。提示符读入 → 斜杠命令分发或对话一轮：append 用户消息 → `provider.stream(...)` → `Renderer.render_stream` → append assistant 消息 → `store.save`。
- **斜杠命令**：`/help` `/new` `/sessions` `/resume <id>` `/provider <名>` `/exit`，实现为 `dict[str, handler]`。`/provider` 通过 factory 重建当前 provider 并记入 session。
- **错误处理**：provider 抛出的 SDK 异常（鉴权/网络/限流）捕获后打印一行红色错误，REPL 不退出，本轮用户消息从历史回滚（不入史、不落盘）。
- **依赖注入**：构造时接收 `provider, session, store, renderer, input_fn=input`，全部可替换 → 离线可测（AC9）。

### cli.py
- **职责**：typer 入口。`wentian [--provider 名] [--continue] [--resume <id>]`；装配 config → provider → session（默认新建 / continue 载最近 / resume 载指定）→ Renderer → REPL.run()。`wt` 为别名入口（pyproject `[project.scripts]` 两条指向同一函数）。

## 模块交互（一轮对话的数据流）

```
用户输入 "你好"
  → repl: 非斜杠命令 → session.messages.append(user)
  → repl: provider.stream(session.messages, system=人格提示)
  → provider: SDK 流式请求，yield ThinkingDelta*/TextDelta*/Done
  → renderer: thinking 暗色斜体直出；text 进 Live-Markdown 缓冲；Done 后定格输出，返回全文
  → repl: session.messages.append(assistant=全文) → store.save(session)
  → 回到提示符
```

`/provider deepseek`：config.get("deepseek") → factory.create_provider → 替换 repl 持有的 provider → session.provider 更新并落盘。

## 测试策略

> 原则：核心逻辑全部离线可测（AC9）。SDK 调用层薄到只剩"参数组装 + 事件映射"，用 mock SDK 客户端测映射；真实联网仅留给 checklist 的端到端人工场景。

| 模块 | 测法 | 关键用例 |
|------|------|----------|
| config | 单测，tmp_path 写 YAML | 正常解析；缺字段/坏 protocol/default 不存在 → ConfigError；default 选中；按名选中 |
| providers/base + FakeProvider | 单测 | FakeProvider(预设事件列表) 按序产出、以 Done 结尾——它同时是 AC8 的"假后端" |
| providers/anthropic | 单测，mock anthropic SDK stream | thinking 开/关时请求参数差异（adaptive+summarized / 无 thinking 字段）；thinking_delta/text_delta → 统一事件；不含 temperature 等禁用参数 |
| providers/openai_compat | 单测，mock openai SDK | delta.content / reasoning_content 映射；system 注入首位；base_url 透传 |
| factory | 单测 | protocol→类 分发；未知 protocol 报错 |
| session | 单测，tmp_path 当数据目录 | create/save/load 往返相等；load_latest 取 updated_at 最新；坏 JSON 跳过；原子写 |
| render | 单测，Console(record=True) | thinking 与正文输出可区分（含 🤔 前缀）；含 # 列表 ``` 的输入最终输出无裸围栏且有渲染特征；返回值=累积正文 |
| repl | 单测，FakeProvider + 注入 input_fn | 一轮对话后历史含 user+assistant 且已落盘（AC3 离线版）；每条斜杠命令行为；provider 异常时不崩、消息回滚 |
| cli | 单测，typer CliRunner + monkeypatch 路径 | --provider 覆盖 default；--continue/--resume 装配正确的 session |
| 端到端（真实联网） | checklist 人工场景 | 真实 Anthropic + 一个 openai 兼容后端各跑一轮（AC2/4/5） |

## 文件组织

```
2026-06-09-WentianCode/
├── spec/                      # 四文档（已有）
├── pyproject.toml             # uv 管理；scripts: wentian, wt
├── src/wentian/
│   ├── __init__.py
│   ├── cli.py
│   ├── config.py
│   ├── session.py
│   ├── render.py
│   ├── repl.py
│   └── providers/
│       ├── __init__.py
│       ├── base.py            # Provider ABC + StreamEvent + Message
│       ├── anthropic.py
│       ├── openai_compat.py
│       └── factory.py
└── tests/
    ├── conftest.py            # FakeProvider、tmp 配置/数据目录 fixtures
    ├── test_config.py
    ├── test_providers_base.py
    ├── test_provider_anthropic.py
    ├── test_provider_openai_compat.py
    ├── test_factory.py
    ├── test_session.py
    ├── test_render.py
    ├── test_repl.py
    └── test_cli.py
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 事件模型 | 三种 dataclass 联合（Thinking/Text/Done） | 最小够用；界面层与协议解耦（F11）；FakeProvider 易造 |
| anthropic 流式 | `messages.stream()` 高层 helper | 官方推荐，自带累积与 `get_final_message()`，比裸 `stream=True` 少手写状态 |
| thinking 参数 | `{"type":"adaptive","display":"summarized"}`，关闭时省略字段 | Opus 4.8 上 budget_tokens 已 400；display 默认 omitted 会拿到空思考文本——必须显式 summarized |
| max_tokens | 64000 | 流式下官方推荐档位，长答不截断 |
| Markdown 流式策略 | Live(transient) 实时重渲 + 结束后整体定格打印 | Rich Live 是成熟方案；transient+定格规避 Live 超屏截断污染 scrollback 的已知坑；满足 AC10 双要求 |
| thinking 不进 Markdown | 纯文本暗色斜体直出 | spec F12 明文约定；思考是过程性文字，渲染反而割裂 |
| 持久化格式 | 单会话单 JSON 文件 + 原子写 | 简单可 grep；v0.1 无并发场景；原子写防半截文件 |
| 失败轮回滚 | 请求异常时该轮 user 消息不入史 | 避免"历史里有问没答"污染下一轮上下文 |
| openai 思考字段 | `getattr(delta, "reasoning_content", None)` | DeepSeek 等的扩展字段，官方 SDK 类型外，容错取 |
| 依赖注入贯穿 | console/input_fn/路径/provider 全可注入 | N5 + AC9 的离线测试前提 |

---

# v0.2 新增设计（F13–F18：Claude Code 式交互）

> 技术方向：prompt_toolkit + Rich，滚动式终端。新增依赖仅 `prompt_toolkit>=3.0`。版本升 `0.2.0`。

## 架构增量

新增 `src/wentian/ui/` 包承载全部 prompt_toolkit 代码，分层不破：

```
cli.py ──► ui/banner.py, ui/select.py（启动期一次性 UI）
repl.py ──► ui/input.py（经已有 input_fn 注入点）, ui/interrupt.py（新 InterruptListener 协议）
render.py ──► ui/spinner.py（rich 实现）+ 内部 _StreamPump（可中断流消费）
```

- `ui/` 只依赖 prompt_toolkit + rich + stdlib，零 SDK import。
- repl/render 不直接 import prompt_toolkit：repl 经 `input_fn` 与 `InterruptListener` 协议接触 UI。
- 不引入 questionary：选择器手写 ~60 行 prompt_toolkit mini-Application——questionary 不暴露 input/output 注入点，离线测试别扭；手写可直接注入 `PipeInput/DummyOutput`。

## 组件设计（C1–C7）

### C1 横幅 `ui/banner.py`（2026-06-11 改版：对齐 Claude Code 布局）
`build_banner(*, version, provider_name, model, session_id, resumed) -> RenderableType`（纯函数）。布局 = `Table.grid` 两列：左列像素小人 icon，右列三行信息（`文天 WentianCode v{version}` / `{provider} · {model}` / `新会话|已恢复 {id}`）。**无 Panel 边框**。
像素 icon：模块级像素栅格（int 矩阵）+ `_render_pixels()` 用半块字符 `▀`（fg=上像素色、bg=下像素色）把 2 行像素压进 1 行字符，输出 rich Text。形象：戴墨色方巾的朱砂色像素小人（双目 + 侧仪 + 双足），与 Claude 像素脸同语言但自有身份。颜色用 truecolor hex；非 TTY 管道下 rich 自动降级/剥色，结构字符仍可断言。
- `wentian/__init__.py` 加 `__version__ = "0.2.0"`。
- Provider ABC 增可选属性 `model: str = ""`，两个具体 provider 在 `__init__` 设 `self.model = cfg.model`（横幅/状态栏读取，getattr 兜底）。

### C2 REPL 状态源 `repl.py`
`REPL.status_line() -> str`：`{provider}:{model} │ 会话 {session.id} │ N 条消息`。读 live 状态——bottom_toolbar 是每次 prompt 重算的 callable，`/provider` `/new` `/resume` 后下一次渲染自动反映，无需通知机制。

### C3 多行输入框 + 状态栏 `ui/input.py`
```python
class PromptInput:           # callable，匹配 REPL input_fn 签名 (prompt:str)->str
    def __init__(self, *, history_path: Path | None = None,
                 status_provider: Callable[[], str] | None = None,
                 input=None, output=None): ...
def default_history_path() -> Path   # $XDG_STATE_HOME/wentian/history
```
- `PromptSession(multiline=True, history=FileHistory(...), bottom_toolbar=…, key_bindings=kb, prompt_continuation="│ ")`。
- 按键：Enter（`\r`）提交；**Ctrl+J（`\n`）与 Alt+Enter 均插入换行**（prompt_toolkit 区分 ControlM/ControlJ）；↑ 在首行=翻历史后退、↓ 在末行=前进（条件绑定）。
- 视觉：调用时打印顶线 `╭──`，prompt `│ ❯ `，continuation `│  `，提交后 `╰──`（开口框；不用 Frame 以保留 PromptSession 的 history/toolbar 免费集成）。
- `status_provider` 后置赋值解决 REPL↔PromptInput 构造环。EOF/Ctrl+D → EOFError（与现有退出路径兼容）。

### C4 Provider 选择器 `ui/select.py`
```python
def select_provider(names: list[str], default: str, *, input=None, output=None) -> str
```
inline `Application`（非全屏，`erase_when_done=True`）：`FormattedTextControl` 渲染 `  ❯ name (默认)` 列表，↑↓ 移动、Enter 确认、Ctrl+C 返回 default（裸 Esc 取消已弃：与方向键转义序列前缀冲突，见 select.py 模块说明）。
cli 接线：`build_app` 增注入参数 `provider_selector`；仅 **多 provider 且无 -p** 时调用；typer main 在 TTY 时传真实实现。

### C5 计时 spinner + RenderResult `ui/spinner.py` + `render.py`
```python
class WaitingSpinner:
    FRAMES = "✻✺✹✸✷✶"
    def __init__(self, console, *, clock=time.monotonic): ...
    def start(self): ...   # 记 t0；TTY 开 Live(get_renderable=self._render, transient=True)
    def stop(self): ...    # 幂等
    def render_line(self) -> Text   # "✻ 构思中… (5s)"
```
- **秒数自动跳动**：`Live(get_renderable=…)` 的内部 refresh 线程周期调用 renderable——主线程阻塞网络读时秒数照常更新，无需自建计时线程。
- **流式期间保留计时**（对齐参考）：正文 Live 用 `Group(Markdown(buffer), 计时行)` 作 renderable；定格时只打印 Markdown。thinking 期间不显示计时行（thinking 文本即活跃信号）。
- `render_stream` 返回 `RenderResult(text: str, interrupted: bool)`（frozen dataclass）替代裸 str；repl 改用 `result.text`。既有 ~20-30 处断言机械更新。
- Live 互斥：spinner Live ↔ 正文 Live ↔ thinking stop-print-reopen，先 stop 再 open，单一 `_active_live` 句柄管理。

### C6 Esc/Ctrl+C 中断 `ui/interrupt.py` + `render.py`
```python
class InterruptListener(Protocol):
    def __enter__(self) -> threading.Event | None: ...   # arm；None=不监听（直接迭代路径）
    def __exit__(self, *exc) -> None: ...          # disarm
class NullListener: ...   # 默认/非 TTY：__enter__ 返回 None → render 走直接迭代路径
class EscListener:
    def __init__(self, fd: int | None = None): ...  # 默认 stdin，可注入 pty 测试
```
- `EscListener.__enter__`：保存 termios → `tty.setcbreak`（关 ICANON+ECHO、**保留 ISIG**，Ctrl+C 仍走 SIGINT）→ daemon 线程 `select([fd],…,0.1)` 轮询：读到 `\x1b` 后 50ms 内无后续字节 → 裸 Esc → set event；有后续字节（方向键等转义序列）读掉丢弃。
- `__exit__`：停线程 join(0.3) → `tcflush` 排残键 → 还原 termios。cbreak 窗口与 prompt_toolkit raw 窗口严格不重叠（prompt 返回后才 arm，生成结束即 disarm）。
- `_StreamPump`（render.py 私有）：泵线程消费 provider 生成器入 `queue.Queue`；主线程 `get(timeout=0.1)` 循环中查 `interrupt.is_set()`——**首 token 前（网络阻塞）与流中期都能响应**；`("error", exc)` 回主线程 re-raise，REPL 现有回滚机制不变。仅 `interrupt is not None` 时启用 pump；非 TTY/测试路径保持直接迭代。
- 中断语义：有部分正文 → 保留屏显 + partial 入史落盘 + dim 打印 `⎿ 已中断`（不写入消息 content）；零正文 → 回滚 user 消息不落盘（与错误路径同构）。流中 Ctrl+C ≡ Esc（renderer 内 `except KeyboardInterrupt` 统一转 interrupted 返回）。
- REPL 构造增 `interrupt_listener: InterruptListener = NullListener()`；`_chat_once` 用 `with self._interrupt_listener as ev:` 包住 stream+render。

### C7 装配 `cli.py` + `pyproject.toml`
- `build_app` 新关键字参数：`provider_selector / input_fn / history_path / show_banner=True`。
- 装配顺序：config → selector? → provider → store → session → renderer → **banner** → PromptInput（TTY 且未注入 input_fn）→ EscListener（TTY）→ REPL。
- pyproject：dependencies 加 `prompt_toolkit`；version `0.2.0`。

## 测试策略（v0.2 增量，全部离线）

| 组件 | 测法 | 关键用例 |
|------|------|----------|
| banner | 纯函数 + Console(record=True) | 含版本/provider/模型/圆角框字符；resumed 两分支文案 |
| status_line | 构造 REPL 调命令 handler | /new、/provider 后字符串变化；消息数随对话增长 |
| PromptInput | `create_pipe_input()` + `DummyOutput` | `"a\x0ab\r"` → `"a\nb"`（Ctrl+J 换行+Enter 提交）；`\x1b[A` 翻历史；history 文件落盘跨实例可读 |
| select | pipe 发键序列 | `\x1b[B\r` → 第二项；`\r` → default；`\x03` → default |
| spinner | fake clock（递增闭包） | render_line 在 0s/5s 的文案与帧轮换；非 TTY start/stop 零输出 |
| pump+中断 | 阻塞式 FakeProvider（yield 2 事件后卡 Event） | set interrupt 后 0.5s 内返回 RenderResult(partial, interrupted=True)；错误经队列 re-raise |
| REPL 中断 | conftest 加 FakeListener | partial 入史已落盘；零正文回滚未落盘；中断后 REPL 继续可用 |
| EscListener | `pty.openpty()` slave fd 注入 | 写 `\x1b` → event set；写 `\x1b[A` → 不 set；exit 后 termios 还原 |
| cli 装配 | 注入 recording fake selector | 多 provider 且无 -p 才被调；单 provider/-p/非 TTY 不调 |

## v0.2 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 输入库 | prompt_toolkit（不用 Textual/questionary） | 滚动式终端与 Claude Code 同形态；questionary 是 pt 包装且无注入点，手写选择器 ~60 行更可测 |
| 流消费 | 泵线程 + queue（仅可中断模式） | 否则首 token 前主线程阻塞网络读，Esc 无法响应；非 TTY 路径不变保 148 测试确定性 |
| Esc 识别 | cbreak + select + 50ms 转义序列探测 | 区分裸 Esc 与方向键；保留 ISIG 让 Ctrl+C 语义不变 |
| 中断后部分正文 | 入史落盘，「已中断」仅屏显不进 content | partial 是真实模型输出，丢弃则模型失忆半句话；标记进 content 会污染发回模型的上下文 |
| 零正文中断 | 回滚 user 消息 | 维持 v0.1「历史无未答之问」不变量 |
| 计时实现 | Live(get_renderable=callable) | rich refresh 线程免费驱动秒数；流式期间用 Group(Markdown, 计时行) 合成 |
| 输入框边框 | 打印式开口框（╭/│/╰） | Frame 包 TextArea 会失去 PromptSession 的 history/toolbar 免费集成 |
| 历史文件 | $XDG_STATE_HOME/wentian/history | N4 XDG 约定的 state 类数据 |

## v0.2 风险与边界

1. Esc 误判：极端高延迟终端下转义序列可能拆包超 50ms——已知取舍（macOS 本地终端 <1ms）。
2. 中断后泵线程挂在 SDK 网络读：daemon 线程挂到 SDK 超时自亡，可接受，文档记录。
3. KeyboardInterrupt 可落在 renderer 任意点：finally 兜底关 spinner/Live/pump；EscListener `__exit__` 兜底还原 termios。
4. bottom_toolbar 占一行且仅 prompt 活动期显示：状态行设计 ≤60 列。
5. 非 TTY 完全降级：banner 照印（record 可测），selector/PromptInput/EscListener/spinner 全跳过，管道行为与 v0.1 等价。

---

# v0.3 新增设计（F19–F28：工具系统）

> 技术方向：新增 `src/wentian/tools/` 包承载工具系统；协议差异全部在 providers 层内消化（N6）。无新增第三方依赖（全 stdlib）。版本升 `0.3.0`。
> SDK 事实来源：`claude-api` skill（2026-06-11 确认）——Anthropic 工具声明 `{name, description, input_schema}`；OpenAI 兼容 `{type:"function", function:{name, description, parameters}}`；并列调用的全部 tool_result 必须合并进**一条** user 消息；**Anthropic 协议 thinking+tool use 同回合续传必须原样回传 assistant 的原始 content 块（含 thinking 块），删除会 400**；anthropic SDK 高层 stream 自带 tool_use input 累积（`get_final_message()`）；OpenAI 流式按 `delta.tool_calls[].index` 拼接 arguments 碎片，`finish_reason == "tool_calls"`。

## 架构增量

```
cli.py ──► tools/registry.py（建注册中心，注册六工具）+ tools/executor.py（建执行器，注入确认函数）
repl.py ──► 单轮工具回合编排：stream(tools=…) → 执行 → 回灌 → 第二次 stream
render.py ──► 工具调用/结果的屏显（render_tool_call / render_tool_result）
providers/base.py ──► 新事件 ToolCallEvent + ToolSpec + Message 扩展（tool 角色 / tool_calls 字段）
providers/anthropic.py, openai_compat.py ──► 工具声明、流式解析、中性历史→协议格式转换
tools/ ──► base.py（Tool ABC）、registry.py、executor.py、files.py、shell.py、search.py
```

- `tools/` 只依赖 stdlib，零 SDK / 零 rich / 零 prompt_toolkit import（N6/N7）。
- repl/render 只认 `ToolCallEvent` 与 executor 的结构化结果，不认协议细节。
- 分层依赖方向：tools 只 import `providers/base.py`（它是全应用的契约模块，stdlib-only，repl/session/render 同样只认它）——绝不 import 具体 provider；providers 绝不 import tools。声明格式转换发生在 providers 层，输入是中性 `ToolSpec`。

## 核心数据结构（v0.3 新增）

### 中性工具声明与调用（providers/base.py）

```python
@dataclass(frozen=True)
class ToolSpec:
    """协议中立的工具声明；providers 各自转换成线上格式。"""
    name: str
    description: str
    parameters: dict        # JSON Schema（双协议通吃）

@dataclass(frozen=True)
class ToolCallEvent:
    """模型发出的一次完整工具调用（参数碎片已在 provider 层拼接完成）。"""
    id: str                 # 协议侧调用 id（tool_use_id / tool_call_id）
    name: str
    arguments: dict | None  # None = 参数 JSON 无法解析（executor 转结构化错误）

StreamEvent = ThinkingDelta | TextDelta | ToolCallEvent | Done
```

### Message 扩展（providers/base.py）

```python
class ToolCallDict(TypedDict):
    id: str
    name: str
    arguments: dict

class Message(TypedDict, total=False):
    role: Literal["user", "assistant", "tool"]
    content: str
    tool_calls: list[ToolCallDict]   # assistant 消息可携带
    tool_call_id: str                # tool 消息必带
    is_error: bool                   # tool 消息可带
    raw_content: list[dict]          # assistant 消息可带：协议原始 content 块
                                     #（Anthropic thinking+tool 续传所需，见技术决策）
```

- 纯 dict、JSON 可序列化 → session 持久化（F28）零改动即兼容（to_dict/from_dict 已是透传）。
- `raw_content` 由 AnthropicProvider 在 Done 事件携带（`Done.raw_content`），REPL 写入 assistant 消息；OpenAI 协议忽略此字段。

### Provider.stream 签名扩展

```python
def stream(self, messages, *, system=None,
           tools: list[ToolSpec] | None = None) -> Iterator[StreamEvent]: ...
```

`tools=None`（默认）行为与 v0.2 完全一致——既有调用零破坏。

### Tool ABC（tools/base.py）

```python
class Tool(ABC):
    name: str               # 模型可见名，snake_case 英文
    description: str        # 面向模型的功能描述
    parameters: dict        # JSON Schema
    timeout_s: float = 60.0 # executor 强制的墙钟超时

    @abstractmethod
    def run(self, args: dict) -> str: ...
    # 成功返回结果文本；失败 raise ToolError(message)（含参数校验失败）

class ToolError(Exception): ...   # 工具层唯一异常类型，message 面向模型

def spec(tool) -> ToolSpec        # Tool → 中性声明
```

### 执行结果（tools/executor.py）

```python
@dataclass(frozen=True)
class ToolOutcome:
    call_id: str
    name: str
    content: str            # 结果文本或错误描述
    is_error: bool
    denied: bool = False    # 用户拒绝（is_error=True 的子情形，屏显区分用）
```

## 组件设计（C8–C13）

### C8 工具基座 `tools/base.py` + `tools/registry.py`
- `ToolRegistry`：`register(tool)`（重名 raise ValueError）、`get(name) -> Tool | None`、`specs() -> list[ToolSpec]`、`names()`。
- 参数校验：每个工具 `run()` 开头用 `_require(args, "path", str)` 风格的轻量校验（缺失/类型错 raise ToolError），不引 jsonschema 依赖——schema 给模型看，校验自己做。

### C9 六个核心工具 `tools/files.py` / `tools/shell.py` / `tools/search.py`
全部工具构造时接收 `root: Path`（cli 注入 `Path.cwd()`；测试注入 tmp_path）。相对路径基于 root 解析；绝对路径原样使用（不做沙箱，spec 已明确）。

| 工具名 | 模块 | 参数 | 行为要点 |
|--------|------|------|----------|
| `read_file` | files.py | `path` 必填；`offset`/`limit`（行号，选填） | 文本读取；超 2000 行或 50KB 截断并附说明；不存在/是目录 → ToolError |
| `write_file` | files.py | `path`、`content` 必填 | 父目录自动创建；覆盖写；返回写入字节数与路径 |
| `edit_file` | files.py | `path`、`old_string`、`new_string` 必填 | `content.count(old)` 恰为 1 才替换；0 或 >1 → ToolError 报实际次数（F25）；old==new → ToolError |
| `run_command` | shell.py | `command` 必填 | `subprocess.run(["/bin/sh","-c",cmd], capture_output=True, timeout=timeout_s-5, cwd=root)`；返回 stdout+stderr（合并标注）+退出码；输出截 10000 字符（头尾各半）；超时 → ToolError |
| `find_files` | search.py | `pattern` 必填（glob，如 `**/*.py`） | 基于 root 的 `Path.glob`；跳过 `.git`/`.venv`/`node_modules`/隐藏目录；排序输出相对路径；截 200 条附说明 |
| `search_text` | search.py | `pattern` 必填（正则）；`glob` 选填（限定文件） | 逐文件逐行 `re.search`；跳过二进制（含 `\0` 判定）与上述目录；输出 `路径:行号:行内容`；截 200 条；正则非法 → ToolError |

### C10 执行器 `tools/executor.py`（F24/F26）
```python
class ToolExecutor:
    def __init__(self, registry, *, confirm: Callable[[str], bool], clock=None): ...
    def execute(self, call: ToolCallEvent) -> ToolOutcome: ...
```
- 流程：未注册名 → 错误结果；`arguments is None` → 「参数 JSON 解析失败」错误结果；副作用工具（`write_file`/`edit_file`/`run_command`，工具类属性 `requires_confirmation: bool` 标记）→ 调 `confirm(意图描述)`，False → `denied=True` 的「用户拒绝执行」结果；然后在**守护工作线程**里跑 `tool.run(args)`，主线程 `join(timeout_s)`——超时 → 错误结果（线程悬挂自亡，与 v0.2 pump 同取舍）；`ToolError` → 错误结果；其他异常 → 包装为错误结果（含异常类型与 message）。**任何路径都不向上抛异常**（F24）。
- `confirm` 默认实现在 cli 层：TTY 下打印意图 + `input("执行？[y/N] ")`；非 TTY → 恒 False（管道模式自动拒绝，安全默认）。

### C11 协议层工具支持 `providers/anthropic.py` / `openai_compat.py`（F22）
**anthropic.py**：
- `_build_kwargs` 增 `tools=[{name, description, input_schema: spec.parameters}, …]`（tools 参数非 None 时）。
- 流结束后从 `get_final_message().content` 提取 `tool_use` 块 → 逐个 yield `ToolCallEvent(id=block.id, name=block.name, arguments=block.input)`，再 yield `Done(usage, raw_content=...)`；`raw_content` = final.content 各块 `model_dump()`（仅当含 tool_use 块时携带，纯文本回复不带，避免膨胀）。
- 中性历史 → 协议格式（`_convert_messages`）：assistant 带 `raw_content` → 原样作 content（thinking 块保真）；assistant 带 `tool_calls` 无 raw → 重建 `[{"type":"text",...}?, {"type":"tool_use",...}*]`；连续 `role:"tool"` 消息 → 合并为**一条** user 消息 `[{"type":"tool_result","tool_use_id":…,"content":…,"is_error":…}*]`。
**openai_compat.py**：
- 请求加 `tools=[{"type":"function","function":{name, description, parameters}}, …]`。
- 流中累积 `delta.tool_calls`：按 `index` 建 `{id, name, arg_fragments[]}`，流尽后 `json.loads("".join(fragments))` → ToolCallEvent；解析失败 → `arguments=None`。
- 中性历史转换：assistant 带 tool_calls → `{"role":"assistant","content":text or None,"tool_calls":[{id, type:"function", function:{name, arguments: json.dumps(args)}}]}`；tool 消息 → `{"role":"tool","tool_call_id":…,"content":…}`（is_error 折叠为 content 前缀 `[error] `）；`raw_content` 忽略。

### C12 渲染与 REPL 单轮回合 `render.py` + `repl.py`（F23/F27）
**render.py**：
- `render_stream` 收集 ToolCallEvent（不打断既有 thinking/正文逻辑；收集时静默），`RenderResult` 增 `tool_calls: tuple[ToolCallEvent, ...] = ()` 与 `raw_content: list[dict] | None = None`（自 Done 取）。
- 新方法 `render_tool_call(call)`：打印 `⏺ {name}({参数摘要})`（摘要：每参数值截 60 字符，单行）；`render_tool_result(outcome)`：dim 打印 `  ⎿ 成功 · {内容首行截断}` / 红 `  ⎿ 失败 · …` / 黄 `  ⎿ 已拒绝`。与正文 Markdown 样式可区分（F27/AC23）。
**repl.py** `_chat_once` 改造（伪码）：
```
round1 = stream+render（with listener，tools=registry.specs()）
if round1.interrupted: 按 v0.2 语义处理并 return（已收集的 tool_calls 丢弃不执行）
if not round1.tool_calls: 走 v0.2 原路径（入史、落盘）return
入史 assistant(text=round1.text, tool_calls=…, raw_content=…)
for call in round1.tool_calls:
    renderer.render_tool_call(call)
    outcome = executor.execute(call)      # 含确认交互；listener 已退出，termios 干净
    renderer.render_tool_result(outcome)
    入史 tool(tool_call_id, content, is_error)
round2 = stream+render（重新 with listener，tools 同样传）
if round2.interrupted and not round2.text: 不回滚（工具已执行，历史保持完整），仅 return 前落盘
入史 assistant(text=round2.text)          # round2 的 tool_calls 一律不执行
if round2.tool_calls: console 打印提示「本版仅支持单轮工具调用，后续请求未执行」（F23）
store.save(session)
```
- round1 异常（API 错误）→ 既有回滚路径不变。round2 异常 → 不回滚 user（工具已执行入史），打印错误、落盘已有历史。
- interrupt listener 每个 stream 各 with 一次（EscListener 的 cbreak 窗口与确认 input() 不重叠）。

### C13 装配 `cli.py` + `pyproject.toml`
- `build_app` 增参：`tool_registry=None`、`tool_executor=None`（注入点，测试用）；默认装配：六工具 `register` 进新 registry（root=Path.cwd()），executor 用 TTY 确认函数（非 TTY 恒拒）。
- REPL 构造增 `registry`、`executor` 参数（None = 工具关闭，纯 v0.2 行为——离线旧测试零改动）。
- system prompt：工具启用时附加一段简短说明（当前工作目录绝对路径 + 「优先用相对路径」），与人格提示拼接。
- `__version__ = "0.3.0"`；pyproject version 同步。无新依赖。

## 测试策略（v0.3 增量，全部离线）

| 组件 | 测法 | 关键用例 |
|------|------|----------|
| tools/files | tmp_path 真实读写 | read 正常/行范围/不存在/截断；write 建父目录/覆盖/返回路径；edit 唯一替换/0 匹配/多匹配报次数/old==new |
| tools/shell | 真实 subprocess（echo、exit 3、sleep） | stdout+stderr+退出码；输出截断；timeout_s 极小时超时 ToolError |
| tools/search | tmp_path 造目录树 | glob 匹配/跳过 .git/截 200；正则搜索带行号/跳过二进制/非法正则 ToolError |
| registry | 单测 | register/get/specs 往返；重名 raise；FakeTool 即 AC17 的「假工具」 |
| executor | FakeTool（可设抛错/睡眠/慢） | 成功；ToolError→is_error；未注册名；arguments=None；超时；confirm False→denied 且 run 未被调；意外异常不外抛 |
| providers/anthropic | mock SDK | kwargs 含 tools 转换格式；final.content 的 tool_use→ToolCallEvent+Done.raw_content；中性历史转换三情形（raw 保真/重建/连续 tool 合并单 user）；tools=None 时 kwargs 无 tools 字段 |
| providers/openai_compat | mock 流碎片 | index 分组拼接 arguments；坏 JSON→arguments=None；请求 tools 格式；历史转换（tool_calls 序列化/tool 消息/is_error 前缀） |
| render | FakeProvider 含 ToolCallEvent | RenderResult.tool_calls 收集顺序；render_tool_call/result 输出特征（⏺/⎿、成功/失败/拒绝三态） |
| repl 单轮回合 | 脚本化 FakeProvider（首调返回 tool_calls，二调返回文本）+ FakeExecutor | 并列两调用都执行且按序入史；二轮历史含 assistant(tool_calls)+tool 消息；round2 再请求工具→不执行+提示文案；round1 中断→工具不执行；deny→拒绝结果入史；无工具调用→行为与 v0.2 全等 |
| session | 单测 | 含 tool_calls/tool 角色/raw_content 的会话 save/load 往返相等（F28） |
| cli | build_app 注入 recording registry | 默认注册六工具名；REPL 收到 registry/executor；非 TTY confirm 恒 False |
| 端到端（真实联网） | checklist 人工场景 | AC18–AC24（双后端读文件、并列调用、单轮边界、错误诱发、确认/拒绝、--continue 引用） |

## v0.3 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 中性消息格式 | 近 OpenAI 形态的 dict 超集（tool 角色 + tool_calls 字段），providers 各自转线格式 | 单一存储格式直接 JSON 持久化（F28）；OpenAI 侧近乎透传，Anthropic 侧集中一个 `_convert_messages` |
| Anthropic thinking+tool 续传 | Done 携带 `raw_content`（原始 content 块 model_dump），入史后续传原样回放 | 官方要求 thinking 块原样回传否则 400（claude-api skill 确认）；重建会丢 signature。仅含 tool_use 的回复才携带，普通回复零开销 |
| 工具调用事件 | provider 层拼好完整 ToolCallEvent 才发出（不发碎片） | UI 不需要半截 JSON；F22 的「碎片拼接」在协议层消化（N6）；anthropic SDK 高层 stream 已自带累积，OpenAI 手拼 index 分组 |
| 参数 JSON 解析失败 | `arguments=None` → executor 产结构化错误回灌 | 不让坏 JSON 炸掉回合（F24），模型可重试 |
| 超时实现 | executor 守护线程 join(timeout)；run_command 另设 subprocess timeout | 与 v0.2 pump 同模式同取舍（悬挂线程自亡）；子进程真正被杀；全 stdlib |
| 确认机制 | 注入 `confirm: Callable[[str], bool]`；TTY 用 input()，非 TTY 恒 False | F26 最小实现；非 TTY 自动拒绝是安全默认；可测试 |
| round2 工具请求 | 不执行；assistant 消息只存文本（丢弃未答 tool_calls） | F23 单轮铁律；存了 tool_calls 无 result 会让下一轮请求 400（两协议都要求成对） |
| round1 中断 | 已收集 tool_calls 丢弃，沿用 v0.2 部分正文语义 | 中断 = 用户否决本轮，不应再有副作用；assistant 消息不带 tool_calls 故历史合法 |
| round2 异常/零字中断 | user 消息与工具交互不回滚 | 工具已真实执行，回滚历史会与现实世界状态脱节；保完整轨迹 |
| 工具参数校验 | 手写轻量校验 raise ToolError，不引 jsonschema | 六个工具参数都很浅；少一个依赖；错误信息可控且面向模型 |
| 工具命名 | 英文 snake_case（read_file 等） | 模型对英文工具名训练分布最佳；描述也用英文，界面摘要中文 |
| 路径解析 | 工具持 root（默认 cwd），相对路径基于 root，绝对路径放行 | spec 明确不做沙箱；root 注入使 N7 离线测试成立 |
| 结果截断上限 | read 2000 行/50KB、command 10000 字符、search/find 200 条，均附截断说明 | 保护上下文窗口；说明让模型知道还有更多可再查 |

## v0.3 风险与边界

1. **raw_content 跨 provider 恢复**：含 anthropic raw_content 的会话切到 OpenAI 后端续聊——转换层忽略 raw_content、按重建路径走，可对话但 thinking 不保真，可接受。
2. **确认窗口与 Esc listener**：确认 input() 发生在两个 listener 窗口之间，termios 干净；但用户在确认提示按 Ctrl+C → KeyboardInterrupt，REPL 主循环既有兜底捕获，按取消本轮处理（executor 不需特殊处理，REPL 层 catch）。
3. **守护线程超时悬挂**：超时后工具线程可能仍在写文件——文档记录；run_command 因 subprocess timeout 真杀进程，文件工具极快超时概率可忽略。
4. **DeepSeek 等兼容端 tool 支持参差**：声明格式相同但能力由模型决定；不支持工具的模型自然不发 tool_calls，行为退化为纯对话，无需特判。
5. **round2 仍传 tools**：Anthropic 历史含 tool_use 块时请求必须带 tools 参数，故两轮都传同一份声明；模型若再调用走 F23 提示路径。

# v0.4 新增设计（F29–F34：Agent Loop）

> 技术方向：新增 `src/wentian/agent/` 包承载 asyncio 循环核心；provider 与 UI **保持同步**，以「守护线程 → 队列 → 异步轮询」桥接（用户拍板，2026-06-12）。无新增第三方依赖（asyncio 为 stdlib；**不引入 pytest-asyncio**，测试用 `asyncio.run()` 包裹）。版本升 `0.4.0`。

## 架构增量

```
cli.py ──► 装配不变（registry/executor 既有注入点直接复用）
repl.py ──► 删 _run_tool_round；_chat_once 统一为「asyncio.run 驱动 AgentLoop + 事件→渲染映射」；/plan //do 模式开关
render.py ──► 从 render_stream 抽出 push 式 StreamView（像素不变）；新增 render_usage
agent/events.py ──► AgentEvent 联合 + StopReason + RoundResult（C14）
agent/collector.py ──► RoundCollector 双路收集器（C14）
agent/bridge.py ──► StreamBridge 同步流→异步桥 + call_in_thread（C15）
agent/batch.py ──► classify / partition_waves 安全分批（C16）
agent/loop.py ──► AgentLoop 五停机条件循环（C17）
```

- 分层依赖方向延续 v0.3 铁律：agent 层只 import `providers/base.py`（契约模块），**绝不 import wentian.tools**——registry/executor 以 duck-typed `object` 注入（与 repl 同规）；工具结果在事件流里以 `object`（鸭子类型 ToolOutcome）传递。
- **核心不变量：AgentLoop 是工具轮历史的唯一写入者**。每轮的 assistant(+tool_calls) 消息与其全部 tool 结果消息在工具阶段完成后**原子成块追加**——任何路径（含工具阶段 Ctrl+C）都不可能留下「有 tool_calls 没 tool 结果」的残史（Anthropic 400 红线，v0.3 技术决策的结构化升级）。

## 核心数据结构（v0.4 新增，agent/events.py）

```python
class StopReason(enum.Enum):
    COMPLETED         = "completed"          # 模型自然完成（无工具请求）
    MAX_ROUNDS        = "max_rounds"         # 轮数上限兜底
    USER_CANCELLED    = "user_cancelled"     # Esc / Ctrl+C
    UNKNOWN_TOOL_LOOP = "unknown_tool_loop"  # 连续 N 轮全未知工具
    STREAM_ERROR      = "stream_error"       # 流式请求出错

# AgentEvent 联合（全部 frozen dataclass；Thinking/TextDelta 直接复用 providers.base 类型，零拷贝）
RoundStart(index)                       # 1 起；界面开新一轮 StreamView
ThinkingDelta / TextDelta               # 透传
UsageUpdate(round_usage, total)         # 本轮 Done.usage 存在时发出
StreamEnd(index, text, interrupted)     # 界面定稿本轮正文
ToolCallStarted(call: ToolCallEvent)    # 一条 ⏺ 行
ToolResultReady(outcome: object)        # 一条 ⎿ 行（鸭子类型 ToolOutcome）
RoundEnd(index, tool_results)           # 原子入史完成后发出；REPL 的落盘点
AgentDone(stop_reason, text, rounds, usage, error=None)

@dataclass(frozen=True)
class RoundResult:                      # 收集器输出 = 循环的决策输入
    text: str
    tool_calls: tuple[ToolCallEvent, ...]
    raw_content: list | None
    usage: Usage | None
    done_seen: bool                     # False ⇒ 中断或错误终止
```

每轮事件文法（界面可据此做结构化断言）：

```
RoundStart → (ThinkingDelta|TextDelta)* → [UsageUpdate] → StreamEnd
           → (ToolCallStarted ToolResultReady)*    # 仅当有工具调用
           → RoundEnd                              # 仅当工具阶段执行过
(下一轮 RoundStart … | AgentDone)
```

## 组件设计（C14–C20）

### C14 事件契约 + 双路收集器 `agent/events.py` + `agent/collector.py`（F30/F31）
- `RoundCollector.feed(event) -> ThinkingDelta | TextDelta | None`：TextDelta 追加缓冲并**原样返回**（实时显示路径）；ThinkingDelta 原样返回；ToolCallEvent 静默收集（v0.3 同规——流中不渲染）；Done 捕获 usage/raw_content、置 done_seen，返回 None。`result(interrupted) -> RoundResult`。
- 这是 v0.3 `render_stream` 中「消费+累积」职责的拆分：v0.4 累积在 agent 层、显示在 render 层，各管一路（F31）。

### C15 同步→异步桥 `agent/bridge.py`（F30）
- `StreamBridge(events)`：守护线程消费 provider 同步生成器 → `queue.Queue`（`("event",e)/("error",exc)/("end",None)` 三元组，与 v0.2 `_StreamPump` 同构）；`async drain(interrupt)` 先 `get_nowait()` 清空积压（保吞吐），空时检查 interrupt（置位 → `stop()` 并返回）后 `await asyncio.sleep(0.05)`（Esc 响应 ≤50ms）；`("error")` → 重抛给循环。`render.py` 的 `_StreamPump` 原样保留（教学镜像，模块 docstring 注明对应关系）。
- `call_in_thread(fn, *args)`：**专用守护线程** + holder + 50ms 轮询取回结果。**刻意不用 `asyncio.to_thread` / `call_soon_threadsafe`**——见技术决策表 R1 行。executor.execute 永不抛且自带 timeout_s 上限，线程必然终结。
- 取消模型沿 v0.2 哲学：**轮询而非任务取消**。中断后底层线程可能挂在网络读直到 SDK 超时（守护线程自亡，只触碰 queue.Queue，不触碰事件循环）。

### C16 安全分批 `agent/batch.py`（F32）
```python
Kind = Literal["read_only", "side_effect", "unknown", "blocked"]
def classify(call, registry, allowed) -> Kind
    # registry.get(name) is None → "unknown"
    # allowed 非 None 且 name ∉ allowed → "blocked"（计划模式越权）
    # not getattr(tool, "requires_confirmation", True) → "read_only"（属性缺失按 True 处理——fail-safe）
    # 其余 → "side_effect"

@dataclass(frozen=True)
class Wave: calls: tuple[ToolCallEvent, ...]; concurrent: bool
def partition_waves(calls, registry, allowed) -> list[Wave]
```
- **连续只读段合并为一个并发 Wave**；其余每个调用独立串行 Wave、保持原位。理由：`[读A, 写B, 读C]` 不能让 C 越过 B（读到写前内容）；只读段内部互换无害。
- 并发 Wave：全部任务先启动（墙钟=最慢者），但**按原调用顺序 await**——结果天然按原序回灌入史（确定性会话文件；OpenAI 协议 tool 消息位置敏感，按序是唯一可移植选择）。
- 屏显：⏺/⎿ 按调用顺序**成对相邻**发出（`ToolCallStarted` 在其结果即将被 await 时才发）——`render_tool_result` 不带工具名，乱序显示无法归属；并发只体现为总墙钟缩短，不体现为乱序行（教学取舍，写入文档）。
- **并发确认互斥证明**：read_only ⇔ requires_confirmation=False ⇒ executor 确认门对并发 Wave 结构性不可达（arguments=None 也在确认门之前短路）；副作用串行 ⇒ 任意时刻至多一个 `input()`。

### C17 循环控制 `agent/loop.py`（F29）
```python
class AgentLoop:
    def __init__(self, provider, *, registry=None, executor=None,
                 interrupt_listener=None,            # 默认 NullListener
                 max_rounds: int = 20, unknown_streak_limit: int = 2,
                 allowed_tools: frozenset[str] | None = None): ...
    async def run(self, messages, *, system=None, tools=None) -> AsyncIterator[AgentEvent]
    # 原地 mutate messages；持久化归调用方（REPL）
```
每轮 n ∈ 1..max_rounds：
1. `yield RoundStart(n)`；**监听器只武装在流阶段**（`with listener` 每轮一开一关；EscListener 每次 __enter__ 造新 Event，已验证可重入）→ StreamBridge + RoundCollector 消费，透传增量。
2. `yield UsageUpdate`（如有）→ `yield StreamEnd(n, text, interrupted)`。
3. 决策：
   - **中断**：有文字 → 只存 `{"role":"assistant","content":text}`（**丢弃 tool_calls**——v0.3「历史无未答之工具」规则）；零文字 → 什么也不存（回滚归 REPL）。→ `AgentDone(USER_CANCELLED)`。
   - **无 tool_calls** → 存文本 → `AgentDone(COMPLETED)`。
   - **有 tool_calls 且 n == max_rounds** → 不执行、只存文本（丢 tool_calls）→ `AgentDone(MAX_ROUNDS)`。理由：上限是失控刹车，刹车点再放一批违背初衷。
   - **有 tool_calls 且有余量** → 工具阶段：partition_waves → 逐 Wave 执行（`call_in_thread(executor.execute, …)`；blocked 调用**不进 executor**，循环合成 `_BlockedOutcome(is_error=True, denied=False, content="计划模式下 'X' 不可用，仅 read_file/find_files/search_text 可用；请用户 /do 后执行")`）→ 全部结果到齐后**原子成块入史**（assistant：text + tool_calls[arguments None→{}] + raw_content 原样；逐条 tool 消息按调用原序）→ `yield RoundEnd(n, …)`。
4. **未知工具连击**：本轮调用非空且**全部** classify 为 "unknown" → streak+1，否则归零（"blocked" 刻意不计——那是模型在学策略，不是幻觉）；streak ≥ unknown_streak_limit(2) → `AgentDone(UNKNOWN_TOOL_LOOP)`。错误结果已入史，下回合可干净续传。
5. **流错误**（bridge 重抛）：本轮**全部丢弃**（部分文字不入史——v0.3 首轮异常同规；此前轮次的成块已在 messages 里）→ `AgentDone(STREAM_ERROR, error=str(exc))`。

### C18 渲染 `render.py`（F34，外科手术）
- 从 `render_stream` 抽出 push 式 `StreamView`：`start()`（spinner 起）→ `feed(delta)`（首事件停 spinner；thinking 即印；正文缓冲 + 惰性开 Live）→ `finish(interrupted) -> str`（关 Live、终稿 Markdown、中断标记）。**像素不变；硬验收：既有 render/repl 测试零修改全绿**。`render_stream` 改为薄拉式包装（pump/ToolCallEvent 收集/KeyboardInterrupt 留在包装层）。`WaitingSpinner` 已支持重启（每 start 新建 Live，v0.3 一回合两次 render_stream 已实证按轮复用可行）。
- 新增 `new_stream_view() -> StreamView`、`render_usage(usage, rounds)`（单条 dim 行：`tokens 输入 X · 输出 Y · 共 N 轮`；usage 为 None 不打印）。`render_tool_call/result` 原样复用。

### C19 REPL 集成 + 计划模式 `repl.py`（F29/F33）
- **删 `_run_tool_round`**；`_chat_once` 统一（无工具回合 = 第 1 轮即 COMPLETED 的循环）：append user → 记 `baseline = len(messages)` → `asyncio.run(self._consume_agent(agent.run(...)))` → **len-baseline 回滚规则**：循环结束（或 KeyboardInterrupt 兜底）后 messages 长度未超过 baseline ⇒ 零进展，弹出 user 消息、不落盘；否则落盘。逐轮落盘：`_consume_agent` 在每个 `RoundEnd`（tool_results>0）即 save（副作用已真实发生，崩溃不可丢）。
- `_consume_agent(events)`：async 事件→渲染映射器（async 与 Rich 的唯一交汇点）——RoundStart→new_stream_view().start()；增量→view.feed；StreamEnd→view.finish；ToolCallStarted/ToolResultReady→render_tool_call/result；RoundEnd→save；AgentDone 后按停机原因打印提示（STREAM_ERROR 红错误行 / MAX_ROUNDS 黄提示 / UNKNOWN_TOOL_LOOP 黄提示）+ render_usage。
- 监听器从 REPL 移交 AgentLoop（构造注入）；REPL 不再自开监听窗口。
- **计划模式**：`self._plan_mode: bool`（REPL 态，不持久化、不随 /new //resume //provider 重置——它是界面策略不是会话数据）。只读视图用**显式名单** `_PLAN_MODE_TOOLS = ("read_file", "find_files", "search_text")`（构造参数可覆盖供测试）：(a) repl 过滤 `registry.specs()` 按名（ToolSpec 是契约类型，零新耦合）；(b) 同名单作 `allowed_tools` 传 AgentLoop——**双保险**：声明过滤防引导、blocked 拦截防硬闯（声明里没有≠模型不会叫，executor 仍注册着真工具）。计划模式 system 后缀：「计划模式：只有只读工具可用。先勘察代码，再给出分步执行计划后停下，不要做任何修改；用户将用 /do 切到执行模式。」
- `/plan [text]`、`/do [text]` 进 handlers dict；尾随文字即刻作为下一条用户消息走 `_chat_once`；`status_line()` 计划模式下追加 ` │ 计划模式`（PromptInput 工具栏自动拾取）；`_HELP_TEXT` 增两行。
- REPL 构造增 `max_rounds: int = 20`、`plan_tools: tuple[str, ...] = _PLAN_MODE_TOOLS`。

### C20 装配 `cli.py` + `pyproject.toml` + conftest
- `build_app` 零新参（默认值即可，未要求新 CLI 旗标）；版本 `0.4.0`；零新依赖。
- conftest：`ScriptedProvider` 从 test_repl.py **提升**（已存在，不重造——支持按 stream() 调用序号弹出不同事件脚本）；新增 `run_to_list(aiter)` helper（`asyncio.run` 收集 async 迭代器）。

## 模块交互（一次多轮回合的数据流）

```
REPL._chat_once
  └─ asyncio.run( _consume_agent( AgentLoop.run(messages, system, tools) ) )
        AgentLoop 每轮：
          with listener → StreamBridge(provider.stream(…)) ──守护线程──► queue
          async drain ──► RoundCollector.feed ──┬─► (透传增量) yield → _consume_agent → StreamView
                                                └─► (累积) RoundResult
          partition_waves → call_in_thread(executor.execute) ──► ToolCallStarted/ToolResultReady → ⏺/⎿
          原子成块入史 → RoundEnd → REPL save → 下一轮 / AgentDone
```

## 测试策略（v0.4 增量，全部离线，无 pytest-asyncio）

| 组件 | 测法 | 关键用例 |
|------|------|----------|
| agent/events | 单测 | StopReason 五成员；事件 frozen；联合可 isinstance 分发 |
| agent/bridge | 脚本化同步生成器 + asyncio.run | 事件按序产出；interrupt 预置→提前返回不再产出；("error")→重抛；call_in_thread 返回值与永不外抛 |
| agent/collector | 直接 feed 序列 | TextDelta 透传且累积；ToolCallEvent 静默；Done 捕 usage/raw_content；result(interrupted) 形状 |
| agent/batch | FakeRegistry + 慢 FakeTool | partition 各形态（全读/读写读/未知/blocked）；两个 0.2s 只读并发墙钟 < 0.35s；结果按原序 |
| agent/loop | ScriptedProvider 多脚本 + FakeExecutor | 三轮（工具→工具→纯文本）事件序列与入史形状；五停机条件各路径；unknown 连击与重置；中断有/无文字；首轮/中途流错误入史差异；usage 累计 |
| render | 既有测试 + record Console | **既有 render 测试零修改全绿**（重构硬验收）；StreamView push 序列输出与 render_stream 拉式一致；render_usage 单行 |
| repl | ScriptedProvider + FakeListener | 多轮往返入史+逐轮落盘；len-baseline 回滚两例；KeyboardInterrupt 不崩且历史成对；停机提示文案 |
| repl 计划模式 | recording registry/executor | /plan 后 stream 收到的 tools 仅三只读且 system 含后缀；越权 write_file → blocked 错误入史且 executor 未被调；status_line 标记；/do 尾随文字成为用户消息 |
| cli | 既有注入点 | 默认装配回归；版本 0.4.0 |
| 端到端（真实联网） | checklist 人工场景 | AC25–AC32（多轮真实任务、各停机、计划模式、双后端、用量、持久化） |

## v0.4 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 异步底座 | agent 核心 asyncio；provider/UI 保持同步，线程桥接 | 用户拍板；改造面可控（providers/tools/ui 零改动），事件流真异步，asyncio.gather 真并发 |
| 桥接线程 | **专用守护线程 + 50ms 轮询，不用 to_thread / call_soon_threadsafe** | `asyncio.run()` 收尾会 join 默认 executor——confirm 的 `input()` 停在 to_thread 里会把 Ctrl+C 后的 REPL 冻住直到用户按回车；`call_soon_threadsafe` 在事件循环关闭后被悬挂线程调用会抛 RuntimeError。守护线程只触碰 queue.Queue，永不阻塞收尾（实现者不得「优化」回 to_thread） |
| AgentEvent 归属 | agent 包内新模块，不进 providers/base.py | providers/base 是 LLM 契约；AgentEvent 是应用层契约，消费方是 UI。Thinking/TextDelta 复用避免拷贝 |
| 事件流形态 | async generator（`AsyncIterator[AgentEvent]`） | 单消费者顺序消费天然背压；记录器即可全断言（AC28） |
| 入史原子性 | assistant+全部 tool 消息成块追加，工具阶段完成后一次写入 | 任何异常/中断点都不会留下未配对 tool_calls（Anthropic 400 红线）；v0.3 是流程保证，v0.4 升级为结构保证 |
| 并发结果顺序 | 任务并发启动、按原调用顺序 await/回灌/屏显 | 会话文件确定性；OpenAI tool 消息位置敏感；⎿ 行无工具名，乱序无法归属 |
| 上限语义 | 刹车点不执行最后一批，存文本丢 tool_calls | 上限是失控保护；「再放一批」让上限名存实亡；丢弃方式与 v0.3 round2 同规 |
| unknown 连击定义 | 「本轮全部调用均未注册」记 1 连击，2 连击停；blocked 不计 | 半对半错说明模型仍在正轨；blocked 是策略教学非幻觉 |
| 计划模式实施 | 显式名单（声明过滤 + allowed_tools 双保险），不靠 requires_confirmation 推断 | repl 不 import tools（分层）；未来工具属性变化不破策略；名单即提示词素材；声明过滤挡引导、blocked 拦截挡硬闯 |
| 计划模式状态 | REPL 内存态，不持久化 | 它是界面策略不是对话内容；恢复会话默认回执行模式更安全 |
| 落盘节奏 | 每 RoundEnd 一次 + 回合终了一次 | 副作用真实发生后立刻持久化，进程崩溃不丢轨迹（v0.3「先落盘再二轮」同规的推广） |
| 回滚规则 | len-baseline：循环零进展 ⇒ 弹出 user 消息不落盘 | v0.2/v0.3「不留未回答提问」规则在多轮下的统一表述 |
| 测试不引 pytest-asyncio | 仅核心 async，`asyncio.run()` 包裹即测 | 少一个依赖与 asyncio_mode 配置；与「provider/UI 保持同步」一致 |
| pump 双份 | render._StreamPump 保留，bridge 独立实现 | 同步/异步 drain 差异大于共享收益；render.py 及其测试零搅动；docstring 互注教学镜像 |

## v0.4 风险与边界

1. **R1 asyncio.run 收尾 join 默认 executor**：本设计全部阻塞调用走专用守护线程，绕开该坑；若实现时替换为 to_thread，确认 input() + Ctrl+C 会冻住 REPL——列为评审检查点。
2. **R2 Esc 后桥线程悬挂**：线程可能挂在网络读直到 SDK 超时（继承 v0.2 取舍，守护线程自亡，不触碰事件循环）。
3. **R3 工具阶段 Ctrl+C**：KeyboardInterrupt 落在主线程 asyncio.run 内——副作用可能已发生但该轮未入史（原子块未写）；严格安全于 v0.3（历史仍成对），文档化为已知边界。
4. **R4 Rich Live × asyncio**：渲染调用都发生在 _consume_agent 协程内、同一线程顺序执行；事件文法保证 StreamView 开闭之间无其他打印。
5. **R5 工具执行期间 Esc 不可用**：监听器只围流阶段（termios 与确认 input() 互斥的结构性保证）；长命令靠 executor 超时兜底。spec 已列入「不做的事」。
6. **R6 计划模式下未知工具报错文案**：executor 的未注册提示会列出全部六个工具名（含副作用工具）——轻微不一致，可接受；修正需 executor 感知模式，违反分层，不做。

## v0.4 实现补注（开发期落定，回填本文档）

> 以下为开发阶段在不偏离设计意图前提下落定的实现细节，按「先改 spec 再动代码」铁律的精神回填，供后续读者对齐。

1. **单只读调用 → 串行 Wave（T51）**：`partition_waves` 对**长度 ≥ 2** 的连续只读段才建并发 Wave；孤立的单个只读调用归为 `concurrent=False` 的串行 Wave。行为等价（单调用无并行收益），与 task.md T51 示例一致；plan.md C16「连续只读段合并为一个并发 Wave」的措辞按此理解（合并发生在 ≥2 时）。
2. **executor 缺席的退化模式（T55）**：registry 存在但 executor=None 时，REPL 仍向后端声明 tools（保 v0.3 既有测试），但以 `registry=None / executor=None / max_rounds=1` 构造 AgentLoop——模型若请求工具，第 1 轮即触 MAX_ROUNDS 刹车（存文本、不执行、无未答 tool_use），外部行为等价于 v0.3「忽略 tool_calls」。该模式下 MAX_ROUNDS 提示被抑制（`limit_notice=False`），与 v0.3 在此处的静默一致；真实 executor 模式不受该标志影响。
3. **RoundEnd 与落盘**：仅当本轮 `tool_results > 0` 时 `_consume_agent` 才在 RoundEnd 落盘；纯文本轮（无工具）不触发逐轮落盘，由回合终了的统一落盘覆盖。RoundEnd 始终在原子块入史**之后**发出，故每次落盘持久化的都是成对完整历史。

# v0.5 新增设计（F35–F40：结构化系统提示 + 提示词缓存）

> 技术方向：新增 `src/wentian/prompt/` 包承载系统提示的模块化组装（`system.py`）与动态提醒的请求时拼装（`reminders.py`）；二者皆纯函数、对协议无感知。后端层消化缓存差异——Anthropic 在 `system` 块上打 `cache_control` 断点、OpenAI 走自动前缀缓存，两端都解析缓存命中字段。`Usage` 扩两个带默认值的缓存字段。AgentLoop 增一个请求装配回调，使「提醒在每轮请求时注入、永不持久化」与「agent 层不 import tools」两条铁律同时成立。无新增第三方依赖。版本升 `0.5.0`。

## 架构增量

```
prompt/__init__.py ──► 新包
prompt/system.py ──► 七固定模块 + 可选槽位 + build_system_prompt（C21）
prompt/reminders.py ──► EnvInfo + <system-reminder> 构造 + cadence + build_request_decorator（C22）
providers/base.py ──► Usage 扩 cache_creation/cache_read 两字段（默认 0）；stream(system) 语义不变仍收 str（C25）
providers/anthropic.py ──► system: str → 带 cache_control 的单块数组；解析 message_start.usage 缓存字段（C23）
providers/openai_compat.py ──► 解析 usage.prompt_tokens_details.cached_tokens → cache_read（C24）
agent/loop.py ──► run() 增可选 request_decorator(messages, round_index)→messages；跨轮 usage 累计含缓存字段（C25）
render.py ──► render_usage 在缓存命中时追加缓存读/创建 token（C26）
repl.py ──► system 改由 prompt.system 产出；构造 reminder decorator 注入 AgentLoop；计划模式提醒从 system 后缀迁到 <system-reminder>（C27）
cli.py ──► build_app 用 build_system_prompt 取代 _tools_system_prompt；版本 0.5.0（C28）
```

- 分层依赖延续铁律：`prompt/` 包**零后端 SDK / 零 rich / 零 prompt_toolkit** import（纯数据→文本）；agent 层仍**绝不 import wentian.tools**——请求装配以 duck-typed 回调 `Callable[[list[Message], int], list[Message]]` 注入（与 registry/executor 同规）。
- **核心不变量（v0.5 新增）：会话持久化的 messages 永不含任何 `<system-reminder>` 提醒块。** 提醒只在「发请求」这条路径上、由 decorator 在 messages 的**副本**上拼装；REPL 落盘的始终是 `session.messages` 原件。恢复会话 + 重发请求时，提醒由 decorator 当场重建，不会累积陈旧环境块。
- **缓存边界（v0.5 新增）：稳定的「工具声明 + system」前缀是唯一缓存对象。** 因全部动态内容已移出 system（走消息通道），system 块 100% 稳定 → Anthropic 单个 `cache_control` 断点打在 system 块上即缓存其前缀全段（tools 在 system 之前）；对话历史不打断点、不进缓存（本版边界）。

## 核心数据结构（v0.5 新增）

```python
# providers/base.py —— Usage 扩两个带默认值字段（既有构造点零破坏）
@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0   # Anthropic：写入缓存的 token（首次）
    cache_read_input_tokens: int = 0        # Anthropic cache_read / OpenAI cached_tokens

# prompt/system.py —— 模块 = (名称, 渲染函数)；渲染返回空串 ⇒ 该模块不输出
@dataclass(frozen=True)
class PromptContext:
    cwd: Path
    tool_names: tuple[str, ...]                       # 供「工具使用」模块列举
    project_instructions: str = ""                    # 可选槽位（本版恒空）
    active_skills: tuple[str, ...] = ()               # 可选槽位（本版恒空）
    memory: str = ""                                  # 可选槽位（本版恒空）

Module = tuple[str, Callable[[PromptContext], str]]   # (name, render)
_FIXED_MODULES: tuple[Module, ...]      # 身份/系统约束/任务模式/动作执行/工具使用/语气风格/文本输出（顺序即优先级）
_OPTIONAL_MODULES: tuple[Module, ...]   # 自定义指令/已激活Skill/长期记忆（本版渲染恒空）
def build_system_prompt(ctx: PromptContext, *, modules: tuple[Module, ...] | None = None) -> str
    # 依次渲染、丢弃空串、以 "\n\n" 连接；modules 参数供测试注入假模块（AC33）

# prompt/reminders.py —— 动态内容（请求时拼装、永不持久化）
@dataclass(frozen=True)
class EnvInfo:
    cwd: Path; os: str; date: str; git_branch: str | None
def render_env_reminder(env: EnvInfo) -> str
    # "<system-reminder>\n工作目录: …\n操作系统: …\n日期: …\nGit 分支: …\n</system-reminder>"
def render_switch_reminder(*, plan_mode: bool, round_index: int,
                           repeat_every: int = 5) -> str | None
    # plan_mode=False → None；round_index==1 或 (round_index-1)%repeat_every==0 → 完整提醒；否则 → 一行精简
def build_request_decorator(*, env: EnvInfo, plan_mode: bool, repeat_every: int = 5
                            ) -> Callable[[list[Message], int], list[Message]]
    # 返回 decorator(messages, round_index)：拷贝 messages →
    #   首条 user 消息 content 前置 env 提醒；
    #   末条 user 消息 content 追加 switch 提醒（如有）；
    #   返回新列表，绝不改动入参（持久化安全）
```

## 组件设计（C21–C28）

### C21 系统提示模块化组装 `prompt/system.py`（F35/F36/F37）
- 七个固定模块各是一个 `(name, render)`：
  1. **身份**：文天是谁——沙雕、幽默、真诚、爱照顾朋友的命令行编程伙伴，油塌头 `=^_^=`；底子是严谨工程师。
  2. **系统约束**（红线，最高约束级）：spec 驱动（先改 spec 再动代码）、TDD（没有先失败的测试不写生产代码）、完成前验证（没有当场新鲜证据不声称完成）、不编造（拿不准就说/标注）、危险或外发操作先确认、改写资源先备份。
  3. **任务模式**：声明当前工作模式的存在（普通执行 / 计划模式）；计划模式的逐轮细节由消息通道提醒承载（见 C22），此处只给总纲。
  4. **动作执行**：怎么干活——先勘察后动手、小步验证、引用 `file:line`、改动匹配周边代码风格、外发/危险操作先确认。
  5. **工具使用**：列举可用工具（由 `ctx.tool_names` 注入）+ 关键约定——**优先用专用工具而非 shell、编辑文件前必先读取、无依赖的调用可并行发起、优先相对路径**（F37：这些句子与工具 description 中的措辞一致）。
  6. **语气风格**：文天怎么说话——贫但不啰嗦、真诚、爱照顾人、适度自嘲油头；技术内容不掺水、不打太极。
  7. **文本输出**：终端 Markdown 渲染约定、简洁优先、`file:line` 可点击、不滥用标题。
- 可选模块（自定义指令/已激活 Skill/长期记忆）的 render 本版恒返回 `""`（接口就绪、内容留后续版本）。
- `build_system_prompt`：渲染全部模块、过滤空串、`"\n\n"` 连接。`modules` 参数允许测试注入假模块，验证「拼装器与模块定义解耦」（AC33）。

### C22 动态提醒请求时拼装 `prompt/reminders.py`（F39）
- `render_env_reminder` / `render_switch_reminder`：纯文本构造，包 `<system-reminder>` 标签。switch 的 cadence：第 1 轮与每 `repeat_every`（默认 5）轮的轮首给完整提醒，其余轮给一行精简（如「（仍在计划模式：只读勘察、不改动）」）。
- `build_request_decorator` 返回的闭包是注入 AgentLoop 的回调：在 messages **副本**上，首条 user 前置 env 提醒、末条 user 追加 switch 提醒（改 content 而非新增消息——避免破坏 Anthropic 的 user/assistant/tool 配对，消息数与角色序列不变）。入参 messages 绝不被改动（持久化安全的结构保证）。
- 防御：messages 中找不到 user 消息时（理论上首轮必有），env 提醒跳过、不抛错。

### C23 Anthropic 缓存断点 + 命中解析 `providers/anthropic.py`（F38/F40）
- `_build_kwargs`：`system` 非空时，从裸字符串改为 `[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]`（单块单断点；其前的 `tools` 随前缀一并缓存）。`system` 为 None 时仍省略该键（v0.4 等价）。
- 流式用量解析：从 `message_start` 事件的 `usage` 读取 `cache_creation_input_tokens` / `cache_read_input_tokens`，填入 `Usage`（缺失按 0）。其余 stream 逻辑不变。

### C24 OpenAI 兼容缓存解析 `providers/openai_compat.py`（F38/F40）
- `system` 仍作单条 `{"role": "system"}` 消息（不显式打缓存标，走服务端自动前缀缓存）。
- 用量解析：从 `usage.prompt_tokens_details.cached_tokens`（存在时）读出，映射到 `Usage.cache_read_input_tokens`；`cache_creation` 该协议无对应、恒 0。字段缺失按 0。

### C25 Usage 扩展 + 循环装配回调 `providers/base.py` + `agent/loop.py`（F39/F40）
- `Usage` 加两个默认 0 字段（见数据结构）；跨轮累计（v0.4 的 usage 相加逻辑）同步累加缓存两字段。
- `AgentLoop.run` 增可选关键字参 `request_decorator: Callable[[list[Message], int], list[Message]] | None = None`：每轮在 `provider.stream(...)` 之前，`outgoing = request_decorator(messages, n) if request_decorator else messages`，用 `outgoing` 发请求；**入史与持久化仍只动 `messages` 原件**（decorator 产出的副本只用于本次请求）。decorator 为 None ⇒ 行为与 v0.4 完全一致（回归保证）。

### C26 渲染缓存命中 `render.py`（F40，外科手术）
- `render_usage(usage, rounds)`：当 `usage.cache_read_input_tokens` 或 `cache_creation_input_tokens` > 0 时，在既有用量行后追加 `· 缓存读 X · 缓存写 Y`；全为 0 时与 v0.4 输出逐字一致（既有 render_usage 测试零修改全绿）。

### C27 REPL 接线 + 计划模式提醒迁移 `repl.py`（F35/F39/F33 迁移）
- `system` 不再由 cli 的 `_tools_system_prompt` 给定，而是由 `build_system_prompt(PromptContext(cwd, tool_names=…))` 产出（在 build_app 装配，见 C28）；REPL 收到的仍是一个 `str`。
- 每个回合构造 `request_decorator`：`build_request_decorator(env=EnvInfo(当前 cwd/os/date/git), plan_mode=self._plan_mode, repeat_every=…)`，传入 `agent.run(..., request_decorator=…)`。env 当场采集（git 分支可缺省 None）。
- **计划模式迁移**：删去 `_effective_tools_and_system` 里给 `system` 追加计划模式后缀的逻辑——`system` 恒为稳定的结构化提示（保缓存稳定）；计划模式的「你在计划模式、只读勘察、产计划后停」改由 switch 提醒经消息通道注入（cadence 控频）。F33 的其余机制**全部保留**：声明过滤（`registry.specs()` 按 `_PLAN_MODE_TOOLS` 过滤）、`allowed_tools` 传 AgentLoop（blocked 拦截）、`status_line` 计划模式标记——均不变。
- `_effective_tools_and_system` 简化为只决定 tools（system 恒定）；或重命名/收窄职责（实现期定，回填补注）。

### C28 装配 `cli.py` + `pyproject.toml`（F35）
- `build_app`：用 `build_system_prompt(PromptContext(cwd=root, tool_names=registry 暴露的工具名))` 取代 `_tools_system_prompt(root)` 作为 REPL 的 `system`；`tool_names` 从 registry/默认六工具取。无新 CLI 旗标。版本 `0.5.0`；零新依赖。

## 模块交互（一次请求的数据流，v0.5 视角）

```
build_app: build_system_prompt(ctx) → system(str, 稳定) ──► REPL._system
REPL._chat_once（每回合）:
  decorator = build_request_decorator(env, plan_mode, repeat_every)
  asyncio.run( _consume_agent( AgentLoop.run(messages, system=system, tools=tools,
                                              request_decorator=decorator) ) )
    AgentLoop 每轮 n：
      outgoing = decorator(messages, n)          # 副本：首 user 前置 env、末 user 追加 switch 提醒
      provider.stream(outgoing, system=system, tools=tools)
        Anthropic: system→[{text, cache_control:ephemeral}]；解析 cache_read/creation
        OpenAI:   system→{role:system}；解析 cached_tokens
      …收集/工具/入史均只动 messages 原件（提醒不入史）→ UsageUpdate 含缓存字段
  RoundEnd/回合终了 save(messages)               # 落盘的 messages 不含任何提醒
```

## 测试策略（v0.5 增量，离线为主）

| 组件 | 测法 | 关键用例 |
|------|------|----------|
| prompt/system | 直接调 build_system_prompt | 七模块按序出现、空行分隔；可选模块空→无残渣；注入假模块验证拼装器解耦（AC33）；身份/语气模块含文天人格关键串（AC34 离线半）；工具模块含「编辑前先读」等约定句（AC35 一半）|
| prompt/reminders | 纯函数断言 | env 提醒含 `<system-reminder>` 标签与四项；switch cadence（轮 1/6 完整、轮 2-5 精简、plan_mode=False→None）；decorator 在副本上注入、入参 messages 不变（持久化安全）；首 user 前置、末 user 追加；无 user 消息不抛 |
| providers/anthropic | _build_kwargs + 假 message_start | system→带 cache_control 的块数组、断点在 system；system=None 省略键（回归）；解析 cache_creation/read，缺失按 0 |
| providers/openai_compat | _build_messages + 假 usage | system 仍单条 system 消息；解析 prompt_tokens_details.cached_tokens→cache_read；缺失按 0 |
| providers/base + loop | Usage 构造 + ScriptedProvider | Usage 两默认字段、位置/关键字构造兼容（既有点不破）；跨轮缓存字段累计；request_decorator 每轮被调且 outgoing≠原件、原 messages 与落盘 messages 不含提醒；decorator=None 行为与 v0.4 全等 |
| render | record Console | 缓存>0 追加缓存读/写；缓存=0/None 输出与 v0.4 逐字一致（既有测试零改） |
| repl | ScriptedProvider + recording registry | system 来自 build_system_prompt（含七模块）、不含计划模式后缀（AC40 断言）；计划模式经 decorator 注入 switch 提醒、声明过滤/blocked/status_line 全绿（F33 回归）；落盘 messages 无提醒块（AC37） |
| cli | 既有注入点 | build_app 产出的 system 非空且结构化；版本 0.5.0 |
| 端到端（联网） | checklist 人工场景 | AC34（人格）/AC36（cache_read>0）/AC37（不当用户输入回复）/AC39（用量行显缓存） |

## v0.5 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 缓存范围 | Anthropic 显式 `cache_control` 断点为主，两端都解析命中字段 | 用户拍板；Anthropic 有显式断点与明确命中字段、可精确省钱省时延；OpenAI 无显式 API 故走自动前缀缓存 |
| 缓存断点数 | system 块上**单断点**（缓存 tools+system 整段前缀） | 动态全走消息通道 ⇒ system 100% 稳定；前缀缓存自然覆盖其前的 tools；不缓存历史（本版边界）|
| 动态内容通道 | 全部以 `<system-reminder>` 走消息通道，零动态进 system | 用户拍板；system 零动态 → 缓存稳定命中；标签让模型当系统补充指令、不当用户输入回复；与 Claude Code 自身做法一致 |
| 提醒注入点 | env→首条 user 前置；switch→末条 user 追加；改 content 不新增消息 | 不破坏 user/assistant/tool 配对（Anthropic 400 红线）；消息数与角色序列不变，双协议安全 |
| 提醒不持久化 | 请求时由 decorator 在 messages 副本上拼装，原件入史/落盘 | 会话文件保持纯净；恢复/重发不累积陈旧 env 块（结构保证而非约定）|
| 装配回调注入 agent 层 | duck-typed `request_decorator` 回调，agent 层不 import prompt/tools | 延续 v0.4 分层铁律；decorator=None 即 v0.4 行为（回归安全）|
| cadence | 首轮 + 每 N(=5) 轮完整、其余精简 | 控制注入频率省 token，又防长循环里模型忘记开关；N 可配 |
| Usage 扩字段给默认 0 | cache_* 两字段默认 0 | 504 既有测试与全部构造点零破坏；后端未报按 0 |
| 内容深雕 + 文天人格 | 身份/语气/输出写出人格；约束/执行保持工程严谨 | 用户要「干得好」不止「能干活」；语气是猫、底子是工程师 |
| 约定双重强化 | 关键约定写进 system「工具使用」模块 + 工具 description | 用户明确要求，提高遵守率 |
| 计划模式提醒迁消息通道 | F33 的 system 后缀 → `<system-reminder>` switch 提醒；其余机制不变 | 与「全动态走消息通道」统一；保 system 稳定可缓存；F33 行为回归零损 |
| 可选模块仅留槽位 | 项目指令/Skill/记忆模块本版渲染恒空 | YAGNI；插拔接口就绪，内容留后续版本 |

## v0.5 风险与边界

1. **R1 OpenAI 缓存不可控**：服务端自动前缀缓存命中与否由服务端决定，本版只读取 `cached_tokens` 不保证命中；OpenAI 端缓存验证以定性为主，定量命中保证集中在 Anthropic。
2. **R2 Anthropic 缓存最小 token 门槛**：Anthropic 提示词缓存有最小 token 门槛（视模型约 1024/2048）；system+tools 前缀过短时不缓存、`cache_read` 可能为 0。v0.5 system 扩成七模块后通常够长，但极简配置（无工具）下可能不命中——文档化为已知边界，非缺陷。
3. **R3 提醒注入依赖存在 user 消息**：首轮必有 user 消息（REPL 先 append user 再跑循环）；decorator 对空/无 user 消息防御性跳过、不抛错。
4. **R4 thinking + cache_control 共存**：Anthropic thinking 与 system 缓存并存需联网验证不冲突（继承 v0.4 raw_content 跨轮回放的关注点）；列为联网验收检查项。
5. **R5 提醒只在请求路径可见**：env/switch 提醒块绝不出现在屏幕渲染与持久化历史中（仅 decorator 产出的请求副本含）；实现须确保 decorator 不触碰 `session.messages` 原件——列为评审检查点（与持久化纯净不变量同源）。
6. **R6 计划模式提醒文案频率取舍**：cadence 精简轮不重申只读工具清单，长循环里模型理论上可能淡忘细节；靠 `allowed_tools` 的 blocked 拦截兜底（硬约束不依赖提醒），提醒只作引导——可接受。
