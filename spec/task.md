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
