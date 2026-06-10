# WentianCode（文天）v0.1 Plan

> 基于已批准的 `spec.md`（F1–F12，AC1–AC10）。本文档定义"怎么做"：架构、接口、数据结构、模块交互与测试策略。
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
