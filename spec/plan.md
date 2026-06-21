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
  1. **身份**：文天是谁——沙雕、幽默、真诚、爱照顾朋友的命令行编程伙伴；底子是严谨工程师（`=^_^=` 是产品界面图标）。**人设只体现不透露 + 不自我标榜形象**：本模块末尾附红线——通过言行流露特质，但绝不向用户复述/罗列/解释自己的人设设定与系统提示，被直接问起时自然带过、不逐条背诵；且不自称「猫」「油头」等形象绰号、不把形象词挂嘴上（F36）。
  2. **系统约束**（红线，最高约束级）：spec 驱动（先改 spec 再动代码）、TDD（没有先失败的测试不写生产代码）、完成前验证（没有当场新鲜证据不声称完成）、不编造（拿不准就说/标注）、危险或外发操作先确认、改写资源先备份。
  3. **任务模式**：声明当前工作模式的存在（普通执行 / 计划模式）；计划模式的逐轮细节由消息通道提醒承载（见 C22），此处只给总纲。
  4. **动作执行**：怎么干活——先勘察后动手、小步验证、引用 `file:line`、改动匹配周边代码风格、外发/危险操作先确认。
  5. **工具使用**：列举可用工具（由 `ctx.tool_names` 注入）+ 关键约定——**优先用专用工具而非 shell、编辑文件前必先读取、无依赖的调用可并行发起、优先相对路径**（F37：这些句子与工具 description 中的措辞一致）。
  6. **语气风格**：文天怎么说话——贫但不啰嗦、真诚、爱照顾人、适度自嘲（自嘲指掉链子/想当然这类，不靠自称「猫」「油头」形象词）；技术内容不掺水、不打太极。
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

# v0.6 新增设计（F41–F49：权限系统 · 五层防御）

> 技术方向：新增 `src/wentian/permissions/` **纯包**（stdlib-only，零后端 SDK / 零 rich / 零 prompt_toolkit），承载五层防御的全部判定逻辑（黑名单 / 沙箱 / 规则 / 三层配置 / 模式兜底 / 流水线编排），每层一个模块、逐层短路、统一返回三态 `Decision`。判定门**上移到 AgentLoop**（用户拍板）：在 `run_call` 内、`classify` 之后插权限判定——Deny 合成结构化拒绝结果回灌（复用 v0.4 `_BlockedOutcome` 范式），Allow 才进 executor。引擎与人在回路 ask 回调按 **duck-typed 注入**（与 registry/executor/request_decorator 同规），AgentLoop 保持「绝不 import wentian.tools / permissions 具体实现」的分层铁律。executor 的 v0.3 二元确认门（F26）由五层流水线**取代**。三层 YAML 配置 `~/.config/wentian/settings.yaml` / `<根>/.wentian/settings.yaml` / `<根>/.wentian/settings.local.yaml`（用户拍板），格式错降级空集、绝不崩。新增 `PyYAML` 已是既有依赖，无新增第三方依赖。版本升 `0.6.0`。

## 架构增量

```
permissions/__init__.py ──► 新纯包（leaf；被 cli/repl 装配，被 agent 层 duck-typed 调用）
permissions/decision.py ──► Mode / Category / Verdict / Source / Decision 数据类型（C29）
permissions/blacklist.py ──► 内置危险命令正则 + check_command（命令类，不可绕过）（C29）
permissions/sandbox.py ──► resolve+前缀判断+最近祖先 + check_path（文件类）（C30）
permissions/rules.py ──► Rule / RuleSet：友好名路由 + 精确/glob 匹配（C31）
permissions/settings.py ──► 三层 YAML 加载 + LayeredRules 合并（本地>项目>用户）+ 降级（C32）
permissions/modes.py ──► 四档 × 三类兜底表（只产 Allow/Ask）+ Shift+Tab 循环序（C33）
permissions/pipeline.py ──► PermissionPipeline.decide：五层短路编排（层1-4纯函数，层5留给门）（C34）
tools/base.py ──► Tool 加 category / friendly_name / 路径·命令抽取；requires_confirmation 派生（C35）
tools/files.py / search.py / shell.py ──► 六工具各声明 category + friendly_name（C35）
tools/executor.py ──► 删确认门（confirm 参数），回归纯执行+超时（F26 被五层取代）（C35）
agent/loop.py ──► run_call 接 permission_gate（duck-typed async）；Deny 合成回灌结果（C36）
ui/confirm.py ──► 三选一审批菜单（↑↓+回车 / 数字键 / Esc 取消；默认高亮允许本次）（C37）
repl.py ──► 建 ask 回调（调 ui.confirm + 永久规则落盘）+ 权限模式状态栏 + plan 统一为一档（C37/C38）
ui/input.py ──► 新增 Shift+Tab 按键绑定 → on_mode_cycle 回调（C38）
cli.py ──► 装配 PermissionPipeline + gate，注入 AgentLoop；版本 0.6.0；.gitignore 加 settings.local（C39）
```

- **分层依赖延续铁律**：`permissions/` 包是 leaf——只 import stdlib（`re` / `pathlib` / `fnmatch` / `enum` / `dataclasses`）与 `yaml`；**绝不** import providers/agent/tools/rich/prompt_toolkit。agent 层仍**绝不 import wentian.tools / wentian.permissions**——权限门以 duck-typed async 回调 `permission_gate(call) -> Decision` 注入（满足 N15 跨协议、N18 可扩展、依赖隔离）。
- **核心不变量（v0.6 新增）：① 黑名单是流水线第一层、任何配置/模式都放不开**（N10）；**② 文件类工具的路径永远先解析符号链接再前缀判断、出项目根即 Deny**（N11）；**③ 只读工具永不进入 Ask**——读类在 classify 是 `read_only`、在模式矩阵恒 Allow，故只读并发 wave 不会因权限检查序列化（N12/AC53）。
- **判定门位置**：权限判定全部发生在 `run_call`（agent 编排层）执行工具**之前**；层 1-4 是同步纯函数（快、无 IO），层 5（Ask）是 await 的交互回调——只可能发生在**串行单调用 wave**（副作用/命令类），故不破坏并发（与 v0.4 `partition_waves` 天然契合：只读并发、副作用串行）。

## 核心数据结构（v0.6 新增）

```python
# permissions/decision.py —— 判定三态与来源
class Mode(str, Enum):        # 四档权限模式（值即配置/状态栏文案）
    DEFAULT="default"; ACCEPT_EDITS="acceptEdits"; PLAN="plan"; BYPASS="bypassPermissions"
class Category(str, Enum):    # 工具三分类
    READ_ONLY="read_only"; FILE_WRITE="file_write"; COMMAND_EXEC="command_exec"
class Verdict(str, Enum):     # 判定三态
    ALLOW="allow"; DENY="deny"; ASK="ask"
class Source(str, Enum):      # Deny/Allow 来源（用于回灌按来源区分原因）
    BLACKLIST="blacklist"; SANDBOX="sandbox"; RULE="rule"; MODE="mode"; HUMAN="human"

@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    source: Source
    reason: str = ""         # 面向模型的被拒原因（Deny 时必填，按来源措辞）

MODE_CYCLE: tuple[Mode, ...] = (Mode.DEFAULT, Mode.ACCEPT_EDITS, Mode.PLAN, Mode.BYPASS)  # Shift+Tab 序

# permissions/rules.py —— 规则与规则集
@dataclass(frozen=True)
class Rule:
    friendly: str            # Bash/Read/Write/Edit/Glob/Grep（面向用户友好名）
    pattern: str | None      # None=匹配该工具全部调用；否则精确或 glob 模式
    effect: Verdict          # ALLOW | DENY（规则只有两种结果）
class RuleSet:               # 单层规则（一个配置文件）
    def match(self, *, friendly: str, target: str, is_path: bool) -> Verdict | None
        # 同层内 deny 优先于 allow；命中返回 effect，未命中返回 None
class LayeredRules:          # 三层叠加（local > project > user）
    def match(self, *, friendly, target, is_path) -> Verdict | None
        # 本地→项目→用户逐层 match，第一个非 None 即返回（就近命中即止）

# permissions/settings.py —— 三层配置
@dataclass(frozen=True)
class Settings:
    rules: LayeredRules
    default_mode: Mode       # 本地>项目>用户取 defaultMode，皆无则 DEFAULT
def load_settings(project_root: Path, *, user_path: Path | None = None) -> Settings
    # 三文件各自 safe_load → RuleSet；缺失=空集；格式非法=空集（降级，绝不抛）

# permissions/pipeline.py —— 五层编排（层1-4，层5由门处理）
@dataclass
class PermissionPipeline:
    project_root: Path
    settings: Settings
    def decide(self, *, friendly: str, category: Category, mode: Mode,
               command: str | None, paths: tuple[str, ...]) -> Decision:
        # ① 黑名单（仅 command_exec）→ ② 沙箱（仅文件类）→ ③ 规则 → ④ 模式兜底
        # 返回 ALLOW / DENY / ASK（ASK 仅可能来自模式层；门据此调人在回路）
```

## 组件设计（C29–C39）

### C29 判定类型 + 危险命令黑名单 `permissions/decision.py` + `blacklist.py`（F41/N10）
- `decision.py`：纯枚举与 `Decision` dataclass（见数据结构）；`MODE_CYCLE` 给 Shift+Tab。
- `blacklist.py`：一组**内置正则**（模块级常量 `_DANGEROUS: tuple[re.Pattern, ...]`），覆盖：`rm -rf /`、`rm -rf ~`/`$HOME`、写块设备 `> /dev/sd*`、`dd of=/dev/...`、fork 炸弹 `:(){ :|:& };:`、`mkfs.*`、`> /dev/disk*` 等已知高危模式。`check_command(command: str) -> Decision | None`：命中任一正则 → `Decision(DENY, BLACKLIST, 可读原因)`；否则 None（继续下一层）。**无任何开关/配置参数**——硬编码不可关（N10）。明确文档化为启发式、非完备。

### C30 路径沙箱 `permissions/sandbox.py`（F42/N11）
- `check_path(path: str, project_root: Path) -> Decision | None`：
  1. 规整为绝对路径（相对路径以 project_root 为基）；
  2. 目标存在 → `Path.resolve()`（解析符号链接）；目标不存在 → 逐级上溯到**最近的已存在祖先**再 `resolve()`，把未存在的尾段拼回；
  3. 前缀判断：解析后路径是否在 `project_root.resolve()` 之下（`is_relative_to`）；不在 → `Decision(DENY, SANDBOX, 可读原因)`，在 → None。
- **解析顺序固定**（先解析软链接再比对）防止软链接指向外部绕过。多路径工具（如 edit 可能两路径？实际六工具单路径为主）逐个 check，任一逃逸即 Deny。

### C31 规则引擎 `permissions/rules.py`（F43/F44 同层合并）
- 友好名 → 内置工具名映射（`_FRIENDLY: {"Bash":"run_command","Read":"read_file","Write":"write_file","Edit":"edit_file","Glob":"find_files","Grep":"search_text"}`；注：搜内容工具真实名为 `search_text`）；解析配置里的「友好名(模式)」字符串为 `Rule`。
- 匹配：精确（`pattern == target`）或 glob。文件类 `is_path=True`：用支持 `**` 跨目录的 glob（`fnmatch.translate` 改造或 `pathlib.PurePath.match` + `**` 处理）；命令类 `is_path=False`：`**` 等价 `*`（`fnmatch`）。`pattern is None` → 匹配该工具全部调用。
- `RuleSet.match`：同层内**先查 deny 命中、再查 allow 命中**（deny 优先于 allow，F44）；都没中返回 None。`LayeredRules.match`：local→project→user 逐层调 `RuleSet.match`，第一个非 None 即返回（本地盖项目盖用户，就近命中即止）。

### C32 三层配置加载 `permissions/settings.py`（F44/N14）
- YAML 形态（每层同构）：
  ```yaml
  defaultMode: default          # 可选；本地>项目>用户取首个出现
  permissions:
    allow: ["Bash(git *)", "Read"]
    deny:  ["Bash(git push)"]
  ```
- `load_settings(project_root)`：依次 `safe_load` 三文件 → 各构 `RuleSet`；文件缺失=空集；`yaml.YAMLError` 或结构非法（非 dict、permissions 非预期形）→ **该文件降级为空集**（不额外放权），其余层照常；`default_mode` 按 本地>项目>用户 取首个合法值，皆无 → `Mode.DEFAULT`。**绝不抛、绝不致构造失败**（N14）。

### C33 模式兜底表 `permissions/modes.py`（F45）
- 纯查表函数 `mode_fallback(mode: Mode, category: Category) -> Verdict`，实现 spec F45 矩阵（**值域严格 {ALLOW, ASK}，绝不产 DENY**）：
  ```
  default:     read_only→ALLOW  file_write→ASK    command_exec→ASK
  acceptEdits: read_only→ALLOW  file_write→ALLOW  command_exec→ASK
  plan:        read_only→ALLOW  file_write→ASK    command_exec→ASK
  bypass:      read_only→ALLOW  file_write→ALLOW  command_exec→ALLOW
  ```
- 表以 `dict[Mode, dict[Category, Verdict]]` 写死；新增一档只扩表（N18）。

### C34 五层流水线 `permissions/pipeline.py`（F46）
- `PermissionPipeline.decide(friendly, category, mode, command, paths)`：
  1. `category == COMMAND_EXEC` 且 `command` 非空 → `blacklist.check_command(command)`；命中 Deny 即返回（短路）。**非命令类跳过本层**。
  2. `category in (READ_ONLY, FILE_WRITE)` → 对每个 path 调 `sandbox.check_path`；任一 Deny 即返回。**命令类跳过本层**。
  3. 规则：`target = command if COMMAND_EXEC else 项目相对路径`；`self.settings.rules.match(...)` → ALLOW→`Decision(ALLOW, RULE)` 返回、DENY→`Decision(DENY, RULE, 原因)` 返回、None→继续。
  4. 模式兜底：`mode_fallback(mode, category)` → ALLOW→返回 Allow、ASK→返回 `Decision(ASK, MODE)`（交给门去人在回路）。
- 层 1-4 全纯函数、可独立单测；短路与跳层语义即 F46/AC48。

### C35 Tool 元数据 + executor 去确认门 `tools/*.py`（F43/F45 分类、F26 取代）
- `tools/base.py` 的 `Tool` 增字段：`category: Category`、`friendly_name: str`、以及「从 arguments 抽取命令串 / 路径列表」的声明（最简：`command_arg: str | None`、`path_args: tuple[str, ...]`）。`requires_confirmation` 改为**派生属性**（`category != READ_ONLY`）——`batch.classify` 既有逻辑零改、向后兼容。
- 六工具各声明：read_file/find_files/search_text→READ_ONLY；write_file/edit_file→FILE_WRITE；run_command→COMMAND_EXEC；并各带 friendly_name 与参数抽取声明。
- `tools/executor.py`：**删 `confirm` 参数与确认门分支**（F26 被五层取代），`execute` 回归「解析→（无确认门）→超时执行」；既有 `denied` 字段语义保留但不再由 executor 产生（改由 loop 的人在回路 Deny 产生）。更新 executor 测试。

### C36 AgentLoop 判定门接入 `agent/loop.py`（F46/F49）
- `AgentLoop.__init__` 增可选 `permission_gate: Callable[[ToolCallEvent], Awaitable[object | None]] | None = None`（duck-typed async；None ⇒ v0.5 行为，回归安全）。
- **门契约（保 agent 层 import 纯净）**：`permission_gate(call) -> outcome | None`——**返回 None = 放行**（loop 继续进 executor）；**返回非 None = 拒绝**：返回的是一个**已成形的拒绝结果对象**（鸭子兼容 `ToolOutcome`/`_BlockedOutcome`：含 `call_id`/`name`/`content`/`is_error=True`/`denied`），loop **原样回灌、不进 executor**。Allow 与 Ask 都在门内消化（层 1-4 纯算；Ask→人在回路→最终放行返 None / 拒绝返成形对象）；content 已由门按来源（黑名单/沙箱/规则/人在回路拒绝）措辞好、`denied` 由门按是否人在回路拒绝置位。**loop 不 import permissions、不解释 verdict/source、不合成 outcome**——零权限概念泄漏。
- `run_call` 改造（在既有 blocked 判定之后、executor 之前）：
  ```
  if classify == blocked: return _make_blocked_outcome(call)   # 计划模式过滤，保留
  if permission_gate is not None:
      denied = await permission_gate(call)                     # 层1-4纯算 + 层5人在回路（门内完成）
      if denied is not None:                                   # 非 None = 成形的拒绝结果
          return denied                                        # 原样回灌、不进 executor
  return await call_in_thread(executor.execute, ...)           # 放行(None)才执行
  ```
- 拒绝结果对象的构造在**装配层**（gate 闭包，见 C37）：它知道 `Decision.source`，按来源生成 content 与 `denied` 标志（人在回路拒绝→`denied=True`，黑名单/沙箱/规则拒绝→`denied=False`、均 `is_error=True`）。**保序**：denied 结果与放行结果一样按原调用序、原 call.id 配对入史（沿用 v0.4 `results` 收集，AC51）。
- **门是 async**：只读类在门内层 1-4 即放行（同步返 None、不 await UI），故只读并发 wave 零阻塞（AC53）。

### C37 人在回路 UI + ask 回调 + 永久落盘 `ui/confirm.py` + `repl.py`（F48）
- `ui/confirm.py`：基于 prompt_toolkit（仿 `ui/select.py`）的三选一审批组件——多行块（工具名 + 关键参数预览 + 触发原因 + 三选项菜单），**↑↓ 移光标 + 回车，数字键 1/2/3 直选，默认高亮「允许本次」**；Esc/Ctrl+C 取消（抛 `Cancelled`，由 REPL 干净结束本轮，N13）。返回 `{ALLOW_ONCE, ALLOW_ALWAYS, DENY}`。
- REPL 构造 `ask` 回调（注入 pipeline 包成的 gate）：Ask 时调 `ui.confirm` →
  - ALLOW_ONCE → `Decision(ALLOW, HUMAN)`；
  - ALLOW_ALWAYS → 写**精确**规则（命令串/项目相对路径）到 `<根>/.wentian/settings.local.yaml` 的 `permissions.allow`（在内存 LayeredRules 也即时追加，本会话即生效）→ `Decision(ALLOW, HUMAN)`；
  - DENY → `Decision(DENY, HUMAN, 原因)`。
- gate 闭包（在 cli/repl 装配）：`async gate(call)`：取 tool 的 category/friendly/抽取 command·paths → `pipeline.decide(..., mode=当前模式)` → 若 ASK 则 `await ask(call, decision)` 解析为终值 → 返回 Decision。**只读永不到 ASK 分支**。

### C38 Shift+Tab 模式切换 + 状态栏 `ui/input.py` + `repl.py`（F47）
- `ui/input.py` `_build_key_bindings` 增 `@kb.add("s-tab")`：调注入的 `on_mode_cycle` 回调（PromptInput 新增可选属性，仿 `status_provider`）→ REPL 把 `self._mode` 推进到 `MODE_CYCLE` 下一档并刷新 bottom toolbar。Shift+Tab 在输入提示符处生效（回合之间），模式存于 REPL 状态 → **天然跨轮保持**（AC49）。
- `repl.status_line`：**首段由 provider:model 改为当前权限模式**（占原位、不再显示 provider 名，F47）：`{mode.value} │ 会话 {id} │ {n} 条消息`。
- **plan 统一**：既有 `self._plan_mode: bool` 收编为 `self._mode == Mode.PLAN` 的派生——`/plan`→`self._mode=PLAN`、`/do`→`self._mode=DEFAULT`（固定回 default，不恢复旧档）、Shift+Tab 循环亦可达 PLAN。原 F33 机制（声明过滤仅三只读、`allowed_tools` 传 loop、计划提醒经 `<system-reminder>`）全部改 key 于 `mode==PLAN`，行为不变（AC47 plan 行、AC49、回归 AC40）。原状态栏「│ 计划模式」后缀可去（plan 已在首段显示）或保留为冗余提示——实现期定，回填补注。

### C39 装配 `cli.py` + `pyproject.toml` + `.gitignore`（F44/N17）
- `cli.build_app`：`Path.cwd()` 作 project_root → `load_settings(root)` → `PermissionPipeline(root, settings)`；构 `ui.confirm` 的 ask 回调（interactive 时；非交互/非 TTY → ask 恒 DENY，安全默认 N16）；包成 `gate` 注入 `AgentLoop(..., permission_gate=gate)`。初始模式 = `settings.default_mode`。删 `_make_confirm` 与 executor 的 confirm 注入。
- `.gitignore` 增 `.wentian/settings.local.yaml`（AC57 不泄漏）。版本 `0.6.0`（源码 + pyproject + lock 同步）；零新增第三方依赖（yaml 已在用）。

## 模块交互（一次工具调用的判定数据流，v0.6 视角）

```
cli.build_app: load_settings(cwd) → Settings(LayeredRules, default_mode)
               PermissionPipeline(cwd, settings); ask=ui.confirm 回调
               gate = 闭包(pipeline, registry, ask, lambda:repl._mode)
               AgentLoop(..., permission_gate=gate); repl._mode=default_mode
AgentLoop.run 每轮 → 工具阶段 partition_waves → run_wave → run_call(call):
  if blocked(plan 声明过滤): _make_blocked_outcome          # F33 保留
  decision = await gate(call):
     tool=registry.get(name); category/friendly/command/paths ← tool 元数据
     d = pipeline.decide(friendly, category, mode=repl._mode, command, paths)
        ①黑名单(命令类) →②沙箱(文件类) →③规则(local>proj>user, deny>allow) →④模式兜底
     if d.verdict==ASK: choice = await ask(call, d)          # 只读到不了这；串行 wave 才可能
        ALLOW_ONCE→Allow / ALLOW_ALWAYS→写 local.yaml+内存+Allow / DENY→Deny(HUMAN)
     return d
  if DENY: _make_denied_outcome(call, d)  → 回灌(按 source 措辞)、不进 executor   # F49
  else:    executor.execute(...)          → 正常执行+超时
  结果按原调用序、原 call.id 配对入史（denied 与 allow 各自独立、不串位）          # AC51
```

## 测试策略（v0.6 增量，离线为主）

| 组件 | 测法 | 关键用例 |
|------|------|----------|
| permissions/blacklist | 纯函数断言 | `rm -rf /`/变体/`dd of=/dev`/fork 炸弹/`mkfs` 命中 Deny；普通命令 None；无任何开关可关（AC41）|
| permissions/sandbox | 临时目录 + 软链接 | 项目内放行；`/etc/passwd`/`../outside` Deny；软链接指向外部 Deny（先解析）；新建文件+未创建多级中间目录放行（AC42）|
| permissions/rules | 纯函数断言 | `Bash(git status)` 精确、`Bash(git *)` glob、`Write(src/**)` 跨目录、命令串 `**`≡`*`；友好名路由六工具；同层 deny>allow（AC43/AC44）|
| permissions/settings | 临时三文件 | 三层 defaultMode 优先级；local>project>user 合并；缺失=空；YAML 非法/结构错→降级空集不抛（AC45/AC46/AC58）|
| permissions/modes | 查表断言 | 四档×三类矩阵逐格；值域恒 {Allow,Ask} 绝不 Deny（AC47）|
| permissions/pipeline | 装配 + 假 settings | 五层短路：黑名单命中不进沙箱；deny 规则不进模式；allow 规则不进兜底；跳层不误拦（非命令不被黑名单、命令不被沙箱）（AC48）；安全默认（类别不明按副作用）（AC55）|
| tools/base + 六工具 | 元数据断言 | 各工具 category/friendly_name 正确；requires_confirmation 派生与 classify 兼容；参数抽取出 command/paths |
| tools/executor | 既有 + 去门 | 删 confirm 后纯执行+超时；既有健壮性测试迁移保持绿 |
| agent/loop | ScriptedProvider + 假 gate | gate=None 行为与 v0.5 全等（回归）；Deny 合成回灌按 source 措辞、不进 executor；保序+call.id 配对（denied 与 allow 混批不串位）（AC51）；只读 wave 并发不被门串行化（假 gate 对只读同步返回 Allow）（AC53）|
| ui/confirm | prompt_toolkit pipe input | ↑↓/数字键三选；默认高亮允许本次；Esc/Ctrl+C 取消干净（AC50/AC52）|
| repl | ScriptedProvider + recording | 状态栏首段显权限模式（不显 provider 名）；Shift+Tab 循环四档跨轮保持；/plan·/do 仍进出 plan（/do 回 default）；plan 统一后 F33 回归全绿（声明过滤/blocked/提醒）（AC49/AC47/AC40）；永久→写 local.yaml 且重载生效（AC50）|
| cli | 既有注入点 | build_app 注入 gate + 初始模式=default_mode；非交互 ask 恒 Deny（安全默认）；版本 0.6.0；.gitignore 含 settings.local（AC57/AC58）|
| 端到端（联网/真终端） | checklist 人工场景 | AC41 真跑被拦 / AC50 真终端三选一 / AC54 双后端一致 / AC49 Shift+Tab 眼见切档 |

## v0.6 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 判定门位置 | **上移到 AgentLoop `run_call`**（用户拍板） | spec 要求「判定在 agent 编排层、provider 无关」；loop 已有 `_BlockedOutcome` 回灌不中断范式；Ask 只发生在串行 wave、不破坏只读并发；executor 回归纯执行 |
| 权限引擎形态 | 独立**纯包** `permissions/`（leaf，stdlib+yaml） | N15/N18：零 SDK/rich/prompt_toolkit，可独立单测每层；agent 层 duck-typed 注入，分层铁律不破 |
| 配置文件 | `~/.config/wentian/settings.yaml` + `<根>/.wentian/settings(.local).yaml`（用户拍板） | Claude Code 风格；与 provider `config.yaml` 分离互不污染；local 层 gitignore 放敏感放行 |
| executor 确认门 | **删除**（F26 被五层取代） | 五层流水线统一在 loop 层判定；executor 单一职责（执行+超时）；`requires_confirmation` 降为派生属性、classify 零改 |
| Tool 分类来源 | Tool 自带 `category`/`friendly_name`/参数抽取声明 | 工具专属知识留在 tools 层（高内聚）；引擎保持通用、经 duck-typed registry 读取 |
| 三层合并语义 | 不扁平化，判定时逐层 match（就近命中即止，同层 deny>allow） | 精确实现「本地>项目>用户」+「同层 deny 优先」；扁平化会丢层级语义 |
| plan 模式统一 | 既有 `_plan_mode` 收编为 `mode==PLAN` 一档 | Shift+Tab 四档循环含 plan；F33 机制全部 re-key 于 mode==PLAN，行为零损；/plan·/do 仍作专用入口出口 |
| 黑名单不可绕过 | 硬编码正则、无任何参数/开关、流水线第一层 | N10 红线；bypass 模式也拦得住；启发式非完备、文档化边界 |
| 沙箱解析顺序 | 先 resolve 软链接再前缀比对；新建按最近祖先 | N11 防软链接逃逸；不因目标不存在误判 |
| 非交互/非 TTY 的 Ask | ask 恒判 Deny（安全默认） | N16：管道/CI 无人确认时不静默放行；与 v0.4 非 TTY confirm=False 一脉相承 |
| 永久规则 | 精确匹配、写 local 层、内存即时生效 | F48/不做自动泛化；跨会话靠落盘、本会话靠内存追加 |
| gate=None 回退 | AgentLoop 不传 gate ⇒ v0.5 行为 | 回归安全；既有 loop 测试零破坏 |

## v0.6 风险与边界

1. **R1 黑名单非完备**：启发式正则只覆盖已知高危模式，无法穷尽（如混淆变形、环境变量拼接的危险命令）；文档化为「启发式防御」，真正兜底靠沙箱（文件围栏）+ 模式 Ask（命令默认要确认）。不追求完备是设计选择，非缺陷。
2. **R2 沙箱不管 Bash 内的文件访问**：命令执行不走沙箱，`run_command` 内的 `cat /etc/passwd` 沙箱拦不住；交给黑名单（危险写）+ 规则 + 模式 Ask 兜底。已知边界。
3. **R3 人在回路在 async 循环中的交互**：Ask 时需暂停循环、在主线程跑 prompt_toolkit 审批 UI；须确保 await ask 期间事件循环不卡死、Esc/Ctrl+C 干净取消不泄漏 task（N13）。仿 v0.2 中断与 select.py 模式，列为评审 + 联网检查点。
4. **R4 OpenAI 兼容端一致性**：权限判定在 agent 层、provider 无关，理论上双端一致；须有一条用例同时用 anthropic-script 与 openai-script provider 跑同一判定断言相等（AC54），防止意外把判定写进某 provider 分支。
5. **R5 settings.local.yaml 泄漏**：本地层可能含敏感放行（甚至误写密钥）；必须 gitignore（AC57）且配置回显/日志不打印其内容。列为评审检查点。
6. **R6 状态栏去 provider 名的信息损失**：F47 要求权限模式占原 provider 位、不再显 provider 名；用户将无法在状态栏一眼看到当前后端。已按 spec 执行（provider 仍可经 `/provider` 查/切、横幅启动时显示）；记为 spec 拍定的取舍。
7. **R7 glob `**` 的命令串语义**：命令串里 `**` 退化为 `*`（不解释跨目录），仅文件路径用跨目录语义；须在 rules 测试明确两路径分支，防止命令规则被 `**` 误扩。

# v0.7 新增设计（F50–F55：MCP 客户端接入）

> 技术方向：新增 `src/wentian/mcp/` **纯包**（stdlib-only：`subprocess` / `urllib` / `threading` / `json` / `queue` / `shlex`，唯一跨层 import 是适配子类 import `wentian.tools.base` 的 `Tool`/`ToolError` 与 `wentian.providers.base` 的 `Category`——与内置工具同规）。协议层手搓 JSON-RPC 2.0 + 两种传输（stdio 子进程 / Streamable HTTP+SSE），**不引入第三方 MCP SDK 或 HTTP 库**（N19）。客户端为**同步线程模型**：每个 Server 一条持久连接，传输层起一个**后台读取线程**把收到的消息按 `id` 投递到对应**等待槽**；`MCPTool.run()` 同步发请求、阻塞等回包——天然嵌进 v0.4 既有「工具跑在工作线程」模型，多个只读 MCP 工具并发调用同一 Server 不串位（N20）。配置层把 `load_config` 升级为**用户级 + 项目级两层深合并**（providers 与 mcpServers 都适用），新增 `mcpServers` 块解析 + `${VAR}` 展开（F50）。装配在 `cli.build_app`：注册六内置工具后调 `MCPManager.discover_and_register`，逐 Server 连接/握手/列工具/建适配器/注册，**单 Server 失败只告警跳过**（N21）；manager 持有连接、随 REPL 退出统一关闭。版本升 `0.7.0`，零新增第三方依赖。

## 架构增量

```
mcp/__init__.py ──► 新纯包（leaf；被 cli 装配，适配子类被 registry/loop 经 Tool 抽象调用）
mcp/protocol.py ──► JSON-RPC 2.0 编解码：build_request/notification、parse_message、id 分配（C40）
mcp/transport.py ──► Transport ABC + StdioTransport（subprocess 管道）+ HttpTransport（urllib+SSE）；各起后台读取线程（C41）
mcp/client.py ──► MCPClient：initialize/list_tools/call_tool；id→等待槽 配对、线程安全 send、超时（C42）
mcp/adapter.py ──► MCPTool(Tool)：远端工具描述→统一工具；run() 调 client.call_tool、content 拼文本、错误转 ToolError（C43）
config.py ──► load_config 升两层深合并 + MCPServerConfig（stdio/http）+ ${VAR} 展开 + mcpServers 校验（C44）
mcp/manager.py ──► MCPManager.discover_and_register：逐 Server 连接/握手/列工具/建适配器/注册 + 故障隔离 + 生命周期（C45）
cli.py ──► build_app 调 manager 发现注册；REPL 退出时 manager.close_all；版本 0.7.0（C46）
```

- **分层依赖延续铁律**：`mcp/` 包是 leaf——只 import stdlib；`adapter.py` 额外 import `wentian.tools.base`（Tool/ToolError）与 `Category`（**与 `Tool.category` 同源**——v0.6 落在 `wentian.permissions.decision`）以产出统一工具（与六内置工具同规，合法）。**agent 层零改**：MCP 工具经统一 `Tool` 抽象进 registry，AgentLoop / executor / 权限门把它当普通工具，对「远端」无感（N23）。**provider 层零改**：远端工具的 `ToolSpec` 与内置工具走同一声明路径。
- **核心不变量（v0.7 新增）：① MCP 包内部不引入 asyncio**——全同步 + threading，嵌进既有工作线程模型（N20）；**② 任一 Server 失败绝不致启动失败 / 会话中断**——发现期 try/except 跳过、调用期转 ToolError（N21）；**③ 无 mcpServers 配置时零行为变化**——manager 发现到空列表即 no-op，两层加载在仅用户文件时等价旧单文件（N23）。
- **同步线程配对**：传输层后台读取线程读到一条 JSON-RPC 消息 → 若带 `id` 且命中 pending 等待槽（`threading.Event` + 结果位）→ 填结果并唤醒发起线程；通知（无 id）走单独回调/丢弃。`MCPTool.run()`（在 executor 工作线程内）`client.call_tool` → 分配 id → 注册等待槽 → 写请求 → `event.wait(timeout)` → 取回结果。多只读工具并发各占独立等待槽，互不串位。

## 核心数据结构（v0.7 新增）

```python
# config.py —— MCP Server 配置（两型）
@dataclass(frozen=True)
class StdioServerConfig:
    name: str
    command: str
    args: list[str]
    env: dict[str, str]          # 已做 ${VAR} 展开；叠加到子进程环境
@dataclass(frozen=True)
class HttpServerConfig:
    name: str
    url: str
    headers: dict[str, str]      # 已做 ${VAR} 展开
MCPServerConfig = StdioServerConfig | HttpServerConfig

@dataclass
class Config:                    # 既有，扩字段
    providers: dict[str, ProviderConfig]
    default: str
    mcp_servers: dict[str, MCPServerConfig]   # v0.7 新增；无配置时空 dict

# mcp/protocol.py —— JSON-RPC 2.0 纯编解码
def build_request(method: str, params: dict | None, *, id: int) -> dict
def build_notification(method: str, params: dict | None) -> dict
def parse_message(raw: dict) -> Response | Notification | None   # 区分回应/通知
@dataclass(frozen=True)
class Response:
    id: int
    result: dict | None
    error: dict | None           # JSON-RPC error 对象 {code,message,data}
@dataclass(frozen=True)
class Notification:
    method: str
    params: dict | None

# mcp/transport.py —— 传输抽象
class Transport(ABC):
    def start(self) -> None: ...                 # 拉起子进程/打开 HTTP；起后台读取线程
    def send(self, message: dict) -> None: ...    # 写一条 JSON-RPC（线程安全）
    def set_on_message(self, cb: Callable[[dict], None]) -> None: ...  # 收到消息回调（读取线程调）
    def close(self) -> None: ...                  # 终止子进程 / 关 HTTP；停读取线程
class StdioTransport(Transport): ...              # subprocess + 按行分帧 + stderr 诊断
class HttpTransport(Transport): ...               # urllib POST + 即时 JSON / SSE 事件流解析

# mcp/client.py —— 会话客户端
@dataclass(frozen=True)
class RemoteTool:                # 一条 tools/list 条目
    name: str
    description: str
    input_schema: dict
    read_only: bool              # 取自 annotations.readOnlyHint，缺省 False
class MCPClient:
    def __init__(self, transport: Transport, *, timeout_s: float = 30.0) -> None
    def initialize(self) -> dict          # 三步①：握手 + 发 initialized 通知
    def list_tools(self) -> list[RemoteTool]   # 三步②
    def call_tool(self, name: str, arguments: dict) -> str   # 三步③：取 result.content 拼文本
    def close(self) -> None
    # 内部：_next_id()、_pending: dict[int, _Waiter]、_on_message 路由（按 id 唤醒）

# mcp/adapter.py —— 统一工具适配（Category 与 Tool.category 同源：v0.6 permissions.decision）
class MCPTool(Tool):
    name: str                    # f"{server_name}__{remote.name}"（命名空间）
    description: str             # remote.description
    parameters: dict             # remote.input_schema
    category: Category           # READ_ONLY if remote.read_only else FILE_WRITE（默认有副作用）
    def run(self, args: dict) -> str   # client.call_tool；传输/远端错 → raise ToolError

# mcp/manager.py —— 发现 + 生命周期
class MCPManager:
    def discover_and_register(self, servers: dict[str, MCPServerConfig],
                              registry: ToolRegistry) -> DiscoveryReport
        # 逐 Server：建 transport→MCPClient→initialize→list_tools→MCPTool→registry.register
        # 任一步异常：记 report.failed[name]=reason，跳过；成功：缓存 client、report.ok[name]=工具数
    def close_all(self) -> None   # 关闭所有缓存 client/transport
@dataclass
class DiscoveryReport:
    ok: dict[str, int]            # server → 注册工具数
    failed: dict[str, str]        # server → 失败原因
```

## 组件设计（C40–C46）

### C40 JSON-RPC 2.0 编解码 `mcp/protocol.py`（F51）
- 纯函数 + 不可变 dataclass，无 IO、无线程：`build_request(method, params, id)` 产 `{"jsonrpc":"2.0","id":id,"method":...,"params":...}`（params 为 None 时省略）；`build_notification` 同形但无 `id`。`parse_message(raw)`：含 `id` 且含 `result`/`error` → `Response`；含 `method` 无 `id` → `Notification`；否则 None（容错，配合降级）。id 分配交给 client（protocol 不持状态）。错误对象按 JSON-RPC 规范保留 `{code,message,data}`。
- 离线纯单测：构造/解析往返、error 分支、通知分支、畸形消息返 None。

### C41 两种传输 `mcp/transport.py`（F52）
- `Transport` ABC 定义 `start/send/set_on_message/close` 四方法；上层 client 只依赖这四个，对 stdio/http 无感。
- **StdioTransport**：`subprocess.Popen([command, *args], stdin=PIPE, stdout=PIPE, stderr=PIPE, env={**os.environ, **cfg.env}, text=True)`。后台读取线程逐行读 stdout → `json.loads` → `on_message`；另起线程把 stderr 抽干到诊断（不参与协议、避免管道阻塞）。`send` 写一行 JSON + `\n` + flush（加锁保多线程写安全）。`close` 终止子进程（terminate→等→kill 兜底）、join 线程。
- **HttpTransport**：`send` 用 `urllib.request` POST JSON 到 `cfg.url`，带 `cfg.headers` + `Content-Type: application/json` + `Accept: application/json, text/event-stream`。响应按 `Content-Type` 分流：`application/json` → 直接 `json.loads` 投 `on_message`；`text/event-stream` → 后台读取线程逐行解析 SSE（`data:` 行累积、空行分隔事件、`[DONE]`/连接关闭结束），每个事件 `json.loads` 投 `on_message`。`close` 关闭打开的响应流。（本版每次 send 一来一回即可满足三步会话；SSE 用于 Server 以事件流回多帧的情形。）
- 测试：stdio 用 stdlib 写的假 server 脚本（读 stdin 行、回 JSON 行）；http 用 `http.server.HTTPServer` 起本地假 server，分别返 JSON 与 SSE。

### C42 会话客户端 `mcp/client.py`（F51/N20）
- `MCPClient(transport, timeout_s)`：`start` transport、`set_on_message(self._route)`。`_route(raw)`：`parse_message` → `Response` 命中 `_pending[id]` 的 `_Waiter`（填 result/error、`event.set()`）；`Notification` 暂存/忽略（本版不需服务端通知）。
- `initialize()`：发 `initialize` 请求（带本端 protocolVersion + capabilities + clientInfo）、等回应取服务端能力；随后发 `notifications/initialized` 通知（无 id、不等）。
- `list_tools()`：发 `tools/list`、解析 `result.tools[]` → `RemoteTool`（`read_only = tool.get("annotations",{}).get("readOnlyHint", False)`）。
- `call_tool(name, arguments)`：发 `tools/call`（params `{name, arguments}`）、等回应；`error` 非空或 `result.isError` 为真 → raise（由 adapter 转 ToolError）；否则把 `result.content[]`（text 块）拼成文本返回。
- **id 配对线程安全**：`_next_id` 用锁自增；`_pending: dict[int, _Waiter]`；`_send_request` 注册 waiter→`transport.send`→`event.wait(timeout_s)`→超时 raise（清理 waiter）。多线程并发调用各自独立 id/waiter（N20）。`close` → `transport.close`、唤醒所有挂起 waiter 报错。
- 测试：假 transport（直接喂预设回包）断言三步；**乱序回包**仍按 id 配对（AC61）；超时分支；error 分支。

### C43 统一工具适配 `mcp/adapter.py`（F53/F54）
- `MCPTool(Tool)`：构造时定 `name = f"{server_name}__{remote.name}"`、`description = remote.description`、`parameters = remote.input_schema`、`category = READ_ONLY if remote.read_only else FILE_WRITE`（默认有副作用，F54；`requires_confirmation` 经 v0.6 派生属性 = `category != READ_ONLY` 自然为真）。`timeout_s` 取 client 超时或默认。
- `run(args)`：`return self._client.call_tool(self._remote.name, args)`；捕获传输/协议/远端错误 → `raise ToolError(可读原因)`（F53/N21：不崩溃，错误回灌）。注意调用时用**原始远端工具名**（去命名空间前缀），命名空间只用于 registry 键与模型可见名。
- 测试：name 命名空间、schema 透传、readOnlyHint→category/requires_confirmation 映射（AC64）、content 拼接、错误转 ToolError。

### C44 两层配置加载 + MCP 配置 `config.py`（F50/N23）
- `load_config` 升级为**两层深合并**：先读用户级（`~/.config/wentian/config.yaml` 或 `$XDG_CONFIG_HOME/...`），再读项目级（`<cwd>/.wentian/config.yaml`，缺失即跳过）；对 `providers` 与 `mcpServers` 两个 map 做**逐键深合并**（项目级同名键覆盖、新键并入），顶层标量（如 `default`）项目级存在则覆盖。仅用户级存在时结果等价旧单文件加载（N23 向后兼容）。保留显式 `path` 入参语义（单文件直载、不触发两层，供测试与 `-c` 用）。
- `mcpServers` 解析：每条按有无 `command`/`url` 判 stdio/http；`StdioServerConfig`（command 必填、args 默认 []、env 默认 {}）/`HttpServerConfig`（url 必填、headers 默认 {}）；类型缺字段 → `ConfigError`（字段级消息）。
- `${VAR}` 展开：对 env/headers 的**值**做 `${VAR}` 替换（用 `string.Template` 或正则），变量取 `os.environ`；缺失 → 空串 + 一条 `warnings`/日志告警（F50：不因缺变量启动失败）。展开在加载期完成，下游拿到的是终值。
- 测试：两层合并（覆盖/并入/向后兼容）、stdio/http 解析、`${VAR}` 展开与缺失告警、字段缺失报错（AC60）。

### C45 发现 + 生命周期 `mcp/manager.py`（F55/N21）
- `discover_and_register(servers, registry)`：遍历 `servers`，每个 try：按类型建 `StdioTransport`/`HttpTransport` → `MCPClient` → `initialize()` → `list_tools()` → 每个 `RemoteTool` 建 `MCPTool` → `registry.register`（撞名时命名空间已隔离；万一仍冲突记 warning 跳过该工具）；成功则缓存 `self._clients[name]=client`、`report.ok[name]=len(tools)`。except（任何异常含超时）：`report.failed[name]=str(exc)`，确保已建的 transport 被 close（不泄漏子进程），继续下一个 Server（N21 故障隔离）。
- `close_all()`：对所有缓存 client `close()`（终止子进程 / 关 HTTP），幂等。
- 连接发现可加整体或单 Server 超时（用 client 的 timeout；stdio 握手卡死靠超时兜底）。
- 测试：两 Server 一坏一好（坏的 command 不存在 / 握手不回）→ 坏跳过 report.failed、好正常注册 + report.ok；close_all 终止子进程（断言进程结束）；空 servers → no-op（AC65/AC66）。

### C46 装配 `cli.py` + `pyproject.toml`（F55/N23）
- `build_app`：`load_config()`（两层）拿到 `cfg.mcp_servers` → 建 `MCPManager` → 注册六内置工具后 `report = manager.discover_and_register(cfg.mcp_servers, registry)` → 以横幅/日志可见汇报 `report`（成功 Server + 工具数、失败 Server + 原因）；无 mcpServers → 跳过、零输出（N23）。把 `manager` 交给 REPL（或 build_app 返回时登记），**REPL 退出路径调 `manager.close_all()`**（含异常退出，用 try/finally 或 atexit 兜底，防子进程泄漏）。
- 版本 `0.7.0`（源码 `__init__` + pyproject + lock 同步）；零新增第三方依赖。
- 测试：build_app 在有/无 mcpServers 下装配正确；冒烟（无配置）行为同 v0.6；manager.close_all 在退出被调用（AC66）。

## 模块交互（一次 MCP 工具调用的数据流，v0.7 视角）

```
cli.build_app: cfg = load_config()  # 两层深合并：user ⊕ project（providers + mcpServers）
               registry ← 六内置工具
               manager = MCPManager()
               report = manager.discover_and_register(cfg.mcp_servers, registry):
                 for name, scfg in servers:
                   try: transport=Stdio/Http(scfg); c=MCPClient(transport)
                        c.initialize(); tools=c.list_tools()
                        for t in tools: registry.register(MCPTool(name, t, c))   # 名 = name__t.name
                        clients[name]=c; report.ok[name]=len(tools)
                   except e: transport.close(); report.failed[name]=str(e)        # 隔离，继续
               汇报 report；REPL 持有 manager（退出 → close_all）
模型请求工具 "fs__read_file" → AgentLoop.run_call（对远端无感）:
   classify(registry.get(name)) → category 来自 MCPTool（readOnlyHint? read_only : 副作用）
   权限门（v0.6）：MCP 工具不进规则路由 → 模式兜底（副作用→Ask / 只读→Allow）
   executor.execute → call_in_thread → MCPTool.run(args):     # 工作线程内（同步）
       client.call_tool("read_file", args):
          id=_next_id(); _pending[id]=waiter; transport.send(build_request("tools/call",{name,arguments},id))
          waiter.event.wait(timeout)      # 后台读取线程收到回包 → parse → 命中 id → 填结果 set()
          result.content[] → 拼文本返回 / error → raise ToolError
   结果按原 call.id 配对入史（与内置工具完全一致）
程序退出 → manager.close_all() → 各 transport.close()（terminate 子进程 / 关 HTTP），无残留
```

## 测试策略（v0.7 增量，离线为主）

| 组件 | 测法 | 关键用例 |
|------|------|----------|
| mcp/protocol | 纯函数断言 | request/notification 构造；response/notification/畸形解析往返；error 对象保留（F51）|
| mcp/transport(stdio) | stdlib 假 server 脚本 + subprocess | 端到端收发一行 JSON-RPC；stderr 不干扰协议；close 终止子进程（AC62）|
| mcp/transport(http) | `http.server` 本地假 server | 即时 JSON 响应 + SSE 事件流响应两分支；请求头被带上（AC62）|
| mcp/client | 假 transport（喂预设回包） | initialize+initialized 通知；list_tools 解析含 readOnlyHint；call_tool 取 content；**乱序回包按 id 配对**；超时；error 分支（AC61）|
| mcp/adapter | 构造 + 假 client | name 命名空间；schema 透传；readOnlyHint→category/requires_confirmation；content 拼接；错误转 ToolError（AC63/AC64）|
| config | 临时两层文件 | 两层深合并（覆盖/并入/仅用户向后兼容）；stdio/http 解析；`${VAR}` 展开与缺失告警；字段缺失 ConfigError（AC60）|
| mcp/manager | 假/真 stdio 假 server + 坏 server | 两 Server 一坏一好 → 隔离（坏 failed、好 ok+注册）；close_all 终止子进程；空 servers no-op（AC65）|
| cli/build_app | 既有注入点 | 有/无 mcpServers 装配；无配置冒烟同 v0.6；退出调 close_all；版本 0.7.0（AC66）|
| agent/loop（回归） | 既有 ScriptedProvider | MCP 工具经统一抽象进 registry，loop/executor/权限门无特判仍全绿（N23）|
| 端到端（可选联网/真 Server） | checklist 人工场景 | 接一个真实 stdio MCP server（如 filesystem）→ 模型多轮调用其工具完成任务（AC63）|

## v0.7 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 协议实现 | **stdlib 手搓** JSON-RPC + stdio + HTTP/SSE（用户拍板） | 本项目教学定位，协议内脏要讲透；零新增依赖；延续 tools/providers 的 stdlib 分层纪律（N19）|
| 并发模型 | **同步 + threading**（后台读取线程 + id→等待槽），不进 asyncio | Tool.run 同步、已在工作线程跑（v0.4 `call_in_thread`）；线程配对天然支持只读并发不串位（N20）；client 无需 asyncio 桥接 |
| 配置布局 | 同一 `config.yaml` + **全量两层深合并**（用户拍板） | providers 与 mcpServers 单一机制；项目级 `.wentian/config.yaml` 与 v0.6 `.wentian/settings.yaml` 同目录；仅用户文件时向后兼容（N23）|
| 工具命名空间 | `<Server名>__<工具名>` | 不与六内置工具（无 `__` 前缀）及跨 Server 撞名；模型可读；registry 唯一键 |
| 外部工具安全默认 | 默认 `category=FILE_WRITE`（有副作用），readOnlyHint=true 才 READ_ONLY（用户拍板） | fail-safe（N16）；复用 v0.6 派生 `requires_confirmation` 与模式兜底，MCP 工具零特判接入权限体系 |
| 故障处理 | 发现期 try/except 跳过 + 告警；调用期转 ToolError | 单 Server 不拖垮其余 / 不中断会话（N21）；无健康检查/重连（YAGNI，留后续）|
| HTTP 传输 | urllib + 手解 SSE（即时 JSON / 事件流双分支） | 不引第三方 HTTP 库（N19）；Streamable HTTP 两种响应形态都覆盖 |
| 生命周期 | manager 持连接、REPL 退出 close_all（try/finally + atexit 兜底） | 子进程不泄漏（N21）；连接进程内复用，不每次调用重连 |
| agent/provider 层 | **零改** | MCP 工具走统一 Tool/ToolSpec 抽象；loop/executor/权限门/持久化对远端无感（N23）|

## v0.7 风险与边界

1. **R1 stdio 子进程管道阻塞/僵死**：Server 不按行回、或 stderr 灌满管道会卡死读取线程。对策：stdout/stderr 各一读取线程抽干、send/recv 加超时、close 用 terminate→kill 兜底；列为评审 + 测试检查点（假 server 模拟不回包触发超时）。
2. **R2 Streamable HTTP 形态多样**：响应可能是即时 JSON、也可能是 SSE 多帧，部分 Server 还要求先 GET 建 SSE 通道。本版只覆盖「POST 请求→即时 JSON 或 POST 响应体为 SSE」两种最常见形态；更复杂的会话语义（独立 GET 流、session id 头）列为已知边界，必要时后续补。
3. **R3 ${VAR} 缺失语义**：缺失变量展开为空串可能让 Server 拿到空 token 而握手失败——但失败会被 F55 隔离为「该 Server 跳过 + 告警」，不崩溃；告警里提示疑似未设环境变量。记为取舍（宁可跳过不可启动失败）。
4. **R4 远端 content 非文本块**：`tools/call` 结果 content 可能含 image/resource 块；本版只拼 text 块、其余以占位说明（如 `[非文本内容已省略]`）。多模态结果留后续版本。
5. **R5 工具名命名空间与模型理解**：`server__tool` 命名加长工具名，极端情况下可能超出某些 provider 工具名长度限制或含非法字符。对策：命名空间分隔用安全的 `__`、必要时对远端名做合法化（替换非法字符）；列为评审检查点。
6. **R6 与 v0.6 权限门的交接**：v0.6 判定门 `classify` 读 `tool.requires_confirmation`/`category`，MCP 工具已声明 category，故天然走「副作用→Ask / 只读→Allow」；但 v0.6 沙箱/黑名单只认六内置工具的路径/命令抽取，MCP 工具不触发这两层（其参数无法静态解析）。这与 spec「MCP 工具仅经默认有副作用接入」一致，记为已知边界。
7. **R7 发现期串行连接拖慢启动**：逐 Server 串行 initialize 在 Server 多/慢时拖长启动。本版接受串行（简单、确定）；并发发现（线程池）属优化，留后续。每 Server 超时兜底防单个卡死拖垮整体启动时长。

# v0.8 新增设计（F56–F62：上下文管理 + 双层压缩）

> 技术方向：新增 `src/wentian/context/` **纯包**（stdlib-only：`os` / `pathlib` / `re`，唯一跨层 import 是类型用 `wentian.providers.base` 的 `Message`/`Usage`，且**鸭子类型**接受 provider，不 import 具体 provider）。压缩注入 AgentLoop 一个**写回式 per-round 钩子** `pre_round_compact(messages, last_usage)`——区别于 v0.5 既有**只读不写回**的 `request_decorator`（环境提醒通道）：前者**原地改写并持久化**历史，后者只在发出时包提醒、永不写回。每轮调用后端**之前**触发：先第一层预防（卸载超大工具结果到磁盘、只留预览+路径），再第二层兜底（逼近窗口时调当前后端把较早历史摘成八段结构化摘要、保留近期原文）。token 用近似估算（锚定上次 usage + 增量字符折算）；两家后端 `input_tokens` 语义差异由 **provider 层新增 `prompt_token_total(usage)` 接口**消化。`/compact` 手动触发（余量 3K）；摘要连续失败 3 次熔断。版本升 `0.8.0`，零新增第三方依赖。

## 架构增量

```
context/__init__.py ──► 新纯包（leaf；被 repl/cli 装配为 Compactor，注入 AgentLoop 的 pre_round_compact 钩子）
context/estimator.py ──► 近似 token 估算：estimate(prompt_total, new_messages, char_per_token)（纯函数）（C47）
context/offload.py ──► 第一层：扫描工具结果、超阈值存盘、替换预览+路径、幂等（C48）
context/summarizer.py ──► 第二层：切割边界 find_cut_index + 八段摘要 prompt + 调 provider 生成 + 解析（C49）
context/compactor.py ──► 编排：每轮先 L1 后 L2、估算锚点状态、熔断计数、拼摘要+边界消息（C50）
providers/base.py + anthropic.py + openai_compat.py ──► Provider.prompt_token_total(usage)（两家语义差异消化）（C47）
agent/loop.py ──► run() 新增 pre_round_compact 钩子（每轮流阶段前调、原地改写、跨轮传 last_usage）（C51）
config.py ──► ProviderConfig.context_window（可选）+ 顶层 ContextConfig（阈值/余量/比率，全可选带默认）（C51）
repl.py + cli.py ──► 装配 Compactor 注入 loop；/compact 命令（余量 3K，强制重试）；存最近轮 usage；版本 0.8.0（C52）
```

- **分层依赖延续铁律**：`context/` 包是 leaf——只 import stdlib + `wentian.providers.base`（仅 `Message`/`Usage` 类型，不 import 具体 provider）；compactor 经**鸭子类型**持有 provider（只用其 `stream` 与 `prompt_token_total`）与 registry 无关。**agent 层薄改**：loop 仅多一个可选钩子，钩子为 None 时与 v0.7 字节级等价（N25）。**provider 层薄改**：仅新增 `prompt_token_total` 一个方法（base 给默认实现、anthropic 覆盖加缓存字段），不改 `stream`。
- **核心不变量（v0.8 新增）：① 未注入压缩器 ⇒ 零行为变化**——loop 钩子 None、provider 新方法不被调用、context 包不被 import（N25）；**② 压缩切割后历史配对始终合法**——保留段绝不以 `tool` 消息开头、绝不拆散「assistant(含 tool_calls) ↔ 其全部 tool 结果」，送两家 `_convert_messages`/openai 转换均不产生 400（N25）；**③ 只卸载工具结果**——第一层绝不改写 user/assistant 文本（N27）；**④ 估算两家一致**——`prompt_token_total` 消化 Anthropic（input 不含缓存）与 OpenAI（prompt_tokens 含缓存）的语义差异（F56）。
- **估算锚点的状态管理**：Compactor 持 `_last_seen_len`（上轮 pre_round_compact 返回时的 `len(messages)`）。第 N 轮入钩子时 `messages` = 上轮 prompt 的消息 + 上轮新追加（assistant + tool 结果）；故 `messages[_last_seen_len:]` 恰为「锚点之后的新消息」。估算总量 = `provider.prompt_token_total(last_usage)` + 这些新消息的字符折算。钩子末更新 `_last_seen_len = len(messages)`。首轮 `last_usage=None` → 全量字符折算。

## 核心数据结构（v0.8 新增）

```python
# providers/base.py —— 真实 prompt token 总量（消化两家 input_tokens 语义差异）
class Provider(ABC):
    def prompt_token_total(self, usage: Usage) -> int:
        # base 默认：input_tokens 即视为完整 prompt（OpenAI 兼容 prompt_tokens 已含缓存读）
        return usage.input_tokens
# anthropic.py 覆盖：input 不含缓存读/写，需相加才是真实 prompt 总量
class AnthropicProvider(Provider):
    def prompt_token_total(self, usage: Usage) -> int:
        return (usage.input_tokens + usage.cache_read_input_tokens
                + usage.cache_creation_input_tokens)

# config.py —— 扩字段（全可选、缺省走内置默认）
@dataclass(frozen=True)
class ProviderConfig:            # 既有，扩一字段
    ...
    context_window: int | None = None     # 该后端上下文窗口；None → ContextConfig.default_window
@dataclass(frozen=True)
class ContextConfig:             # 顶层新块 context:；整块缺失 → 全默认
    default_window: int = 200_000         # provider 未配 context_window 时的兜底
    reserved_output: int = 64_000         # 输出预留（窗口 = 输入预算 + 输出）；默认对齐 anthropic max_tokens
    auto_margin: int = 13_000             # 自动触发安全余量
    manual_margin: int = 3_000            # /compact 手动触发余量
    recent_keep_tokens: int = 10_000      # 尾部保留原文目标 token
    recent_keep_min_messages: int = 5     # 尾部保留至少条数
    offload_single_tokens: int = 2_000    # 单条工具结果卸载阈值
    offload_round_sum_tokens: int = 8_000 # 单轮工具结果合计卸载阈值
    char_per_token: float = 3.5           # 增量字符折算比

# providers/base.py —— Message 扩内部标记（total=False，不发给后端、仅防重复卸载）
class Message(TypedDict, total=False):
    ...
    offloaded: bool              # v0.8：该工具结果已被第一层卸载，扫描时跳过

# context/estimator.py —— 纯函数
def char_estimate(messages: list[Message], char_per_token: float) -> int   # 各 content 字符 / 比率，向上取整
def estimate_total(prompt_total: int, new_messages: list[Message], char_per_token: float) -> int
    # = prompt_total + char_estimate(new_messages)

# context/offload.py —— 第一层（原地改写 messages，返回动作清单供日志/测试）
@dataclass(frozen=True)
class OffloadAction:
    tool_call_id: str
    path: str
    original_tokens: int
def offload_oversized(messages, *, artifacts_dir: Path, cfg: ContextConfig) -> list[OffloadAction]
    # 扫 role=="tool" 且未 offloaded 的消息：① 单条 est>single → 卸载；
    # ② 按「轮」分组（相邻 tool 消息为一组），组内合计 est>round_sum →
    #    组内按 est 降序挑最大依次卸载直到合计回落阈值下。
    # 卸载：原 content 写 artifacts_dir/f"tool-{id}.txt"；content← 预览+绝对路径+提示；offloaded=True；保留 is_error

# context/summarizer.py —— 第二层
def find_cut_index(messages, *, cfg: ContextConfig) -> int
    # 从尾部累计 char_estimate 直到 ≥recent_keep_tokens 且尾部条数 ≥min → 初始 cut；
    # 边界 snap：while cut<len and messages[cut]["role"]=="tool": cut+=1（把孤儿 tool 结果推进摘要区，
    #   保证保留段不以 tool 开头、不留孤儿 tool_result）；clamp 不摘空（至少留 min 条）
SUMMARY_SYSTEM = "...禁止调用任何工具；先写分析草稿再写正式摘要，草稿用完即弃；正式摘要包在 <final_summary>…</final_summary>，按八段组织..."
def summarize(provider, earlier: list[Message]) -> str
    # provider.stream(earlier + [用户指令消息], system=SUMMARY_SYSTEM, tools=None) 收集文本；
    # 抽取 <final_summary>（缺标记则取全文）；空/异常 → raise SummaryError
def build_compacted(summary_text: str, kept: list[Message]) -> list[Message]
    # 返回 [ {role:"user", content: 摘要+边界提示(合并一条)} ] + kept

# context/compactor.py —— 编排 + 熔断（有状态）
class Compactor:
    def __init__(self, provider, *, artifacts_dir: Path, context_window: int, cfg: ContextConfig)
    def compact(self, messages: list[Message], last_usage: Usage | None, *, manual: bool = False) -> CompactionResult
        # 1) L1: offload_oversized(messages)
        # 2) est = estimate_total(provider.prompt_token_total(last_usage) if last_usage else 0,
        #          messages[_last_seen_len:], cfg.char_per_token)；last_usage None → 全量字符
        # 3) threshold = context_window - cfg.reserved_output - (manual_margin if manual else auto_margin)
        #    if est>threshold and (manual or not _tripped):
        #        try: cut=find_cut_index; summary=summarize(provider, messages[:cut]);
        #             messages[:] = build_compacted(summary, messages[cut:]); _fail=0; if manual:_tripped=False
        #        except SummaryError: _fail+=1; if _fail>=3:_tripped=True
        # 4) _last_seen_len = len(messages); return CompactionResult(offloaded=..., summarized=bool, tripped=_tripped, ...)
@dataclass(frozen=True)
class CompactionResult:
    offloaded: list[OffloadAction]
    summarized: bool
    estimated_tokens: int
    tripped: bool
    failed_this_call: bool
```

## 组件设计（C47–C52）

### C47 token 估算 + provider 总量接口 `context/estimator.py` + `providers/*`（F56）
- `estimator.py` 纯函数、无 IO：`char_estimate` 把每条消息的 `content`（含 tool_calls 的 arguments 序列化长度可粗算入）字符数累加 / `char_per_token` 向上取整；`estimate_total = prompt_total + char_estimate(new_messages)`。
- `Provider.prompt_token_total(usage)`：base 默认 `return usage.input_tokens`（OpenAI 兼容 `prompt_tokens` 已含 cached_tokens，不重复加）；`AnthropicProvider` 覆盖为 `input + cache_read + cache_creation`（Anthropic `input_tokens` 不含缓存读/写）。**这是修正点**：核验真代码确认两家语义不同（anthropic.py:87 input 单列缓存；openai_compat.py:296 prompt_tokens 含 cached）。
- 离线纯单测：两家 Usage 构造断言总量；增量只折算新消息；字符比可配；空消息=0。

### C48 第一层·超大工具结果卸载 `context/offload.py`（F57/N27）
- `offload_oversized(messages, artifacts_dir, cfg)` 原地改写：先按相邻 `role=="tool"` 分组为「轮组」；单条闸：任一未 `offloaded` 的 tool 消息 `char_estimate([m])>offload_single_tokens` → 卸载；单轮闸：组内合计 `>offload_round_sum_tokens` → 组内按 est 降序挑最大依次卸载直到合计回落。
- 卸载动作：`artifacts_dir.mkdir(parents=True, exist_ok=True)`；原 `content` 写 `tool-{tool_call_id}.txt`（绝对路径）；`content` ← `f"{preview}\n\n[完整输出已存盘：{path}（约 {tokens} tokens）。需要细节请用读文件工具读取该路径，勿照预览推断。]"`；`m["offloaded"]=True`；`is_error` 原样保留。preview 取前 ~20 行或 ~800 字（取先到者）。
- **幂等**：`offloaded` 为真直接跳过。**只碰 tool 消息**：user/assistant 消息从不进入扫描（N27）。
- 离线单测（tmp_path）：单条卸载落盘+替换+幂等；单轮挑大停止点；user/assistant 不被改；is_error 保留（AC68/AC69）。

### C49 第二层·切割 + 摘要 `context/summarizer.py`（F58/F59/F60）
- `find_cut_index`：从尾部累计 `char_estimate` 直到 `≥recent_keep_tokens` 且尾部条数 `≥recent_keep_min_messages` 得初始 cut；边界 snap：`while cut<len and messages[cut]["role"]=="tool": cut+=1`（把任何孤儿 tool 结果推入摘要区，保证保留段首条非 tool、不产生无 tool_use 配对的 tool_result）；若 snap 后保留段 < min 条或 cut≥len，则回退多保留（clamp，宁可少摘不可摘空/越界）。**配对铁律来源**：核验 anthropic.py:154-218 `_convert_messages` 确认连续 tool 折叠成一条 user tool_result、每个 `tool_use_id` 必须配前序 assistant 的 `tool_use`。
- 摘要请求：`provider.stream(messages[:cut] + [{"role":"user","content": 摘要指令}], system=SUMMARY_SYSTEM, tools=None)`——**tools=None 物理禁用工具**（F59）；`SUMMARY_SYSTEM`/指令含「禁止调用任何工具」「先写分析草稿、再写正式摘要、草稿用完即弃」「正式摘要按八段组织并包在 `<final_summary>…</final_summary>`」。收集流文本 → 正则抽 `<final_summary>` 内容（缺标记容错取全文）→ 空则 `raise SummaryError`。
- `build_compacted(summary_text, kept)`：返回 `[{"role":"user","content": f"<conversation_summary>\n{summary_text}\n</conversation_summary>\n\n[以上为早期对话的摘要。需要文件具体内容请重新用工具读取，切勿照摘要脑补或重建代码。]"}]` + `kept`——摘要 + 边界**合并为一条 user 消息**（F60；规避连续同角色风险）。
- **连续同角色**：保留段首条可能是 user（与摘要 user 相邻）。核验项：Anthropic/OpenAI 对连续 user 消息的容忍度——**T 任务里用 context7 查证 Anthropic Messages API**；若不容忍则 fallback 把 snap 推进到下一条 assistant。计划默认按「容忍」实现（API 通常合并同角色），查证落定。
- 离线单测：切割点位置/配对合法/snap 跳孤儿（AC70）；假 provider 返回固定 `<final_summary>` → 请求无 tools、prompt 含禁令、压缩后 = 摘要+边界+保留段（AC71）。

### C50 编排 + 熔断 `context/compactor.py`（F61/F62）
- `Compactor.compact(messages, last_usage, manual)` 按上方伪码：L1 卸载 → 估算 → 阈值判定（`window - reserved_output - margin`，manual 用 manual_margin 且无视 `_tripped` 强制尝试）→ L2 切割+摘要+替换 → 更新 `_last_seen_len` → 返回 `CompactionResult`。
- 熔断：`_fail` 连续失败计数，`≥3` 置 `_tripped`；成功清零；manual 成功额外清 `_tripped`（解除熔断，F61）。
- 离线单测：注入「摘要必失败」假 provider → 3 次熔断、后续自动跳过 L2 但 L1 照常、manual 强制重试并在成功时解除（AC72）；阈值/余量算法（AC71/AC72）。

### C51 钩子 + 配置 `agent/loop.py` + `config.py`（F62/F56/N25）
- `agent/loop.py`：`run(...)` 增可选参 `pre_round_compact: Callable[[list[Message], Usage|None], None] | None = None`。每轮在 `RoundStart` 之后、构造 `outgoing` 之前调用 `pre_round_compact(messages, last_round_usage)`（`last_round_usage` 跨轮保存，首轮 None）；钩子原地改写 `messages`，其后 `outgoing = request_decorator(messages, n)`（顺序：先压缩、后包提醒）。钩子为 None ⇒ 与 v0.7 字节级等价（N25）。loop 不持有 Compactor、不解释 CompactionResult（纯鸭子回调）。
- `config.py`：`ProviderConfig` 加 `context_window: int|None=None`；新增顶层 `context:` 块 → `ContextConfig`（整块缺失全默认）；两层深合并对 `context` 块逐键合并。
- 离线单测：loop 注入记录式钩子断言每轮调用次序与 last_usage 传递；钩子 None 时既有 loop 测试零修改全绿（AC73）；config 解析 context_window/ContextConfig 默认与覆盖。

### C52 装配 `repl.py` + `cli.py` + 版本（F61/F62）
- `cli.build_app`：按当前 provider 解析 `context_window`（provider 配置优先、否则 `ContextConfig.default_window`）+ 会话产物目录（`<sessions_dir>/<session_id>.artifacts/`）建 `Compactor`，把 `compactor.compact`（绑定 manual=False）作为 `pre_round_compact` 注入 `_chat_once` 的 `AgentLoop.run`。
- `repl.py`：① `_consume_agent` 消费 `UsageUpdate` 时存 `self._last_round_usage`（供 /compact 锚点；此前刻意忽略，现仅多存一个字段，屏显仍用 AgentDone.usage）；② `_dispatch_command` 加 `/compact`：调 `compactor.compact(self._session.messages, self._last_round_usage, manual=True)` → 落盘 → 打印 `CompactionResult`（卸载数 / 是否摘要 / 是否熔断）；③ 帮助表加 `/compact` 条目；④ provider 切换后重建/更新 compactor 的 provider 与 window。
- 版本 `0.8.0`（`__init__` + pyproject + lock 同步）；零新增第三方依赖。
- 离线单测：build_app 注入 compactor 后多轮触发；`/compact` 命令以 manual 余量触发并落盘；无 context 配置时全默认、行为不破坏既有冒烟（AC72/AC73）。

## 模块交互（一次压缩的数据流，v0.8 视角）

```
_chat_once: agent.run(messages, ..., pre_round_compact=compactor.compact)
AgentLoop 每轮 n：
  RoundStart(n)
  pre_round_compact(messages, last_round_usage):       # ← 写回式钩子，每轮调后端前
    L1: offload_oversized(messages, artifacts_dir, cfg)  # 超大 tool 结果 → 存盘 + 预览/路径替换（幂等）
    est = prompt_token_total(last_usage) + char(messages[_last_seen_len:])   # 锚点 + 增量字符
    if est > window - reserved_output - margin (and not tripped, 或 manual):
       cut = find_cut_index(messages)                    # 尾部保留 ~10K/≥5；snap 过孤儿 tool
       summary = provider.stream(messages[:cut]+[指令], system=禁工具+草稿后正式, tools=None)  # 物理禁工具
       messages[:] = [user: <conversation_summary>+边界提示] + messages[cut:]   # 摘要+边界合一条
       _fail=0 (manual 成功则 _tripped=False)  或  _fail+=1→≥3 置 _tripped
    _last_seen_len = len(messages)
  outgoing = request_decorator(messages, n)              # 再包 <system-reminder>（只读、不写回）
  provider.stream(outgoing, ...) → RoundCollector → 决策/工具/原子入史
  RoundEnd(n) → REPL 逐轮落盘（压缩改写与卸载随当轮持久化，中途崩溃不留孤儿引用）
  last_round_usage = round_result.usage                  # 传给下一轮钩子作锚点
用户 /compact → compactor.compact(messages, last_round_usage, manual=True) → 落盘 → 打印结果
程序请求结束 → 正常持久化
```

## 测试策略（v0.8 增量，离线为主）

| 组件 | 测法 | 关键用例 |
|------|------|----------|
| context/estimator | 纯函数 + 两家 Usage | prompt_token_total 两家算法；增量只折算新消息；字符比可配；空=0（AC67）|
| providers.prompt_token_total | 构造 Usage | anthropic=input+read+creation；openai/base=input；不重复加缓存（AC67）|
| context/offload | tmp_path + 构造工具结果 | 单条卸载落盘/替换/幂等；单轮挑大停止点；user/assistant 不改；is_error 保留（AC68/AC69）|
| context/summarizer(cut) | 构造含 tool 对历史 | 切割点位置；保留段不以 tool 开头；不拆 assistant↔结果对；snap 跳孤儿；clamp 不摘空（AC70）|
| context/summarizer(摘要) | 假 provider 固定回包 | 请求无 tools；prompt 含禁令+草稿；抽 `<final_summary>`；压缩后=摘要+边界+保留段（AC71）|
| context/compactor | 假 provider（含必失败） | 阈值/余量算法；熔断 3 次→跳 L2 留 L1；成功清零；manual 强制重试+解除（AC72）|
| agent/loop（钩子） | 记录式钩子 + ScriptedProvider | 每轮调用次序/last_usage 传递；**钩子 None 时既有 loop 测试零改全绿**（AC73 字节级回归）|
| config | 临时配置 | context_window/ContextConfig 默认与两层覆盖 |
| repl/cli | 既有注入点 | build_app 注入 compactor 多轮触发；/compact manual 余量+落盘；无 context 配置冒烟同 v0.7（AC72/AC73）|
| 切割配对（联网回归可选） | 压缩后历史送 provider | 两家 _convert_messages 转换无 400；真实长会话 /compact 后续轮正常（AC70）|

## v0.8 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 触发位置 | **AgentLoop 内·每轮请求前**写回式钩子（用户拍板） | token 爆炸常在长工具循环中途、非回合之间；精确对应「每次 API 请求前」；钩子独立于只读的 request_decorator |
| token 估算 | **近似**：锚定上次 usage + 增量字符折算（用户拍板） | 不上精确 tokenizer（YAGNI）；锚点吃真实用量、只对增量估算误差小；13K 余量兜估算误差 |
| 两家用量语义 | provider 新增 `prompt_token_total`（**核验修正**） | anthropic input 不含缓存、openai prompt_tokens 含缓存——差异关在 provider 层，估算层与协议无关（N26）|
| 窗口阈值 | `window − reserved_output − margin`（**核验修正**） | anthropic max_tokens=64000 占输出，窗口是输入+输出合计；只扣 margin 会撑爆；reserved_output 可配默认对齐 max_tokens |
| 窗口来源 | per-provider `context_window` + 内置默认（用户拍板） | opus 1M 与 deepseek 128K 差一个数量级，单一全局值要么浪费要么高估 |
| 第一层范围 | **只卸载工具结果**，不碰 user/assistant（用户拍板） | token 大头在工具结果；用户原始消息原文保真（N27）|
| 卸载持久性 | 永久替换为预览+路径、原文存盘（用户拍板） | 对话只留预览、磁盘留全文；REPL 逐轮落盘 ⇒ 中途崩溃不留孤儿引用（核验 repl.py:598-601）|
| 摘要段落 | Claude Code 式**八段**（用户拍板） | 被验证过的长任务续航骨架；覆盖意图/文件/报错/待办/下一步最全 |
| 摘要消息角色 | 摘要+边界**合并一条 user**（替主拍） | 规避连续同角色；保留段首条非 tool（配对合法）；边界提示紧贴摘要 |
| 手动 vs 熔断 | `/compact` 无视熔断**强制重试**、成功解除（替主拍） | 用户主动要就该尝试；自动轮才受熔断保护，避免死循环 |
| 装配 | repl/cli 注入 Compactor（默认开、config 调参） | 这是本版功能默认生效；loop「钩子 None=等价 v0.7」仅为测试与回归契约（N25）|

## v0.8 风险与边界

1. **R1 近似估算偏差**：字符折算与真实分词有出入（代码/CJK/JSON 比率不同）。对策：锚点吃真实 usage、只对增量估算；自动留 13K 余量、手动 3K；偏差只影响触发早晚、不影响正确性（晚触发最坏是某次请求略超窗→provider 报错被既有 STREAM_ERROR 兜住、不崩溃）。列为评审检查点。
2. **R2 连续同角色消息**：摘要 user 紧邻保留段首条 user。**T 任务用 context7 查证 Anthropic Messages API 对连续同角色的容忍**；默认按「容忍/合并」实现，查证否定则 fallback 把 cut snap 到下一 assistant（多摘一两条用户消息）。
3. **R3 摘要请求自身很贵/也可能超窗**：被摘的早段本就大，送 LLM 摘要是一次性大输入。本版接受（一次性成本换长期可持续）；若早段已超窗导致摘要请求失败 → 计入熔断、3 次后停 L2、L1 仍压（多级/分块摘要留后续，见「不做」）。
4. **R4 卸载产物孤儿**：会话被删/改名后 artifacts 目录残留；或卸载后未落盘崩溃。对策：artifacts 随 session_id 命名、与会话同目录；REPL 逐轮落盘使改写与卸载原子持久化（核验 repl.py:598-601 RoundEnd save）；不做自动清理（见「不做」），记为已知边界。
5. **R5 切割把关键近期上下文摘掉**：保留窗口（~10K/≥5 条）若太小可能摘掉仍需要的近期细节。对策：保留按 token 且有最小条数双保险；阈值可配；摘要八段含「当前工作/下一步」尽量承接。列为评审 + 人工场景检查。
6. **R6 与 v0.5 缓存交互**：压缩改写稳定前缀 → 击穿 Anthropic 提示词缓存（下一轮 cache_read 归零、重新 creation）。这是压缩的固有代价、且压缩本就罕见；记为已知取舍，不做缓存友好的「只在断点后追加摘要」优化（留后续）。
7. **R7 /compact 锚点**：回合之间手动触发时 `last_round_usage` 是上一回合最后一轮的 per-round usage，距今可能又追加了新用户消息。对策：估算对锚点后新消息按字符折算补足；锚点缺失（从未请求过）→ 全量字符估算。偏差被 3K 余量与「宁可多摘」吸收。

# v0.9 新增设计（F63–F69：记忆与会话 — 项目指令 + JSONL 会话/恢复 + 自动记忆）

> 技术方向：三套机制并行落地——**①项目指令文件三层加载**（新增 `prompt/instructions.py` 叶子，stdlib-only，填 v0.5 留好的「项目/自定义指令」空槽）；**②会话存档改 JSONL 追加 + 按 cwd 分区 + 恢复卫生**（重构 `session.py`，v0.8 没碰过、干净无冲突）；**③自动记忆**（新增 `src/wentian/memory/` 纯包：store/extractor/runner，填 v0.5「长期记忆」空槽，抽取在**后台 daemon 线程 fire-and-forget**）。v0.9 **复用 v0.8 `context` 包绝不重造**：会话恢复溢出判定用 `estimator.estimate_total`、恢复溢出压缩**直接调 `Compactor.compact(messages, last_usage, *, manual=False)`**、token 口径用 `Provider.prompt_token_total(usage)`。后台抽取照搬 v0.8 summarizer 的**同步** `for event in provider.stream(...)` 迭代模式（provider.stream 是同步生成器），**不引 asyncio**。记忆抽取引入的是 v0.5/v0.8 之外的**第三种**集成范式——**后台写回钩子**：区别于 v0.8 的同步 per-round `pre_round_compact`（原地改写当前发出的历史）、也区别于 v0.5 只读的 `request_decorator`（仅发出时包提醒），后台钩子**离线异步**写盘到记忆目录、**绝不触碰当前对话历史 messages**、本会话不热刷、下一会话启动注入才生效。版本升 `0.9.0`，零新增第三方依赖。

## 架构增量（v0.9）

```
prompt/instructions.py ──► 新叶子（stdlib-only）：三层 WENTIAN.md 加载 + @include 递归内联（限深+visited防环+越界拦截）+ 体积上限（C53）
session.py ──► 重构：单文件全量 JSON 重写 → 每会话一个 JSONL 按消息追加；project_sessions_dir(cwd) 按 <cwd-slug> 分区；坏行跳过（C54）
session.py（恢复卫生，叶子纯函数）──► truncate_unpaired / prune_expired / resume_gap_reminder；溢出复用 v0.8 Compactor（装配层调，不在包内 import provider）（C55）
memory/store.py ──► 新叶子（stdlib + 复用 estimator）：笔记 .md+frontmatter 读写、INDEX.md 读写、user/project 分级目录、索引体积上限、写锁串行（C56）
memory/extractor.py ──► 新（provider 鸭子注入，仿 summarizer）：四类笔记抽取 Prompt + 同步 stream 收文本 + 解析 + LLM 去重决策（C57）
memory/runner.py + repl.py ──► MemoryRunner 持自建 provider+store+cfg，submit() 启 daemon 线程 fire-and-forget；REPL 在 COMPLETED 回合后钩 submit（C58）
prompt/system.py ──► 两个可选模块（项目/自定义指令、长期记忆）从恒返回 "" 改为真渲染 ctx.project_instructions / ctx.memory（C59）
config.py ──► 顶层 memory: 块（MemoryConfig）+ sessions: 块（SessionsConfig），全可选带默认，沿用 v0.7 两层深合并（C59）
cli.py ──► build_app 装配：加载指令→ctx、加载两份 INDEX→ctx、会话用分区目录、resume 后接 Compactor 压一次、构造 MemoryRunner 注入 REPL；版本 0.9.0（C59）
```

- **分层依赖延续铁律**：`prompt/instructions.py` 是 **leaf**——只 import stdlib（`os`/`pathlib`/`re`），不 import provider/registry/agent；越界拦截「先解析符号链接再前缀比对」与 v0.6 N11 沙箱同规、但**不依赖** permissions 包（自含路径解析）。`memory/` 包对 **agent 编排层零反向依赖**——store 是 leaf（stdlib + 复用 `context/estimator` 的 `char_estimate` 卡索引体积）；extractor 经**鸭子类型**持有 provider（只用其 `stream`，仿 v0.8 summarizer），不 import 具体 provider、不依赖 registry/loop；runner 持 store + 鸭子 provider，**自建 provider 实例不跨线程共享**对话 provider。**会话层不依赖后端 SDK**：`session.py` 只读写 JSONL（`json`/`pathlib`），消息是 `Message` TypedDict、对两家 provider 适配层透明。**复用 v0.8 `context` 包不重造**：恢复溢出判定/压缩既不在 `session.py` 也不在 `memory/` 内重写估算或摘要，由**装配层（cli/repl）** 在 resume 后调既有 `Compactor.compact()`。
- **核心不变量（v0.9 新增）：① 无配置/无文件 ⇒ 零行为变化**——无 `WENTIAN.md`（三层全缺）则「项目/自定义指令」模块仍渲染 `""`（拼装无空行残渣，N29）；无 `memory`/`sessions` 配置块走全默认、无历史会话与旧扁平 `.json` 视为遗留不报错；`memory.enabled:false` ⇒ 抽取与注入整条链路关闭、退回 v0.8（N29/N31）。**② 后台抽取绝不污染对话**——抽取在 daemon 线程读**历史副本**、只写记忆目录，**绝不修改/追加当前会话 `messages`**、绝不阻塞 REPL 输入、异常静默吞 stderr、绝不中断会话（N31）；与 v0.8 写回式 `pre_round_compact`（改当前发出历史）正交。**③ 恢复后历史对两家 provider 合法**——尾部未配对 tool_call 截断后送两家 `_convert_messages`/openai 转换不产生 400（复用 v0.8 配对铁律，N29）。**④ 启动注入一次不热刷**——指令与记忆 INDEX 在 build_app 注入 `system` 一次，本会话不刷新（保 v0.5 缓存前缀稳定，N29）；本会话新抽记忆下一会话生效。**⑤ 索引启动注入**——记忆靠全量 INDEX 摘要注入、不做向量检索/RAG 按需召回（见「不做」）。
- **会话追加游标的状态管理**：`SessionStore` 不再每轮全量重写，REPL 持 `_persisted_count`（上次落盘已写入的消息行数）。每轮 `RoundEnd` 落盘时 `store.append(session, session.messages[_persisted_count:])` 只追加增量行，随后 `_persisted_count = len(session.messages)`。压缩（v0.8 `Compactor` 原地改写 `messages[:]`）会缩短 `messages` ⇒ 压缩轮把 `_persisted_count` 复位为 0 并整文件重写一次（压缩本就罕见，重写代价可接受；语义：压缩后 messages 是新历史、JSONL 需与之一致）——此为追加快路径的唯一例外，落定于实现期回填补注。

## 核心数据结构（v0.9 新增）

```python
# config.py —— 两个新顶层块（全可选、缺块/缺字段走内置默认、不抛异常）
@dataclass(frozen=True)
class MemoryConfig:               # 顶层新块 memory:；整块缺失 → 全默认（且 enabled=True）
    enabled: bool = True                  # 自动记忆总开关；false ⇒ 抽取+注入整链关闭（退回 v0.8）
    provider: str | None = None           # 抽取用 provider 名；None ⇒ 复用当前对话 provider 配置自建实例
    max_index_lines: int = 200            # 注入 system 的 INDEX 行数上限（用 estimator 卡预算）
    max_index_bytes: int = 25_600         # INDEX 字节上限（25KB）；超限按上限截断
@dataclass(frozen=True)
class SessionsConfig:             # 顶层新块 sessions:；整块缺失 → 全默认
    retention_days: int = 30              # 过期会话惰性清理阈值（mtime 早于此 → 删文件 + .artifacts/）
    resume_gap_reminder_hours: int = 4    # 距上次 updated_at 超此值 → 恢复首轮注入一次性时间跨度提醒

# memory —— 一条记忆笔记（落盘为带 frontmatter 的 .md）
CATEGORIES = ("用户偏好", "纠正反馈", "项目知识", "参考资料")  # 四类
SCOPE_OF = {"用户偏好": "user", "纠正反馈": "user",            # 默认归属（LLM 可改判）
            "项目知识": "project", "参考资料": "project"}
@dataclass(frozen=True)
class Note:
    category: str                 # ∈ CATEGORIES
    scope: str                    # "user" | "project"（默认按 SCOPE_OF，LLM 抽取时可覆盖）
    title: str                    # 索引行用（一句话标题）
    content: str                  # 笔记正文（Markdown body）
    created_at: str               # ISO8601
    source_session: str           # 来源会话 id
    tags: list[str]               # 可空
    # 落盘格式：YAML-lite frontmatter（category/scope/created_at/source_session/tags）+ 空行 + content
    # 抽取决策（LLM 去重）：每条带 action ∈ {"add","update","skip"}（skip 不落盘、update 替换同标题旧条）

# memory —— LLM 抽取回包的结构化笔记决策（extractor 解析目标）
@dataclass(frozen=True)
class NoteDecision:
    action: str                   # "add" | "update" | "skip"
    note: Note | None             # skip 时可为 None

# session.py —— JSONL 行格式（每行一个 JSON 对象）
#   可选首行 meta： {"type":"meta","id":"YYYYMMDD-HHMMSS-xxxx","created_at":...,"provider":...}
#   其后每行一条 Message（既有 providers.base.Message TypedDict，原样序列化）
#   不维护独立 meta 文件：id←文件名 stem；title←首条 role=="user" 消息 content 首行；
#   消息数←数非 meta 行；updated_at←文件 mtime（或末行时间戳，实现期定）
@dataclass(frozen=True)
class SessionMeta:                # list()/load_latest() 扫描产出的轻量摘要（不读全文）
    id: str
    title: str                    # 首条 user 消息首行（截断）
    message_count: int
    updated_at: float             # mtime

# 路径约定 —— <cwd-slug> 分区（参考 Claude Code）
#   cwd_slug(cwd: Path) -> str ： 绝对 cwd 路径分隔符 "/"→"-"（如 /Users/yn/proj → -Users-yn-proj）
#   会话分区： <data_dir>/projects/<cwd-slug>/sessions/<id>.jsonl
#             v0.8 卸载产物 <id>.artifacts/ 自动随会话同分区
#   用户级记忆： ~/.config/wentian/memory/{<category>/*.md, INDEX.md}      （用户偏好/纠正反馈）
#   项目级记忆： <cwd>/.wentian/memory/{<category>/*.md, INDEX.md}        （项目知识/参考资料）
#   旧全局扁平 ~/.local/share/wentian/sessions/*.json ⇒ 遗留：不列/不删/不报错
```

## 组件设计（C53–C59）

### C53 项目指令加载 `prompt/instructions.py`（叶子，stdlib only）（F63/N30/N32）
- **职责**：解析三层 `WENTIAN.md`、按优先级拼接、递归内联 `@include`、体积上限截断；产出一段纯文本灌进 `PromptContext.project_instructions`（该字段 v0.5 起恒空、本版起真渲染）。
- **对外接口**：
  - `load_project_instructions(cwd: Path, *, user_home: Path | None = None, cfg: MemoryConfig | None = None) -> str`——按优先级**高在前**读三处并以 `"\n\n"` 拼接：① `<cwd>/.wentian/WENTIAN.md`（项目本地覆盖，最高、放最前）② `<cwd>/WENTIAN.md`（项目根、团队共享）③ `<user_home>/.config/wentian/WENTIAN.md`（用户全局、放最后）。每层可选、缺失静默跳过；三层全缺 ⇒ 返回 `""`。
  - `expand_includes(text: str, base_dir: Path, project_root: Path, *, depth: int = 0, visited: frozenset[Path] = frozenset(), max_depth: int = 5) -> str`——把独占一行的 `@include <相对路径>` 内联展开（相对**包含它的文件所在目录** `base_dir` 解析）；递归展开被包含文件中的 `@include`。
- **约束（三道护栏）**：① **限深**：`depth >= max_depth` 停止展开该 include 并告警（保留原行或留空，实现期定）；② **`visited` 防环**：解析后的真实路径已在当前 include 链 `visited` 中 ⇒ 跳过并告警（a→b→a 不无限递归）；③ **越界拦截**：`resolved = (base_dir / rel).resolve()`（先解析符号链接），若 `project_root not in resolved.parents and resolved != project_root` 或 rel 是越界绝对路径 ⇒ 拒绝该 include 并告警、不读取（与 v0.6 N11 同规、自含实现不依赖 permissions 包）。
- **体积上限**：拼接后总字符超上限（防撑爆上下文）按上限截断并告警（沿用 estimator 字符口径或固定字节上限，实现期定）。
- **依赖与分层**：leaf——仅 stdlib（`pathlib`/`re`/`os`）；不 import provider/registry/agent/permissions。被 `cli.build_app` 调用。
- **测法**：纯函数 + 临时目录真实读写离线测——三层放不同文件断言注入顺序（高优先级在前）；`@include` 内联目标内容；构造 a→b→a 环断言不无限递归、按 visited 跳过且告警；`@include` 指向项目根外/越界绝对路径/软链接指向项目外 → 拒绝且不读取；缺文件静默跳过；超深度告警停止；超体积截断（AC75/AC84）。

### C54 会话层 JSONL 重构 `session.py`（F64/N29/N30）
- **职责**：会话从「单文件全量 JSON 重写」改 **每会话一个 JSONL、按消息追加写**；目录按 `<cwd-slug>` 分区；`/sessions` 与 `--continue` 默认只看当前分区、`--all` 跨分区。
- **对外接口**（`SessionStore`，`Session` dataclass 保留）：
  - `project_sessions_dir(cwd: Path, *, data_dir: Path | None = None) -> Path`——`<data_dir>/projects/<cwd-slug>/sessions/`；`cwd_slug(cwd) -> str` 分隔符 `/`→`-`（与 Claude Code 同法）。
  - `append(session: Session, new_messages: list[Message]) -> None`——把增量消息**逐行追加**到 `<id>.jsonl`（不重写全文）；文件不存在则先写可选 `meta` 首行再写消息行。**追加快、崩溃只丢最后半行**。
  - `load(id: str) -> Session`——逐行 `json.loads`，**坏行（解析失败）跳过并告警 stderr**、不抛给调用方（JSONL 原生能力）；meta 行用于回填 id/created_at/provider。
  - `list(*, all_projects: bool = False) -> list[SessionMeta]`——`all_projects=False` 只扫当前 `<cwd-slug>` 分区、`True` 跨 `projects/*/sessions/` 全扫；标题取首条 user 行、消息数=数消息行、updated_at=mtime（**扫描得出、不读全文**）。
  - `load_latest() -> Session | None`——当前分区按 mtime 取最新（`--continue` 用）。
- **依赖与分层**：只读写 JSONL（`json`/`pathlib`）+ 复用既有 `Session`/`Message`；**不依赖后端 SDK**；对 Agent Loop / v0.8 压缩写回钩子 / 权限门 / provider 适配层透明。REPL 持 `_persisted_count` 追加游标（见架构增量状态管理）。
- **测法**：临时 `data_dir` 真实读写——新会话写到 `projects/<cwd-slug>/sessions/<id>.jsonl`、每轮 append 断言文件**按追加增长**（非全量重写）；故意写坏行 → `load` 跳过坏行加载其余、id 取文件名/标题取首条 user/消息数正确；`list()` 默认只当前分区、`all_projects=True` 列全部；旧扁平 `.json` 不被列入、不报错（AC76/AC82）。

### C55 会话恢复卫生 `session.py`（叶子纯函数；溢出复用 v0.8 Compactor）（F65/F66/N29）
- **职责**：`load`/`resume` 时四道卫生处理，保证恢复后历史**对两家 provider 合法**且**不撑爆窗口**；启动惰性清理过期会话。坏行跳过已由 C54 `load` 承担（第①道）。
- **对外接口**（纯函数 / 可注入，离线测）：
  - `truncate_unpaired(messages: list[Message]) -> list[Message]`（第②道）——历史尾部若是「助手 tool_call 无后续工具结果」的悬空调用 → 截断该未配对部分（复用 v0.8 配对铁律：送两家转换不产生 400）。
  - `prune_expired(sessions_dir: Path, retention_days: int, *, now: float | None = None) -> list[str]`（F66）——删当前分区内 mtime 早于 `retention_days` 的会话文件**及其 `<id>.artifacts/` 目录**；清理失败不致命（告警跳过），返回被删 id 清单。
  - `resume_gap_reminder(updated_at: float, now: float, hours: int) -> str | None`（第④道）——距上次 `updated_at` 超 `hours` → 返回一条时间跨度提示文案，否则 `None`；该文案经 **v0.5 `<system-reminder>` 消息通道**在恢复后**首轮**注入**一次性**（绝不写回持久化 `messages`）。
  - 第③道**溢出先压一次**：**不在 session 包内 import provider**——由装配层（cli/repl，C59）在 resume 后用 v0.8 `estimate_total(provider.prompt_token_total(last_usage or 0), messages, char_per_token)` 判定 `> context_window - reserved_output - margin`，若超 ⇒ 调 **v0.8 `Compactor.compact(messages, last_usage, manual=False)`** 压一次（复用、不重造），再进入对话。
- **依赖与分层**：`truncate_unpaired`/`resume_gap_reminder` 纯函数 leaf；`prune_expired` 只动文件系统；溢出压缩的 provider 依赖留在装配层（保持 session 包不碰 provider）。
- **测法**：构造尾部「助手 tool_call 无后续结果」→ 截断后送两家转换不 400；构造 `estimate>窗口-余量` 的历史 → 装配层调 v0.8 `Compactor` 压一次后再进入对话（假 provider 固定摘要）；`updated_at` 距今超阈值 → 恢复首轮注入一条 `<system-reminder>` 时间提醒、且**不写回持久化**（对比 provider 收到的 messages 与 store 落盘 messages）；构造超 `retention_days` 会话 + 新会话 → 旧的（含 `.artifacts/`）被删、新的保留、`retention_days` 可配（AC77/AC78）。

### C56 记忆存储 `memory/store.py`（叶子，stdlib + 复用 estimator）（F68/N30/N31）
- **职责**：笔记 `.md`+frontmatter 读写、`INDEX.md` 读写、user/project 分级目录解析、索引体积上限（复用 v0.8 `char_estimate`/字节数卡 `max_index_lines`/`max_index_bytes`）、写操作加锁串行。
- **对外接口**（`MemoryStore`）：
  - `__init__(self, *, user_dir: Path, project_dir: Path, cfg: MemoryConfig)`——`user_dir=~/.config/wentian/memory/`、`project_dir=<cwd>/.wentian/memory/`。
  - `read_index(scope: str) -> str`——读 `<scope_dir>/INDEX.md`（缺失返回 `""`）。
  - `read_indexes_for_injection() -> str`——读 user+project 两份 INDEX 拼接、用 `char_estimate`/字节数卡 `max_index_lines`/`max_index_bytes`、超限按上限截断；供 build_app 注入「长期记忆」模块。
  - `write_note(note: Note) -> Path`——按 `note.scope`/`note.category` 落 `<scope_dir>/<category>/<slug>.md`（frontmatter + content）；`threading.Lock` 串行。
  - `upsert_index(note: Note, *, action: str) -> None`——按 action（add/update）改对应 scope 的 `INDEX.md`（标题 + 一句话，非全文）；加锁串行。
- **依赖与分层**：leaf——stdlib（`pathlib`/`json`/`threading`/`datetime`）+ 复用 `context/estimator.char_estimate`（卡索引预算）；不 import provider/agent。**写操作 `threading.Lock` 串行**（后台多线程并发写 INDEX 安全，N31）。
- **测法**：临时目录——`write_note` 落对 scope/category、frontmatter 正确、content 完整；`upsert_index` add/update 改 INDEX 不重复；`read_indexes_for_injection` 超 200 行/25KB 按上限截断、无记忆返回 `""`；并发多线程写同一 INDEX 加锁不丢条目（AC79/AC81/AC83）。

### C57 记忆抽取 `memory/extractor.py`（provider 鸭子注入，仿 summarizer）（F67/N30）
- **职责**：取**最近一轮对话**（末次 user + 助手正文 + 必要工具活动摘要）+ 现有 INDEX，调 LLM 产出**四类笔记**（用户偏好/纠正反馈/项目知识/参考资料）+ **LLM 去重决策**（喂现有索引让它判 add/update/skip）。
- **对外接口**：
  - `EXTRACT_SYSTEM`/抽取 Prompt——含「按四类归纳」「每条给 category/scope/title/content/tags」「把现有 INDEX 喂你、判 add(新增)/update(更新已有同主题)/skip(已覆盖)」「禁止调用任何工具」「正式结果包在结构化标记内」。
  - `extract(provider, recent_messages: list[Message], existing_index: str, *, source_session: str, now: str) -> list[NoteDecision]`——**同步** `for event in provider.stream(recent_messages + [指令], system=EXTRACT_SYSTEM, tools=None)` 收文本（照搬 v0.8 summarizer 同步迭代，**不引 asyncio**、**tools=None 物理禁用工具**）；解析结构化笔记 + 去重决策；解析失败/空 → 返回 `[]`（不抛、由 runner 静默吞）。
  - `build_recent_window(messages) -> list[Message]`（纯函数）——从完整历史切出「最近一轮」喂抽取（末次 user + 其后助手正文，工具活动取摘要不取全文）。
- **依赖与分层**：经**鸭子类型**持有 provider（只用 `stream`，仿 summarizer），不 import 具体 provider、不 import registry/loop/agent（对编排层零反向依赖，N30）。解析为纯函数。
- **测法**：解析为纯函数离线测（构造固定回包 → 解析出四类笔记 + add/update/skip 决策）；`extract` 用**假 provider** 返回固定笔记 JSON 端到端、断言请求 `tools=None`、prompt 含四类+去重指令；去重：已有 INDEX 含某条 → 再抽等价信息 → 假 provider 返回「已覆盖」决策 → 不重复追加（AC79/AC80）。

### C58 后台抽取编排 + REPL 钩子 `memory/runner.py` + `repl.py`（F67/N31）
- **职责**：把 extractor + store 编排成**后台 daemon 线程 fire-and-forget**，挂到 REPL 的 `COMPLETED` 回合钩子；`/exit` 时短 join 不卡退出。
- **对外接口**（`MemoryRunner`）：
  - `__init__(self, *, provider, store: MemoryStore, cfg: MemoryConfig, session_id: str)`——**持自建 provider 实例**（按 `memory.provider` 或复用当前对话 provider 的配置另建，**不跨线程共享**对话 provider，N31）+ store + cfg。
  - `submit(self, recent_messages: list[Message]) -> None`——`cfg.enabled` 为真时启 `threading.Thread(daemon=True)` 跑 `extract → 按决策 write_note + upsert_index`；**异常静默吞到 stderr**、**绝不阻塞调用方、绝不触碰对话 messages**；`enabled=False` ⇒ 直接 return（整链关闭）。
  - `close(self, timeout: float = ...) -> None`——`/exit` 时最多 `join` 一个短超时（不卡退出、无线程泄漏，N31）。
- **REPL 钩子**：`_chat_once` 在 `AgentDone.stop_reason == COMPLETED` 后调 `self._memory_runner.submit(extractor.build_recent_window(self._session.messages))`——**鸭子注入**：`self._memory_runner is None`（未注入 / `enabled=False`）⇒ 不抽取、回归 v0.8 行为。`run()` 的 `try/finally` + atexit 在退出路径调 `close()`（与 v0.7 MCP `close_all` 同款退出钩子）。
- **依赖与分层**：runner 持 store + 鸭子 provider，对 agent 编排层零反向依赖；REPL 经可选属性注入（仿 v0.6 `ask` 回调 / v0.7 manager 的注入风格），钩子 None ⇒ 字节级回归 v0.8。
- **测法**：假 provider（返回固定四类笔记）+ 真临时 store——一个 `COMPLETED` 回合后台线程抽出笔记按类落 user/project 目录、写 frontmatter `.md` + 更新 INDEX；注入「抽取必抛异常」假 provider → **静默吞、会话不中断、对话历史 messages 不被污染、不阻塞下一次输入**；daemon 线程不阻塞主线程（断言 submit 立即返回）；并发写 INDEX 加锁串行；自建 provider 不跨线程共享；`close` 短超时不泄漏线程（AC79/AC83）。

### C59 装配 `cli.py` + `prompt/system.py` + `config.py` + 版本（F68/F69/N29）
- **职责**：把三套机制接进启动装配；两个可选模块真渲染；配置扩展；版本升 `0.9.0`。
- **`prompt/system.py`**（两槽真渲染，v0.5 起恒空 → 本版真渲染）：
  - `_render_project_instructions(ctx)`：`ctx.project_instructions` 非空 ⇒ 渲染「项目/自定义指令」模块，否则返回 `""`（空槽，拼装器过滤空串无残渣，N29）。
  - `_render_memory(ctx)`：`ctx.memory` 非空 ⇒ 渲染「长期记忆」模块，否则 `""`。`PromptContext` 加两字段 `project_instructions: str = ""` / `memory: str = ""`。
- **`config.py`**：增 `MemoryConfig`/`SessionsConfig` 解析（顶层 `memory:`/`sessions:` 块，沿用 v0.7 两层深合并、逐键合并、缺块/缺字段全默认不抛 `ConfigError`）。
- **`cli.build_app`**：① `load_project_instructions(cwd, user_home, cfg)` → `ctx.project_instructions`；② `store.read_indexes_for_injection()` → `ctx.memory`（启动注入一次、不热刷，保缓存）；③ 会话用 `project_sessions_dir(cwd)`、启动惰性 `prune_expired`；④ resume 后按 C55 第③道用 v0.8 `Compactor.compact()` 压一次、按第④道经 reminder 通道注入时间提醒；⑤ 按 `memory.enabled`/`memory.provider` 构造 `MemoryRunner`（自建 provider 实例）注入 REPL，`enabled=False` ⇒ 注入 None。
- **版本** `0.9.0`（`__init__` + pyproject + lock 同步）；零新增第三方依赖。
- **测法**：build_app 注入指令/INDEX → 内容可见于发给后端的 `system`；索引超 200 行/25KB 按上限截断；无 memory 时模块为空、拼装无残渣；`memory`/`sessions` 配置块缺失走默认、`memory.enabled:false` 关抽取与注入；无 `WENTIAN.md`/无 memory/无历史会话时 v0.1–v0.8 测试全绿、行为与 v0.8 一致（AC81/AC82）。

## 模块交互（启动注入 + 一轮对话 + 后台抽取的数据流，v0.9 视角）

```
build_app(cwd):
  instr = load_project_instructions(cwd, user_home, cfg)        # 三层 WENTIAN.md + @include 内联（C53）
  ctx.project_instructions = instr                               # 填 v0.5「项目/自定义指令」空槽（一次注入、不热刷）
  store = MemoryStore(user_dir, project_dir, mem_cfg)
  ctx.memory = store.read_indexes_for_injection()               # user+project INDEX 拼接，卡 200行/25KB（C56）
  system = build_system_prompt(ctx)                              # 两槽真渲染、空槽过滤无残渣（C59）
  sessions_dir = project_sessions_dir(cwd)                       # <cwd-slug> 分区（C54）
  prune_expired(sessions_dir, sessions_cfg.retention_days)      # 启动惰性清理过期会话+.artifacts/（C55）
  if resume/--continue:
     messages = store.load(id)                                   # 逐行解析、坏行跳过（C54 ①）
     messages = truncate_unpaired(messages)                      # 尾部悬空 tool_call 截断（C55 ②）
     if estimate_total(prompt_token_total(last_usage), messages) > window-reserved-margin:
        compactor.compact(messages, last_usage, manual=False)    # 溢出复用 v0.8 Compactor 压一次（C55 ③）
     if (rem := resume_gap_reminder(updated_at, now, hours)):    # 时间跨度提醒（C55 ④）
        inject_once_via_system_reminder(rem)                     # 经 v0.5 reminder 通道、绝不写回 messages
  runner = MemoryRunner(provider=自建实例, store, mem_cfg, id)   # enabled=False ⇒ None（C58）

REPL 每轮：
  agent.run(messages, ..., pre_round_compact=compactor.compact)  # v0.8 同步压缩钩子（不变）
  RoundEnd → store.append(session, messages[_persisted_count:])  # JSONL 追加增量行（C54）；_persisted_count=len
  AgentDone:
    if stop_reason == COMPLETED and runner:                      # 后台写回钩子（C58）—— 区别于 v0.8/v0.5
       runner.submit(build_recent_window(messages))             #   启 daemon 线程 fire-and-forget
         └─[后台线程] extract(自建provider, recent, existing_index)  # 同步 stream、tools=None、不引 asyncio（C57）
            └─ 按 add/update/skip：write_note + upsert_index（加锁串行，C56）   # 只写记忆目录、绝不碰对话 messages
            └─ 异常 → 静默吞 stderr、不中断会话、不阻塞输入
/exit → runner.close(short_timeout)（try/finally + atexit，不卡退出、无线程泄漏）
下一会话启动 → ctx.memory 注入本会话新抽记忆 → 「越用越懂你」
```

## 测试策略（v0.9 增量，全部离线）

| 组件 | 测法 | 关键用例 |
|------|------|----------|
| prompt/instructions（三层） | tmp_path 真实文件 | 三层放不同 WENTIAN.md → 注入顺序高优先级在前；缺层静默跳过；三层全缺=""（AC75）|
| prompt/instructions（@include） | tmp_path + 构造 include 链 | 内联目标内容；a→b→a 环按 visited 跳过+告警不递归；超 max_depth 停止告警；体积超上限截断（AC75）|
| prompt/instructions（越界） | tmp_path + 软链接 | @include 指向项目根外/越界绝对路径/软链接指向项目外 → 先解析符号链接再比对、拒绝且不读取（AC84）|
| session（JSONL 追加） | 临时 data_dir | 写到 projects/<cwd-slug>/sessions/<id>.jsonl；每轮 append 文件按追加增长（非全量重写）；可选 meta 首行（AC76）|
| session（坏行/编解码） | 构造坏行 | load 跳过坏行加载其余、不抛；id 取文件名/标题取首条 user/消息数正确（AC76）|
| session（分区/--all） | 多分区临时目录 | list() 默认只当前分区、all_projects=True 跨分区全扫；旧扁平 .json 不列、不报错（AC76/AC82）|
| session（恢复卫生） | 构造含 tool 对历史 | truncate_unpaired 截尾部悬空 tool_call、送两家转换不 400；resume_gap_reminder 超阈值返提示、不写回持久化（AC77）|
| session（溢出复用 Compactor） | 假 provider 固定摘要 | estimate>窗口-余量 → 装配层调 v0.8 Compactor.compact() 压一次（复用不重造），断言走的是既有 Compactor（AC77）|
| session（过期清理） | 构造超期+新会话 | prune_expired 删旧的+.artifacts/、保留新的；retention_days 可配；清理失败告警不致命（AC78）|
| memory/store | 临时 user/project 目录 | write_note 落对 scope/category+frontmatter；upsert_index add/update 不重复；read_indexes_for_injection 超200行/25KB 截断、无记忆="" ；并发写 INDEX 加锁不丢条目（AC79/AC81/AC83）|
| memory/extractor（解析） | 纯函数 + 固定回包 | 解析四类笔记 + add/update/skip 决策；解析失败/空 → []（AC79/AC80）|
| memory/extractor（去重） | 假 provider 脚本化决策 | 请求 tools=None、prompt 含四类+去重指令；已有 INDEX 再抽等价信息 → 判「已覆盖」不重复追加（AC80）|
| memory/runner（后台） | 假 provider + 真临时 store | COMPLETED 回合后台抽出按类落盘+更新 INDEX；submit 立即返回不阻塞；daemon 线程；自建 provider 不跨线程共享；close 短超时不泄漏（AC79/AC83）|
| memory/runner（异常隔离） | 「必抛异常」假 provider | 静默吞 stderr、会话不中断、对话 messages 不被污染、不阻塞下一次输入（AC79）|
| prompt/system（两槽） | 注入假 ctx 字段 | project_instructions/memory 非空真渲染、空时模块过滤无残渣、缓存前缀稳定（AC81/AC82）|
| config | 临时配置 | memory:/sessions: 块默认与两层覆盖；缺块/缺字段不抛 ConfigError；enabled:false 关链（AC82）|
| cli/build_app | 既有注入点 | 指令/INDEX 注入 system；分区会话；resume 接 Compactor 压一次；按 enabled 注入/不注入 MemoryRunner；无配置冒烟同 v0.8（AC81/AC82）|
| 分层 import 断言 | 静态 import 检查 | instructions 叶子（无 provider/agent import）；memory 对 agent 编排零反向依赖；session 不依赖后端 SDK；复用 context 包（AC83）|
| 端到端（联网/真终端） | checklist 人工场景 | 真 provider 抽取一次落盘；真终端跨会话「越用越懂你」体感；--continue 跨时间看时间提醒；放 WENTIAN.md+@include 启动看注入 |

## v0.9 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 会话存储分区 | **参考 Claude Code 按 `<cwd-slug>` 分区**（用户拍板） | `/sessions` 与 `--continue` 默认只看当前项目、避免跨项目串台；slug 化可读可调试；与 Claude Code 同法降认知成本 |
| 旧扁平会话 | 视为**遗留**：不列/不删/不报错（用户拍板） | 向后兼容不破坏、又不污染分区视图；不做迁移（YAGNI，旧会话价值低） |
| 会话写入 | **JSONL 按消息追加**（替单文件全量重写） | 追加快、崩溃只丢最后半行；坏行可逐行跳过；无需独立 meta 文件（id/标题/数量由扫描得出） |
| 记忆启动注入 | **启动注入一次、不热刷**（用户拍板） | 保 v0.5 提示词缓存前缀稳定；本会话新记忆下一会话生效，正合「新会话启动时恢复」语义 |
| 抽取时机 | **后台 daemon 线程·每个 COMPLETED 回合**（用户拍板） | 不阻塞输入；异常静默吞、绝不中断会话；fire-and-forget 与对话主链解耦 |
| 抽取 provider | **自建 provider 实例**（默认复用当前 provider 配置、`memory.provider` 可覆盖，用户拍板） | 线程安全——不跨线程共享对话 provider；可单独指更便宜的模型抽取 |
| 抽取迭代模型 | **同步 `for event in provider.stream(...)`**（仿 v0.8 summarizer） | provider.stream 是同步生成器；后台线程内同步调用即可、**不引 asyncio**（零新增范式） |
| 去重 | **交 LLM 判断**（喂现有索引判 add/update/skip） | 语义去重比字符串匹配准；不上向量库（见「不做」） |
| 指令文件名 | **`WENTIAN.md`**（用户拍板） | 对标 CLAUDE.md 约定、团队可读可提交 |
| @include 越界 | **先解析符号链接再前缀比对**（与 v0.6 N11 沙箱同规） | 防软链接逃逸出项目根；自含实现不依赖 permissions 包（保 instructions 叶子纯净） |
| 恢复溢出 | **复用 v0.8 `Compactor.compact()` 不重造** | 估算/摘要/配对铁律 v0.8 已实现且测过；session 包不碰 provider、压缩留装配层 |
| 记忆分级 | **user/project 分目录**（用户偏好/纠正反馈→user，项目知识/参考资料→project，LLM 可改判） | 跨项目偏好与项目知识天然分层；项目级 `.wentian/memory/` 建议入 gitignore |
| 集成范式 | **后台写回钩子**（区别于 v0.8 同步 pre_round_compact、v0.5 只读 request_decorator） | 抽取离线异步、只写记忆目录、绝不触碰当前对话历史 messages，三种范式正交不互扰 |
| 装配 | cli/repl 注入 MemoryRunner（默认开、`enabled:false` 一键关） | 本版功能默认生效；REPL 钩子「None=回归 v0.8」为测试与回归契约（N29/N31）|

## v0.9 风险与边界

1. **R1 后台线程异常/泄漏**：抽取在 daemon 线程，异常若不吞会冒泡、线程若不 join 会泄漏。对策：`submit` 内 `try/except` 静默吞到 stderr、绝不中断会话或污染 `messages`；`close(short_timeout)` 经 `try/finally` + atexit（仿 v0.7 MCP `close_all`）在所有退出路径短 join，不卡退出。列为评审 + 退出零残留线程取证。
2. **R2 并发写 INDEX 竞态**：多个 `COMPLETED` 回合的后台线程可能并发写同一 `INDEX.md`。对策：`MemoryStore` 写操作 `threading.Lock` 串行；自建 provider 实例不跨线程共享对话 provider。列为并发写不丢条目检查点。
3. **R3 @include 路径逃逸**：软链接或 `../` 越界绝对路径试图读项目外文件。对策：**先 `resolve()`（解析符号链接）再前缀比对**项目根（与 v0.6 N11 同规）、限嵌套深度 5、`visited` 防环；越界即拒绝告警不读取。列为安全检查点。
4. **R4 记忆/会话落盘泄密**：抽取可能把 api_key 等密钥写进记忆，或会话 JSONL 落盘含密钥。对策：抽取 Prompt 明确「绝不记录密钥/凭证」；落盘扫描断言不含 api_key；项目级 `.wentian/memory/` 建议入 gitignore（文档核对）。列为安全检查点。
5. **R5 近似估算误判恢复溢出**：恢复溢出判定复用 v0.8 字符折算估算，偏差只影响是否多压一次。对策：复用 v0.8 锚点 + 余量机制；晚触发最坏是某次请求略超窗→既有 STREAM_ERROR 兜住；早触发只是多摘一点近期原文。已知边界，不上精确 tokenizer（见「不做」）。
6. **R6 记忆质量/噪声**：LLM 抽取可能产出低价值或重复笔记、INDEX 膨胀。对策：四类约束 + LLM 去重判 skip/update；INDEX 卡 200 行/25KB 上限截断；笔记是可手动编辑的 Markdown（不做图形化管理 UI，见「不做」）。列为人工体感场景检查（「越用越懂你」对比）。
7. **R7 压缩与 JSONL 追加游标冲突**：v0.8 `Compactor` 原地缩短 `messages` 会让追加游标 `_persisted_count` 失效。对策：压缩轮把 `_persisted_count` 复位、整文件重写一次（压缩罕见、代价可接受）；其余轮走追加快路径。落定于实现期回填补注。
8. **R8 与 v0.5 缓存交互**：指令 + 记忆 INDEX 注入 `system`、改变缓存前缀。对策：**启动注入一次、不热刷**（保前缀稳定、缓存命中）；本会话新记忆下一会话才生效。已知取舍——这正是「不做会话内热刷」的根据（见「不做」）。

## v0.9 不做的事（边界）

- 不做**向量数据库 / 嵌入检索**（记忆靠启动注入全量 INDEX 摘要，不做语义检索）。
- 不做 **RAG 式按需检索记忆**（不在对话中动态召回相关笔记）。
- 不做**团队 / 跨机器记忆同步**（本地单用户，不做云同步/共享）。
- 不做**会话内记忆热刷新**（启动注入一次、保提示词缓存；本会话新记忆下一会话生效）。
- 不做**记忆的图形化管理 / 编辑 UI**（笔记是带 frontmatter 的 Markdown、手动可编辑）。
- 不做**精确 tokenizer**（索引体积/恢复溢出判定复用 v0.8 近似估算）。
- 不做**指令文件实时热重载**（启动加载一次；改了 `WENTIAN.md` 需重启生效）。
- 不做**旧扁平会话迁移**（旧 `.json` 视为遗留不列不删；不自动搬进分区目录）。
- **作废** v0.5 的「不做项目指令文件加载」「不做自动记忆 / 长期记忆提炼」两条——本版正是实现它们。

# v0.10 新增设计（F70–F76：斜杠命令系统 — 注册中心 + 解析 + 分发 + 补全）

> 技术方向：把 REPL 里临时手写的 `_dispatch_command` + 硬编码 `handlers` dict + `_COMMANDS` 元组收口成一个**框架无关的命令包** `src/wentian/commands/`（spec / registry / context / parser / builtins 五件，**纯包零 `rich` / 零 `prompt_toolkit` / 零后端 SDK**），prompt_toolkit 相关的补全器单独落在 **ui 层** `ui/completion.py`。命令处理函数只依赖一个 **`CommandContext` Protocol**（界面控制接口），由 REPL 实现——命令与 Rich/REPL 具体类解耦、可用**假 ctx** 离线驱动每条命令。注册中心**镜像既有 `ToolRegistry`**：按注册顺序存、命名/别名冲突在 `register` 即 `raise`（在 `build_app` 启动触发 = panic、不延后运行时）。十个内置命令 + 归并的 `/provider`/`/exit` 全走**同一分发路径**；`/new`/`/sessions`/`/resume` 折成 `/session` 子命令。版本升 `0.10.0`，零新增依赖。

## 架构增量（v0.10）

```
commands/spec.py     ──► 新叶子：CommandSpec dataclass + CommandType 枚举（LOCAL/UI_STATE/PROMPT）（C85）
commands/registry.py ──► 新叶子：CommandRegistry——按序存、名/别名查找(大小写不敏感)、可见列举、前缀补全候选、冲突 raise（C86）
commands/context.py  ──► 新叶子：CommandContext Protocol（界面控制接口，仅 typing.Protocol、零运行时依赖）（C87）
commands/parser.py   ──► 新叶子：parse(line) -> ParsedCommand|None（斜杠前缀 / 空格切分 / 名转小写 / 裸斜杠空白早返回）（C88）
commands/builtins.py ──► 新：build_builtin_registry() + 12 条 handler（只调 ctx.*、纯包、对 ctx 鸭子）（C89）
ui/completion.py     ──► 新（ui 层）：CommandCompleter(prompt_toolkit.Completer) 读 registry.completions；input.py 注入 completer（C90）
repl.py              ──► 改：REPL 实现 CommandContext；_dispatch_command 重写「parse→registry→handler」；status_line 模式标记 [DEFAULT]/[PLAN]；新增 clear_context / 会话 / provider / compact / memory_summary 等 ctx 方法；老 _cmd_* 逻辑迁进 ctx 方法或 builtins（C91）
cli.py               ──► 改：build_app 构造 registry（冲突即 panic）+ 建 CommandCompleter 注入 PromptInput；版本 0.10.0（C92）
```

- **分层依赖铁律（v0.10 延续）**：`commands/` 是**纯包**——`spec`/`registry`/`parser`/`context` 为叶子（只 stdlib + `typing`/`dataclasses`/`enum`）；`builtins` 只 import 同包三件 + 经 `CommandContext` 协议对 REPL 鸭子调用，**不 import `repl` 具体类、不 import `rich`/`prompt_toolkit`/provider**。prompt_toolkit 是 ui 层依赖：`CommandCompleter` 住 `ui/completion.py`、只消费注册中心的「前缀 → 候选」纯数据查询（`registry.completions(prefix)`），命令包对补全框架无感。`repl.py` 作为装配/界面层**实现** `CommandContext`（既有「repl 不 import `wentian.tools`、registry/executor 鸭子」惯例延续——`commands` registry 同样鸭子注入 REPL）。
- **核心不变量（v0.10 新增）：① 归并不改语义**——`/help`/`/provider`/`/exit`/`/plan`/`/do`/`/compact` 经注册中心分发后行为与归并前逐字一致；`/new`/`/sessions`/`/resume` 的能力由 `/session new|list|resume` 等价承载（N35）。**② 非命令路径零变化**——`line.startswith("/")` 为假时仍走既有 `_chat_once`/AgentLoop，无命令输入时与 v0.9 字节级等价（N35）。**③ 命令不发请求**——LOCAL/UI_STATE 命令绝不触发 provider 调用（除提示词类经 `send_user_message` 显式跑一轮）；命令分发不进 AgentLoop、不进权限门（命令是用户主动本地操作，不做命令级权限，见「不做」）。**④ 启动期硬失败**——命名/别名冲突在 `build_app` 构造 registry 时 `raise`、进程带 traceback 退出（panic、N37）。**⑤ 状态栏模式标记**——`status_line` 左段由 `{mode.value}` 改 `[{MODE_NAME}]` 括号式，活读 `self._mode`、切换后下次工具栏重算自动反映（沿用 v0.6 既有 live 重算机制）。

## 核心数据结构（v0.10 新增）

```python
# commands/spec.py
class CommandType(Enum):
    LOCAL    = "local"      # 纯本地：跑完即返回、不扩展对话历史（/help、/status、/memory、/compact、/exit）
    UI_STATE = "ui_state"   # 影响界面/会话状态（/clear、/plan、/do、/session、/permission、/provider）
    PROMPT   = "prompt"     # 把预设提示词送进对话交给 AI 跑一轮（/review）

@dataclass(frozen=True)
class CommandSpec:
    name: str                                          # 规范名、无斜杠、小写（如 "session"）
    summary: str                                       # /help 一行说明
    usage: str                                         # 用法示例（如 "/session resume <id>"）
    type: CommandType
    handler: Callable[["CommandContext", str], bool | None]  # 返回真值 ⇒ REPL 退出（沿用既有约定）
    aliases: tuple[str, ...] = ()                      # 别名（多别名指向同一命令）
    arg_hint: str = ""                                 # 可选参数提示
    hidden: bool = False                               # 隐藏命令：不进 /help、不进补全

# commands/parser.py
@dataclass(frozen=True)
class ParsedCommand:
    name: str        # 小写、无斜杠
    args: str        # 第一个空格之后、strip 过的参数串（无参数则 ""）
# parse(line) -> ParsedCommand | None ：line 已 strip 且以 "/" 开头；裸 "/" / 纯空白 → None

# commands/context.py —— 界面控制接口（REPL 实现；命令只依赖它、不碰 Rich）
class CommandContext(Protocol):
    # 显示 / 发送
    def print(self, renderable: object) -> None: ...               # 显示消息（渲染层细节由实现方吞）
    def send_user_message(self, text: str) -> None: ...            # 发送用户消息：提示词类触发一轮 AI（= _chat_once）
    # 模式
    def get_mode(self) -> "Mode": ...
    def set_mode(self, mode: "Mode") -> None: ...                  # /plan //do //permission 切档
    # 查询
    def token_usage(self) -> object | None: ...                   # 上轮 usage 快照（/status）
    def status_line(self) -> str: ...                             # 当前状态行（/status）
    def memory_summary(self) -> str: ...                          # 长期记忆目录 + 各域 INDEX 摘要（/memory，只读）
    def visible_commands(self) -> list[CommandSpec]: ...          # 供 /help 渲染
    # 状态变更
    def clear_context(self) -> None: ...                          # /clear：清空当前会话 messages、留同一 id
    def new_session(self) -> None: ...                            # /session new
    def list_sessions(self, *, all_projects: bool) -> None: ...   # /session list [--all]
    def resume_session(self, sid: str) -> None: ...               # /session resume <id>
    def switch_provider(self, name: str) -> None: ...             # /provider
    def compact_now(self) -> str: ...                             # /compact：触发重量压缩、返回可读汇报
```

## 组件设计（C85–C92）

### C85 命令规格 `commands/spec.py`（叶子）（F70/F72）
- **职责**：定义 `CommandType` 三类枚举与 `CommandSpec` 不可变 dataclass（命令定义元数据的唯一形状）。
- **对外接口**：上方数据结构；`CommandSpec` 字段即 spec F70 的「每条命令登记项」。
- **依赖与分层**：叶子——只 `dataclasses`/`enum`/`typing`/`collections.abc.Callable`；`handler` 的 ctx 形参用字符串前向引用避免对 `context.py` 的运行时 import。
- **测法**：构造 `CommandSpec` 断言字段齐备、`frozen` 不可变、`CommandType` 三值；纯数据离线测（AC85）。

### C86 命令注册中心 `commands/registry.py`（叶子，镜像 ToolRegistry）（F70/N34/N37）
- **职责**：按注册顺序存命令、名/别名查找（大小写不敏感）、列举可见命令、按前缀给补全候选、**冲突即 `raise`**。
- **对外接口**（`CommandRegistry`）：
  - `register(spec: CommandSpec) -> None`——把 `spec.name` 与每个 `spec.aliases` 作 key 登记；**任一 key 已存在 → `raise ValueError`**（命名或别名冲突，镜像 `ToolRegistry.register` 重名 `raise`）；规范名追加进有序列表。
  - `lookup(name: str) -> CommandSpec | None`——按名/别名查找（入参先 `.lower()`）。
  - `visible() -> list[CommandSpec]`——按注册顺序返回 `hidden is False` 的命令（`/help` 用）。
  - `all() -> list[CommandSpec]` / `completions(prefix: str) -> list[CommandSpec]`——补全候选 = 可见命令中**规范名以 `prefix`（小写）开头**的，按注册顺序（隐藏不入）。
- **依赖与分层**：叶子——只 stdlib + import 同包 `spec`。**线程安全非必需**（启动期单线程构造、之后只读，同 ToolRegistry）。
- **测法**：注册假命令后按名/别名/大小写查找命中；`visible`/`completions` 过滤 hidden + 前缀；重复名、重复别名、别名撞他人名 → 各断言 `raise`（AC85/AC90）。

### C87 界面控制接口 `commands/context.py`（叶子，仅 Protocol）（F73）
- **职责**：定义 `CommandContext` Protocol——命令处理函数依赖的全部界面能力面（见核心数据结构）；REPL 实现它。
- **对外接口**：上方 Protocol 方法签名。设计原则——这是「REPL 对命令的公开面」，宽度受控（~14 法），但都是命令实需能力；提示词类只用 `send_user_message`、本地类只用 `print`/查询。
- **依赖与分层**：叶子——`typing.Protocol` + 前向引用 `Mode`/`CommandSpec`（`TYPE_CHECKING` 下 import，运行时零依赖）。
- **测法**：定义一个**假 ctx**（dataclass 记录 `printed`/`sent`/`mode`/调用计数）实现该协议，被 C89 全部 builtin 测试复用；协议本身无运行时逻辑、靠 builtins 测试覆盖（AC87）。

### C88 解析器 `commands/parser.py`（叶子）（F71）
- **职责**：`parse(line) -> ParsedCommand | None`——`line` 已 `strip` 且以 `/` 开头：去首斜杠、第一个空格前为名（`.lower()`）、之后为参数（`strip`）；裸 `/` 或纯空白体 → `None`（早返回、不进分发）。
- **对外接口**：`parse`、`ParsedCommand`。
- **依赖与分层**：叶子——纯字符串处理（`str.partition`）、零 import 业务模块。
- **测法**：`/Help` → `name="help"`；`/session resume abc` → `("session","resume abc")`；`/x` → `("x","")`；裸 `/`、`/   ` → `None`（AC86）。

### C89 内置命令 `commands/builtins.py`（依赖 spec/registry/context）（F76/F72）
- **职责**：`build_builtin_registry() -> CommandRegistry`——构造并注册 12 条内置命令（10 + 归并的 `/provider`/`/exit`）；每条 handler 是自由函数 `def _h_xxx(ctx, args) -> bool | None`，**只调 `ctx.*`**。
- **命令清单与类型**：`/help`(别名 `?`,`h` · LOCAL)、`/status`(`st` · LOCAL)、`/memory`(`mem` · LOCAL)、`/compact`(LOCAL)、`/clear`(`cls` · UI_STATE)、`/plan`(UI_STATE)、`/do`(UI_STATE)、`/permission`(`perm` · UI_STATE)、`/session`(`sess` · UI_STATE)、`/provider`(UI_STATE)、`/review`(PROMPT)、`/exit`(`quit`,`q` · LOCAL，handler 返回 `True`)。
- **代表实现**：`_h_help` → `ctx.print(render_help(ctx.visible_commands()))`；`_h_clear` → `ctx.clear_context()` + 确认打印；`_h_review` → `ctx.send_user_message("请审查未提交改动：" + (args or "全部"))`（提示词类）；`_h_session` 解析子命令 `new`/`list [--all]`/`resume <id>` 路由到 `ctx.new_session()`/`ctx.list_sessions(all_projects=...)`/`ctx.resume_session(sid)`；`_h_plan`/`_h_do` → `ctx.set_mode(Mode.PLAN/DEFAULT)` + 尾随文字经 `ctx.send_user_message`；`_h_permission` 无参打印当前模式、带参 `ctx.set_mode(parse_mode(args))`。
- **依赖与分层**：纯包——import 同包 `spec`/`registry` + 经 `CommandContext` 对 ctx 鸭子；`Mode` 从 `permissions.decision` import（permissions 是纯叶子、既有 repl 已 import，分层允许）；**不 import `rich`/`prompt_toolkit`/`repl` 具体类**。`render_help` 产出结构化数据或纯文本，Rich 表格化留在 REPL 的 `print` 实现侧（命令不碰 Rich）。
- **测法**：用假 ctx 逐条驱动——`/help` 断言 `ctx.print` 收到含全部可见命令的体；`/plan`/`/do` 断言 `ctx.set_mode` 被调对值；`/review` 断言 `ctx.send_user_message` 收到预设提示（**非** provider 调用）；`/session resume x` 断言 `ctx.resume_session("x")`；`/clear` 断言 `ctx.clear_context()`；`/exit` 返回 `True`（AC87/AC91/AC92）。

### C90 Tab 补全 `ui/completion.py`（ui 层，prompt_toolkit）（F75）
- **职责**：`CommandCompleter(prompt_toolkit.completion.Completer)`——输入以 `/` 开头**且尚无空格**时，从 `registry.completions(prefix)` 产出 `Completion`（隐藏不入、带一行 `display_meta`）；有空格（已在敲参数）则不补。
- **对外接口**：`CommandCompleter(registry)`；`get_completions(document, complete_event)`。`input.py` 的 `PromptSession` 增 `completer=` + `complete_style=MULTI_COLUMN`——**单匹配 Tab 直接补、多匹配弹菜单** = prompt_toolkit 默认行为。
- **依赖与分层**：ui 层——import `prompt_toolkit` + 经鸭子 registry（只调 `.completions`）；命令包对它无感。Shift+Tab 仍是既有 `on_mode_cycle`（切权限模式），与 Tab 不冲突。
- **测法**：构造假 `Document`（`text_before_cursor="/se"`）驱动 `get_completions`，断言产出 `/session`（前缀命中）；`/` 多候选、`/zzz` 零候选、隐藏命令不现身、有空格不补（AC90）；真终端菜单弹出留 👁 人工。

### C91 REPL 实现接口 + 分发重写 `repl.py`（改）（F73/F74/F76）
- **职责**：① REPL **实现 `CommandContext`**（既有 `_cmd_*` 逻辑迁为协议方法：`clear_context`/`new_session`/`list_sessions`/`resume_session`/`switch_provider`/`compact_now`/`memory_summary`/`token_usage`/`status_line`/`print`/`send_user_message`/`get_mode`/`set_mode`/`visible_commands`）；② `_dispatch_command` 重写为「`parse(line)` → 空/裸斜杠引导 → `registry.lookup(name)` → 未命中打印 `/help` 引导 → 命中 `spec.handler(self, args)`、返回真值则退出」；③ `status_line` 左段模式标记改 `[DEFAULT]`/`[PLAN]` 括号式；④ 新增 `clear_context`（`messages=[]` + 复位 `_persisted_count`/指纹 + 覆写落盘 + 复位 `_last_round_usage`、留同一 session id）。
- **对外接口**：构造新增 `commands: object | None`（鸭子注入的 `CommandRegistry`；None ⇒ 回退既有硬编码分发，回归安全）。
- **依赖与分层**：REPL 作装配/界面层实现协议（既有「registry/executor 鸭子、不 import wentian.tools」惯例延续——`commands` 同样鸭子）；`send_user_message` = 调既有 `_chat_once`（提示词类复用一轮 AI 路径）。
- **测法**：假 provider + 假 input——命令路径断言 provider 零调用（AC89）；`/plan` 后 `status_line` 含 `[PLAN]`（AC88）；`/clear` 后消息数归零、session id 不变（AC92）；未命中打印 `/help` 引导（AC86）；归并命令逐条回归（AC91）。

### C92 装配 `cli.py` + 版本（改）（F70/N37/N38）
- **职责**：`build_app` 调 `build_builtin_registry()` 构造命令注册中心（**别名冲突在此 `raise` = panic、进程退出**）→ 注入 REPL；建 `CommandCompleter(registry)` 注入 `PromptInput`；版本升 `0.10.0`。
- **对外接口**：`build_app` 内部装配；命令系统默认启用（无配置开关，命令是核心交互）。
- **测法**：`build_app` 后 REPL 持非空 registry、PromptInput 持 completer；构造一个故意冲突的 registry 工厂断言启动 `raise`（AC85/AC94）；版本字符串 `0.10.0`；无配置冒烟退出码 0（AC93/AC94）。

## 模块交互（一次命令分发 + 补全 + 提示词类的数据流，v0.10 视角）

```
build_app():
  registry = build_builtin_registry()        # 12 条注册；命名/别名冲突在此 raise = panic（C89/C92）
  completer = CommandCompleter(registry)      # ui 层（C90）
  PromptInput(completer=completer, ...)        # 单匹配补 / 多匹配菜单 = prompt_toolkit 默认
  REPL(commands=registry, ...)                 # REPL 实现 CommandContext（C91）

REPL.run() 每行：
  line = input()                               # Tab 补全在此交互（敲命令名时）
  if not line: continue                        # 空输入早返回（既有）
  if line.startswith("/"):                     # 分流器（F74，既有判断保留）
     parsed = parse(line)                       # 斜杠/空格切分/小写（C88）
     if parsed is None: print 引导; continue    # 裸斜杠/空白
     spec = registry.lookup(parsed.name)        # 名/别名查找（C86）
     if spec is None: print "未知命令 + /help"; continue
     exit = spec.handler(self, parsed.args)     # self 即 CommandContext（C89→C91）
     if exit: return                            # /exit 返回 True
  else:
     self._chat_once(line)                      # 非命令 → AgentLoop 一轮（既有，零变化）

提示词类（/review）：handler 调 ctx.send_user_message(preset) ──► REPL._chat_once(preset) ──► 一轮 AI
本地/界面类：handler 只调 ctx.print / ctx.set_mode / ctx.clear_context / ... ──► 绝不触发 provider 请求
```

## 测试策略（v0.10 增量，全部离线）

| 组件 | 测法 | 关键用例 |
|------|------|----------|
| commands/spec | 纯数据 | CommandSpec 字段齐备 frozen 不可变；CommandType 三值（AC85）|
| commands/registry | 构造假命令 | 名/别名/大小写查找命中；visible/completions 过滤 hidden+前缀；重名/重别名/别名撞名 → raise（AC85/AC90）|
| commands/parser | 纯函数 | /Help→help；/session resume x→(session,"resume x")；裸 / 与纯空白→None（AC86）|
| commands/context | 假 ctx 实现协议 | 被 builtins 测试复用（记录 printed/sent/mode/调用）（AC87）|
| commands/builtins | 假 ctx 逐条驱动 | /help 列可见命令；/plan//do 调 set_mode；/review 调 send_user_message（非请求）；/session 子命令路由；/clear 调 clear_context；/exit 返回 True（AC87/AC91/AC92）|
| ui/completion | 假 Document | /se→/session；/ 多候选；隐藏不现身；有空格不补；零候选（AC90）|
| repl（分发） | 假 provider+假 input | 命令路径 provider 零调用；未命中 /help 引导；非命令走 _chat_once（AC89/AC86）|
| repl（状态栏） | 断言状态行字串 | /plan→[PLAN]、/do→[DEFAULT]、/permission 切档反映（AC88）|
| repl（/clear） | 真 store 临时目录 | messages 归零、session id 不变、覆写落盘、压缩锚点复位（AC92）|
| repl（归并回归） | 既有命令逐条 | /help//provider//exit//plan//do//compact 行为与归并前一致；/session 等价 /new//sessions//resume（AC91/N35）|
| cli/build_app | 既有注入点 | registry/completer 注入；冲突 registry 启动 raise；版本 0.10.0；无命令输入回归 v0.9（AC93/AC94）|
| 分层 import 断言 | ast/静态检查 | commands/ 零 rich/prompt_toolkit/SDK；builtins 不 import repl 具体类；completer 在 ui 层（AC94）|
| 端到端（真终端） | checklist 人工 | 👁 Tab 单补/多菜单弹出观感；真跑十命令一轮 |

## v0.10 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 命令与 REPL 解耦 | **CommandContext Protocol**（REPL 实现，命令只依赖协议） | 命令不绑定 Rich/REPL 具体类、可用假 ctx 离线全测；对应 spec「界面控制接口」（用户拍板 A 方案） |
| 控制信号 | **handler 返回 `bool|None`**（真值=退出），其余经 ctx 副作用 | 与既有 `_dispatch_command` 返回真值退出的约定平滑衔接，最小惊讶 |
| 注册中心 | **镜像 ToolRegistry**：按序存、冲突 raise | 复用既有心智模型；冲突在 build_app 启动 raise = 用户要的「撞名 panic、不等运行时」（N37） |
| 补全器落点 | **ui 层 `ui/completion.py`**（非命令包） | prompt_toolkit 是渲染框架依赖，留 ui 层保命令包框架无关（N36）；补全靠 prompt_toolkit 原生菜单（不自造 UI） |
| 老命令处理 | **归并进注册中心**：/new//sessions//resume→/session 子命令，/provider//exit 独立登记（用户拍板） | 单一分发路径无旁路；避免 /new vs /clear、/sessions vs /session 概念重复 |
| /clear 语义 | **清空当前会话上下文**（messages 归零、留同一 id，用户拍板） | 对标 Claude Code /clear；与 /session new（开新 id）职责区分 |
| 命令动作深度 | **展示为主 + /permission 切档**（不做增删改子命令，用户拍板） | 契合「本步不做命令级权限/自定义」边界；/memory//status 只读、/permission 复用 set_mode |
| 命令类型用途 | **元数据 + 约束分发**（提示词类经 send_user_message 跑 AI） | 类型既供 /help 分类，也让提示词类有统一触发路径；本地/界面类绝不发请求 |
| Tab vs Shift+Tab | **Tab 补全、Shift+Tab 仍切权限模式**（既有） | 二者不冲突；补全只在敲命令名（无空格）时触发 |
| 版本 | **v0.10**（用户拍板，非 1.0） | 继续 0.x；1.0 留给 Skill 系统（用户自定义命令/动态提示词/命令级权限） |

## v0.10 风险与边界

1. **R1 归并回归**：把临时手写分发改成注册中心可能改动既有命令语义。对策：归并后 `/help`/`/provider`/`/exit`/`/plan`/`/do`/`/compact` 逐条回归断言行为不变；`/new`/`/sessions`/`/resume` 的等价由 `/session` 子命令测试覆盖；既有 repl 测试零修改保持绿（N35）。列为回归检查点。
2. **R2 CommandContext 协议过宽**：~14 法的协议面若膨胀会侵蚀解耦价值。对策：协议只收命令**实需**能力、不塞 REPL 内部状态；新命令若需新能力须显式加协议方法（评审把关），不开「给命令一个 REPL 引用」的后门。列为评审检查点。
3. **R3 补全器与多行输入交互**：prompt_toolkit `multiline=True` + completer 可能在换行/参数态误触发。对策：`get_completions` 仅在 `text_before_cursor` 以 `/` 开头且无空格时产出候选，其余早返回；真终端 👁 验观感。
4. **R4 提示词类命令再入**：`/review` 在命令分发中调 `_chat_once` 是嵌套调用。对策：既有 `/plan <text>` 已在 `_cmd_plan` 内调 `_chat_once`（先例存在），分发在 `run()` 顶层、`_chat_once` 返回后正常回到循环，无重入风险。列为回归核验。
5. **R5 启动 panic 误伤**：别名冲突 raise 会让 `build_app` 失败、整个程序起不来。对策：这正是 spec 要的语义（N37）——内置命令集固定、冲突是开发期 bug 应当场炸；测试覆盖「正常 12 命令不冲突」+「故意冲突 raise」两面。
6. **R6 与 v0.5 缓存无关但需确认**：命令分发不进 system 提示、不改缓存前缀。对策：命令系统纯交互层、不碰 `build_system_prompt`；无命令输入时 system 与 v0.9 字节级等价（N35）。已知无交互，列回归冒烟。

## v0.10 不做的事（边界）

- 不做**用户自定义命令**（命令集启动时由内置注册中心固定，用户不可在配置增删——留给 Skill 系统）。
- 不做**动态生成提示词**（提示词类预设文案为内置固定模板，不做按上下文动态拼装/用户可编辑模板）。
- 不做**命令级权限控制**（命令不进 v0.6 权限引擎；本地命令用户主动触发、天然可信）。
- 不做**命令历史 / 参数补全**（Tab 只补命令名，不补子命令参数或历史值；输入历史仍走既有上下键）。
- 不做**全屏命令面板 / 模糊搜索**（补全沿用 prompt_toolkit 原生菜单，不做 fzf 式模糊匹配或独立面板 UI）。

# v0.11 新增设计（F84–F90：Skill 系统 — 定义/三层发现/两阶段加载/双执行模式/白名单/斜杠注册）

> 技术方向：新增一个**纯数据 + 加载叶子包** `src/wentian/skills/`（`base`/`loader`/`registry` 三件，**零 `rich`/`prompt_toolkit`/后端 SDK/agent/repl import**），把 Skill 文件从三层目录发现、解析 frontmatter+正文、占位符替换；一个**系统级 `load_skill` 工具**（住 `tools/`，经注入的鸭子**激活句柄**操作，不硬依赖 repl）做按需加载；激活态的编排（每轮注入、白名单收窄、isolated 子对话）落在 **repl 装配层**的一个 `SkillActivator`（它持 AgentLoop/provider，是命令包/skills 包都不该碰的「装配缝」）。**最大化复用**：菜单复用 v0.5 系统提示「已激活 Skill」槽（改渲菜单）、激活正文复用 v0.8 `request_decorator`（`<system-reminder>` 通道）、白名单复用 v0.4/v0.6 `allowed_tools`、斜杠复用 v0.10 命令注册中心、isolated 子对话复用 v0.4 `AgentLoop`——**零重造**。版本升 `0.11.0`，零新增依赖。

## 架构增量（v0.11）

```
skills/base.py      ──► 新叶子：Skill dataclass（name/description/allowed_tools/mode/history/model/body/source）+ SkillMode 枚举（C100）
skills/loader.py    ──► 新叶子：discover_skills(项目/用户/内置三层) + parse_skill(frontmatter+正文) + render_body(占位符替换)（C101）
skills/registry.py  ──► 新叶子：SkillRegistry——按 name 存（高层覆盖低层）、list()/get(name)、纯数据（C102）
tools/skill_tool.py ──► 新：LoadSkillTool(Tool)——系统级、经鸭子 activator 句柄 activate(name,args)→str；run 不碰 repl 具体类（C103）
（repl 层）SkillActivator ──► 新（装配缝，住 repl.py 或 repl 邻接模块）：激活集 + allowed_tools()/active_bodies() + activate() 双模式 + isolated 子 AgentLoop（worker 线程自起事件循环）（C104）
prompt/system.py    ──► 改：PromptContext 加 available_skills；_render_active_skills 渲「可用 Skill」菜单（name+desc）（C105）
prompt/reminders.py ──► 改：build_request_decorator 增 active_skill_bodies 源（每轮 live 读），把激活正文经 <system-reminder> 贴最新 user 消息后（C106）
config.py           ──► 改：SkillsConfig（enabled，mirror MemoryConfig）+ _parse_block（C107）
cli.py / repl.py    ──► 改：build_app 发现 Skill→白名单校验(排 MCP 后) fail-fast→注 PromptContext.available_skills→建 SkillActivator+LoadSkillTool 注册→Skill 自动注册为 PROMPT 命令进 v0.10 registry(冲突策略)→/skills //skills reload；/clear //session new 清激活集；版本 0.11.0（C107）
skills/builtin/*.md ──► 新：内置 commit/review/test 三样板（importlib.resources 打包）（C107）
```

- **分层依赖铁律（v0.11 延续 v0.9 memory 包纪律）**：`skills/` 是**纯数据 + 加载叶子**——`base`/`registry` 只 `dataclasses`/`enum`/`typing`；`loader` 只 stdlib（`pathlib`/`importlib.resources`/手解析 YAML frontmatter，**不引第三方 YAML**——沿用既有 frontmatter 解析风格）。`skills/` 包**零 import** `agent`/`providers`/`repl`/`commands`/`rich`/`prompt_toolkit`。`isolated` 子对话编排（嵌套 `AgentLoop`）住 **repl 装配层** `SkillActivator`，不下放进 `skills`。`LoadSkillTool` 住 `tools/`（与既有工具同层），只经**鸭子 activator 句柄**（调 `.activate(name, args) -> str`）操作，**不 import repl 具体类**（既有「tools 不依赖 repl」惯例延续，activator 鸭子注入）。
- **核心不变量（v0.11 新增）：① 菜单稳定可缓存**——「可用 Skill」菜单（name+desc）进系统提示前缀、对话内逐轮稳定（只随 `/skills reload`//`clear`/会话切换变），不破 v0.5 缓存断点（N45）。**② 激活正文不持久化**——激活 Skill 完整正文每轮经 `<system-reminder>` 注入最新 user 消息处、**绝不写回 store messages**（沿用 v0.8 decorator 不 mutate 契约，N45）。**③ 白名单收窄复用既有**——激活集→AgentLoop `allowed_tools`（声明过滤 + 运行时拦截双层，同计划模式）；任一激活 Skill 不声明白名单则不收窄；`load_skill` 恒含；激活集空→全工具（N46）。**④ 加载工具系统级豁免**——`load_skill` 不受任何 Skill 白名单约束、恒在可用集（N47）。**⑤ 启动 fail-fast**——白名单引用不存在工具在 `build_app`（**MCP 发现之后**）`raise`、进程退出；区别于解析失败的静默跳过（N44）。**⑥ skills=None 回退**——未注入 Skill 注册表/激活器时行为与 v0.10 字节级等价（N45，同既有「None⇒旧行为」注入惯例）。

## 核心数据结构（v0.11 新增）

```python
# skills/base.py
class SkillMode(Enum):
    SHARED   = "shared"     # 共享当前对话：激活进集、每轮注入正文、收窄白名单、结果留主历史（默认）
    ISOLATED = "isolated"   # 独立子对话：worker 线程跑嵌套 AgentLoop、末条助手正文回流为工具结果

@dataclass(frozen=True)
class Skill:
    name: str                              # 唯一标识、无斜杠、小写（slash 命令名来源）
    description: str                       # 一句话，进「可用 Skill」菜单 + /help
    body: str                              # Markdown 正文（SOP；含未替换的 $ARGUMENTS/$1 占位符）
    mode: SkillMode = SkillMode.SHARED
    allowed_tools: tuple[str, ...] | None = None   # None ⇒ 不收窄；() 视为 None（空列表不收窄）
    history: int = 0                       # 仅 isolated：带多少条主历史进子对话
    model: str | None = None               # 模型覆盖（缺省复用当前）
    source: str = "builtin"                # "project" / "user" / "builtin"（来源层，/skills 展示）

# skills/loader.py
def discover_skills(project_dir, user_dir) -> SkillRegistry: ...
#   三层扫描（项目 .wentian/skills > 用户 ~/.config/wentian/skills > 内置 importlib.resources）
#   同 name 高层覆盖；单文件 parse 失败静默跳过；返回 SkillRegistry
def parse_skill(text: str, *, name_hint: str | None, source: str) -> Skill | None: ...
#   切 frontmatter（--- 包裹的 YAML）/正文；缺 name 或坏 YAML → None（跳过）
def render_body(body: str, args: str) -> str: ...
#   $ARGUMENTS→整串；$1/$2…→空格切分的位置参数；无对应→""（字面替换、不执行）

# skills/registry.py
class SkillRegistry:
    def get(self, name: str) -> Skill | None: ...
    def list(self) -> list[Skill]: ...                 # 按 name 排序，供菜单/补全/​/skills
    def menu(self) -> tuple[tuple[str, str], ...]: ...  # (name, description) 元组，喂 PromptContext.available_skills

# repl 层 SkillActivator（装配缝；持 SkillRegistry + provider/AgentLoop 工厂）
class SkillActivator:
    def activate(self, name: str, args: str) -> str:    # load_skill 工具与 /<name> 命令的统一入口
        # SHARED:  渲正文→加入激活集（name→rendered_body, allowed_tools）→返回「已激活 <name>，指令已注入」
        # ISOLATED: worker 线程跑嵌套 AgentLoop（末 history 条 + 正文 + 白名单 + model）→返回子对话末条助手正文
    def active_bodies(self) -> list[tuple[str, str]]: ...  # [(name, rendered_body)]，reminders 每轮 live 读
    def allowed_tools(self) -> frozenset[str] | None: ...   # 激活集白名单并集 ∪ {load_skill}；任一不限/空集→None
    def clear(self) -> None: ...                            # /clear //session new 调
```

## 组件设计（C100–C107）

### C100 Skill 数据模型 `skills/base.py`（叶子）（F84）
- **职责**：定义 `SkillMode` 枚举与 `Skill` 不可变 dataclass（Skill 的唯一形状）。
- **依赖与分层**：叶子——只 `dataclasses`/`enum`/`typing`。
- **测法**：构造 `Skill` 断言字段齐备/`frozen`/默认值（mode=SHARED、history=0、allowed_tools=None）；纯数据离线（AC105）。

### C101 加载器 `skills/loader.py`（叶子，stdlib）（F84/F85）
- **职责**：`discover_skills`（三层发现 + 同名高层覆盖 + 单文件解析失败跳过）、`parse_skill`（手解析 `---` 包裹 YAML frontmatter + 正文、缺 name/坏 YAML→None）、`render_body`（`$ARGUMENTS`/`$1` 占位符字面替换）。单文件 `<name>.md` 与目录型 `<name>/SKILL.md` 等价（目录型 `tools/` 子目录识别但不加载）。
- **依赖与分层**：叶子——`pathlib`/`importlib.resources`/`re` 手解析（**不引第三方 YAML**，沿用 v0.9 指令文件 frontmatter 风格）；零 import 业务模块。
- **测法**：三层覆盖取高层；坏 YAML/缺 name 跳过、合法照常；`$ARGUMENTS`/`$1`/`$2` 替换；单文件 vs 目录型等价（AC105/AC106 临时目录）。

### C102 Skill 注册表 `skills/registry.py`（叶子）（F85）
- **职责**：`SkillRegistry`——按 `name` 存（`discover` 时高层覆盖低层）、`get`/`list`/`menu`，纯数据只读。
- **依赖与分层**：叶子——只 stdlib + import 同包 `base`。
- **测法**：注册/覆盖后 `get`/`list`/`menu` 正确；按 name 排序稳定（AC106）。

### C103 加载工具 `tools/skill_tool.py`（F86）
- **职责**：`LoadSkillTool(Tool)`——`name="load_skill"`、参数 `{name: str, args?: str}`、`category=READ_ONLY`（不进副作用确认；激活是上下文操作）。`run(args)` 调注入的鸭子 `self._activator.activate(name, args)` 返回其字符串结果。
- **系统级豁免**：该工具**不来自任何 Skill 白名单**，由 `build_app` 直接注册进工具 registry，且 `SkillActivator.allowed_tools()` 恒把 `"load_skill"` 并入可用集——即便白名单收窄也在。
- **依赖与分层**：住 `tools/`（同既有工具）；只经鸭子 `activator`（`.activate(name,args)->str`），**不 import repl/agent/skills 具体类**（activator 注入）。
- **测法**：注入假 activator，`run({"name":"x","args":"y"})` 断言转调 `activate("x","y")` 并回传其结果；参数缺失走结构化错误（AC107/AC111）。

### C104 激活器 `SkillActivator`（repl 装配层）（F87/F89）
- **职责**：Skill 激活的**唯一编排点**，`load_skill` 工具与 `/<name>` 命令共用：
  - **SHARED**：`render_body(skill.body, args)`→把 `(name, rendered, skill.allowed_tools)` 加进激活集（去重按 name，重复激活刷新 args）；返回「已激活 Skill `<name>`，指令已注入上下文」。
  - **ISOLATED**：取主 session 末 `skill.history` 条 messages 作子起始 → 子 system = 主 system + `# Skill: <name>\n<rendered>` → 子 tools = 白名单（同 SHARED 规则）→ 子 model = `skill.model or 当前` → 在**独立 worker 线程**内 `asyncio.run` 一个新 `AgentLoop`（自建/传入 provider 实例、线程安全，仿 v0.9 抽取）→ 收集子对话末条助手正文，作为返回值（→ `load_skill` 工具结果 / `/<name>` 命令的 `ctx.print`）。**不进激活集**。
  - `active_bodies()`/`allowed_tools()`/`clear()` 供 reminders、AgentLoop 装配、`/clear` 调。
- **依赖与分层**：住 repl 层（持 `AgentLoop`/provider/SkillRegistry）；`skills` 包与 `tools` 包都**不**依赖它（鸭子注入）。worker 线程自起事件循环——避免与主 `_chat_once` 的 `asyncio.run` 嵌套冲突（无论 `activate` 由执行器工作线程还是 REPL 主线程触发都安全）。
- **测法**：假 provider——SHARED `activate` 进集 + 返回确认 + `active_bodies()`/`allowed_tools()` 反映；ISOLATED `activate` 跑子对话（假 provider 返回固定文本）返回末条助手正文、激活集不变、线程 join 干净（AC108/AC109/AC111）。

### C105 系统提示菜单 `prompt/system.py`（改）（F86）
- **职责**：`PromptContext` 增 `available_skills: tuple[tuple[str, str], ...] = ()`；`_render_active_skills(ctx)` 由「恒空」改为：非空 → 渲「# 可用 Skill\n用 `load_skill` 加载完整指令。\n- `<name>`：`<desc>`…」；空 → `""`（拼装器过滤、无残渣）。
- **依赖与分层**：纯函数模块不变（零 SDK）；菜单只读 `available_skills`，启动注入一次、对话内稳定（保缓存前缀）。
- **测法**：`available_skills` 非空→模块含各 name+desc；空→模块省略、拼装无空行残渣（AC107）。

### C106 激活正文注入 `prompt/reminders.py`（改）（F87）
- **职责**：`build_request_decorator(...)` 增一个 `active_skill_bodies: Callable[[], list[tuple[str,str]]] | None`（**每轮 live 读**激活集，非快照——支持模型在循环中途 `load_skill` 后下一轮即注入）；装饰器在既有 env/switch 提醒之后、把每个激活 Skill 的 `<system-reminder># 已激活 Skill: <name>\n<rendered>` 追加到最新 user 消息（**深拷不 mutate、不写回**，沿用既有契约）。
- **依赖与分层**：纯函数；与 v0.8 decorator 同款不持久化纪律。
- **测法**：给定激活源 → 装饰后 messages 末 user 含两段 reminder 正文；入参 messages 不被改；空激活源 → 与 v0.8 行为一致（AC108）。

### C107 装配 `config.py`/`cli.py`/`repl.py` + 内置样板 + 版本（改）（F85/F88/F90/N45/N48）
- **职责**：
  1. **`config.py`**：`@dataclass(frozen=True) class SkillsConfig: enabled: bool = True`（mirror `MemoryConfig`）；进 `Config`；`_parse_block` 缺块全默认。
  2. **`cli.build_app`**：① `discover_skills(项目, 用户, 内置)`（`enabled=false`→空注册表、不注入）；② **白名单校验**——`skills` 发现**排在 MCP `discover_and_register` 之后**，对每个 Skill 的 `allowed_tools` 校验都在工具 registry 内，否则 `raise`（fail-fast、指明 Skill+工具名）；③ 注 `PromptContext(available_skills=registry.menu())`；④ 注册 `LoadSkillTool`（系统级）进工具 registry；⑤ 建 `SkillActivator(registry, provider/loop 工厂, ...)`；⑥ **Skill→PROMPT 命令**：把每个 Skill 包成 `CommandSpec(type=PROMPT, handler=_skill_handler(name))` 注册进 v0.10 命令 registry——**冲突策略**：同名提示词类命令（`/review`）先移除再注册（Skill 替换）、同名控制类命令（保护集）跳过 + 告警、自由名直接注册；⑦ 注册 `/skills`（列举）、`/skills reload`（重扫 + 重建 + 重校验，失败保旧不崩）；⑧ 版本 `0.11.0`。
  3. **`repl.py`**：`_chat_once` 每轮把 `activator.active_bodies` 喂 `build_request_decorator`、把 `activator.allowed_tools()` 喂 `AgentLoop.run(allowed_tools=...)`；`/clear` 与 `/session new`（既有 `clear_context`/`new_session`）末尾调 `activator.clear()`；`_skill_handler(name)(ctx, args)`：SHARED→`activator.activate`+`ctx.send_user_message(args or 触发串)` 跑一轮；ISOLATED→`ctx.print(activator.activate(name,args))`。`skills`/`activator` 为可选注入，`None`⇒回退 v0.10。
  4. **`skills/builtin/*.md`**：`commit.md`/`review.md`/`test.md`（`importlib.resources` 打包，`pyproject` include）。
- **测法**：`build_app` 后 PromptContext 持菜单、工具 registry 含 `load_skill`、命令 registry 含各 `/<skill>`；白名单错工具→启动 `raise`（MCP 工具在场则通过）；`/skills`/`/skills reload` 行为；`/clear` 清激活集；`skills.enabled:false`/无目录→回退 v0.10；版本 `0.11.0`；无配置冒烟退出码 0（AC110/AC112/AC113/AC114/AC115）。

## 模块交互（激活/注入/收窄/独立模式的数据流，v0.11 视角）

```
build_app():
  skills = discover_skills(proj, user)            # 三层发现，enabled=false→空（C101）
  for s in skills: validate s.allowed_tools ⊆ tool_registry  # 排 MCP 之后；非法→raise panic（C107/F88）
  tool_registry.register(LoadSkillTool(activator)) # 系统级（C103）
  PromptContext(available_skills=skills.menu())    # 菜单进系统提示稳定槽（C105）
  activator = SkillActivator(skills, loop_factory, provider, get_main_messages, get_main_system)
  for s in skills: cmd_registry.register(skill→PROMPT CommandSpec)  # 冲突策略（C107/F90）
  cmd_registry.register(/skills, /skills reload)

模型调 load_skill(name,args)（循环中途）：
  executor → LoadSkillTool.run → activator.activate(name,args)
    SHARED  : 渲正文→进激活集→返回「已激活」字符串（→工具结果）；下一轮 reminders live 读到→注入正文 + allowed_tools 收窄
    ISOLATED: worker 线程 asyncio.run 子 AgentLoop（末 history 条+正文+白名单+model）→末条助手正文（→工具结果，落主历史一条 tool result）

用户敲 /<name> args：
  _dispatch_command → cmd_registry.lookup(name) → _skill_handler(name)(ctx,args)
    SHARED  : activator.activate + ctx.send_user_message(args or 触发) → _chat_once 跑一轮（正文经 reminder 注入 + 白名单收窄）
    ISOLATED: ctx.print(activator.activate(name,args))

每轮 _chat_once 请求前（C106/C107）：
  decorator = build_request_decorator(env, plan_mode, active_skill_bodies=activator.active_bodies)
  AgentLoop.run(messages, system, tools, request_decorator=decorator, allowed_tools=activator.allowed_tools())
  → 激活正文经 <system-reminder> 贴最新 user 消息（不写回 store）；白名单声明过滤 + 运行时拦截

/clear 或 /session new：clear_context()/new_session() 末尾 activator.clear()  # 激活集清空（F90）
```

## 测试策略（v0.11 增量，全部离线）

| 组件 | 测法 | 关键用例 |
|------|------|----------|
| skills/base | 纯数据 | Skill 字段齐备 frozen；默认 mode=SHARED/history=0/allowed_tools=None（AC105）|
| skills/loader | 临时目录 + 假内置 | 三层覆盖取高层；坏 YAML/缺 name 跳过不阻断；$ARGUMENTS/$1/$2 替换；单文件 vs 目录型等价（AC105/AC106）|
| skills/registry | 构造假 Skill | get/list/menu；同名覆盖；按 name 排序（AC106）|
| tools/skill_tool | 假 activator | run 转调 activate(name,args) 回传结果；参数缺失结构化错误（AC107/AC111）|
| SkillActivator（shared）| 假 provider | activate 进集 + 确认串；active_bodies/allowed_tools 反映；多 Skill 并存并集；任一不限则不限（AC108/AC109）|
| SkillActivator（isolated）| 假 provider 固定文本 | 子对话末条助手正文为返回值；激活集不变；线程 join 干净；history 条带入（AC111）|
| prompt/system（菜单）| PromptContext | available_skills→「可用 Skill」含 name+desc；空→省略无残渣（AC107）|
| prompt/reminders（注入）| 假激活源 | 装饰后末 user 含各激活正文 reminder；入参不 mutate；空源=v0.8 行为（AC108）|
| repl（白名单收窄）| 假 provider | 激活→AgentLoop allowed_tools=并集+load_skill；空→None；不限 Skill→不收窄（AC109）|
| repl（/skills //reload）| 临时目录改文件 | /skills 列举零请求；reload 增删生效；reload 校验失败保旧不崩（AC113）|
| repl（清激活）| 假 provider | /clear //session new 后激活集空（AC114）|
| cli/build_app（fail-fast）| 注入冲突/错工具 | 白名单错工具启动 raise（MCP 在场通过）；Skill→PROMPT 命令冲突策略三分支；版本 0.11.0；无 skills 回退 v0.10（AC110/AC112/AC115）|
| 分层 import 断言 | ast/静态 | skills/ 零 agent/provider/repl/commands/rich/prompt_toolkit；load_skill 工具不 import repl 具体类（AC115）|
| 内置三样板 | 发现 + 正文核对 | commit/review/test 被发现注册；commit 正文含「无 Co-Authored-By/不主动 push」约束（AC114）|
| 端到端（真终端/真 provider）| checklist 人工 | 🌐 真 provider 激活一个 Skill 跑一轮观察正文生效；👁 /skills 菜单观感、isolated 回流 |

## v0.11 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 专属工具脚本 | **本版只认结构 + 白名单，不执行 `tools/` 脚本**（用户拍板 A） | 降本版风险、无任意代码执行面；脚本执行连同分发/版本留后续章节 |
| 激活正文位置 | **每轮 `<system-reminder>` 注入（env 通道）**，菜单留系统提示稳定槽（用户拍板 A） | 保 v0.5 缓存前缀稳定；契合「钉在环境上下文最显眼、每轮重建」；不写回历史 |
| 热更新触发 | **手动 `/skills reload`**（用户拍板 A） | 贴合既有「读一次、零热重载」纪律；零后台线程/零新依赖 |
| 白名单语义 | **激活集白名单并集 + load_skill 恒含；任一不限则不限；空集全工具** | 复用 v0.4/v0.6 allowed_tools；并集对多 Skill 安全、不限优先避免误锁 |
| 白名单错工具 | **启动 fail-fast `raise`（MCP 之后）**，区别解析失败静默跳过 | 配置硬错误应当场炸（镜像 v0.10 命令冲突/v0.3 工具重名 raise）；坏文件不该拖垮整体 |
| isolated 子循环 | **worker 线程自起事件循环 + 末条助手正文回流**，不额外 LLM 摘要 | 避免嵌套 asyncio.run；省一次调用；正文可指示子 agent 末尾总结 |
| Skill→命令冲突 | **替换提示词类（/review）/ 保护控制类跳过告警 / 自由名直接注册** | 避免 v0.10 注册中心启动 panic；富化优先、控制命令受保护、能力不丢（仍 load_skill 可达）|
| 激活生命周期 | **激活即 sticky，至 /clear //session new 清空**（设计拍板） | 契合「多 Skill 同时激活、清空对话清激活」；逐 Skill 关闭留后续 |
| 占位符语法 | **`$ARGUMENTS` + `$1/$2`**（用户拍板，对齐 Claude Code） | 用户熟悉；字面替换不执行（N47）|
| 加载工具豁免 | **load_skill 系统级、不受白名单约束、恒在可用集** | 否则收窄后无法再加载/切换 Skill（用户明确要求）|
| 版本 | **v0.11**（Skill 系统） | 续 0.x；与并行 v0.12-hooks 分支号错开避免合并冲突；ID 块 F84+/N44+/AC105+/C100+/T126+ 避让 v0.12 占用 |

## v0.11 风险与边界

1. **R1 与 v0.10 命令注册中心的冲突 panic**：Skill 自动注册成命令，撞 `/review` 等内置命令会触发 v0.10 启动 `raise`。对策：注册前按冲突策略处理（替换提示词类 / 保护控制类跳过告警 / 自由名注册）——**绝不让 Skill 把 `build_app` 炸掉**；测试覆盖三分支（AC112）。列为评审检查点。
2. **R2 isolated 子对话嵌套事件循环**：`load_skill` 可能被执行器工作线程或 REPL 主线程触发，直接 `asyncio.run` 有「已在运行的事件循环」风险。对策：子循环固定在**独立 worker 线程**起自己的事件循环、join 回收；线程安全用独立 provider 实例（仿 v0.9）。列为回归核验。
3. **R3 激活正文 live 读 vs 快照**：模型在循环中途 `load_skill` 后，本轮内构建的 decorator 若是快照则下一轮注入不到。对策：decorator 持 `active_bodies` **可调用源、每轮 live 读**（C106）；测试覆盖「中途激活下一轮即注入」。
4. **R4 白名单收窄误伤 load_skill**：收窄后若 `load_skill` 不在可用集，模型无法再加载/切换 Skill。对策：`allowed_tools()` 恒并入 `load_skill`；声明过滤侧也保证它在声明里（N47）。列为回归核验。
5. **R5 缓存前缀稳定 + 内置样板默认在**：菜单进系统提示，若每轮变会破 v0.5 缓存。对策：菜单只随 `/skills reload`//`clear`/会话切换变、对话内稳定；激活正文走 env 通道（非 system 前缀）。**注意**：内置 commit/review/test 三样板随包分发、默认 `enabled:true` 即被发现，故默认系统提示**含**「可用 Skill」菜单、工具集**含** `load_skill`（有意的 v0.11 新默认，既有「6 工具」断言更新为 7）；**字节级回退 v0.10 的闸是 `skills.enabled:false`**（activator None ⇒ 无菜单、无 load_skill、与 v0.10 一致，N45）。列回归冒烟。
6. **R6 分层越界**：isolated 编排需要 AgentLoop/provider，易把 agent 依赖漏进 `skills` 包。对策：编排住 repl 层 `SkillActivator`、`skills` 包零 agent/provider/repl import（ast 断言，AC115）；`LoadSkillTool` 经鸭子 activator，不 import repl 具体类。列为评审 + 自动 import 断言检查点。
7. **R7 与并行 v0.12-hooks 的合并**：v0.11 与 v0.12 同自 v0.10 分叉、都改 `cli.py`/`repl.py`/`config.py`。对策：ID 块错开（F84+/AC105+/C100+/T126+ 避让 v0.12 的 F77–83 等）消除编号冲突；装配改动尽量局部、追加式（不重排既有装配序）以减小文本合并冲突。**合并顺序与冲突解决由用户在集成阶段处理**（本分支只保证自身内聚 + 自身全绿）。

## v0.11 不做的事（边界）

- 不做**专属工具脚本的加载执行**（目录型 `tools/` 仅识别结构）。
- 不做 **Skill 市场分发 / 安装 / 更新 / 版本管理 / 依赖声明**。
- 不做 **Skill 实时热重载**（手动 `/skills reload` 或重启；不引文件监听线程）。
- 不做 **Skill 级权限控制 / 沙箱**（白名单只收窄可见集、非安全边界；正文是用户主动加载的可信内容）。
- 不做 **Skill 正文向量检索 / 按需召回**（菜单全量注入 name+desc）。
- 不做 **嵌套 Skill 激活**（isolated 子对话内不提供 load_skill、不递归发现）。
- 不做 **逐 Skill 手动关闭**（激活 sticky、`/clear`//`session new` 一并清空；逐个关留后续）。
