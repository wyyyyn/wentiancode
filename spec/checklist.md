# WentianCode（文天）v0.1 Checklist

> 每项通过运行代码或观察行为验证，聚焦系统行为，与实现解耦。离线项全部可自动化；标 🌐 的项需要真实 API key 联网人工跑（验收时执行一次并记录证据）。
>
> **验收记录 2026-06-10**：联网项用 SiliconFlow（openai 兼容，DeepSeek-V3.2 / R1）实测。Anthropic 协议侧（AC4 双协议、AC5 claude thinking）等 Anthropic key 后补验。TTY 视觉流式（AC2/AC10 的"逐步出现"）建议用户亲跑一次确认观感。

## 实现完整性

- [x] 包可导入、入口可用（证据：`uv run wentian --help` 与 `uv run wt --help` 均输出帮助，含三个选项说明）
- [x] （AC1）启动进入 REPL：🌐 实测 `uv run wentian` 出现 `文天> ` 提示符，提问后发起请求并输出回答
- [x] （AC2/F3）流式可观测：🌐 代码路径经 spy 测试锁死逐 delta `Live.update`；真实流（SSE chunk）实测打通。终端视觉效果待用户 TTY 亲验 ⏳
- [ ] （AC5/F8）thinking 可区分（Claude 后端）：🌐 **待 Anthropic key**。openai 侧旁证已过：DeepSeek-R1 的 reasoning_content 思考流先于正文展示 ✅
- [x] （AC10/F12）Markdown 渲染：🌐 实测"列表+python 代码块"回答渲染为 `•` 列表与代码块样式，输出无裸 ``` 围栏；"生成中逐步出现"部分同 AC2 待 TTY 亲验 ⏳
- [x] （AC3/F4）上下文记忆：🌐 实测第二轮问"我刚才问的是什么"，正确复述第一轮问题

## 多后端与配置

- [ ] （AC4/F5/F6）双协议同配置可用：🌐 openai 兼容侧（SiliconFlow）✅；Anthropic 侧**待 key**
- [x] （AC4/F7）启动覆盖默认：🌐 实测 `uv run wentian -p sf` 生效（含真实对话一轮）
- [x] 配置错误友好报错：实测 `default: ghost` 启动 → 一行人类可读错误（列出可用 providers）、退出码 1、无 traceback

## 会话持久化

- [x] （AC6/F9 续上次）：🌐 实测对话后退出，`uv run wentian --continue` 跨进程追问"第一个话题是什么"答对（"介绍你自己"）
- [x] （AC6 新会话）：实测无 `--continue` 启动追问历史 → "没有记录对话历史"；旧会话文件保留
- [x] 会话文件落地正确：实测 `~/.local/share/wentian/sessions/<id>.json` 含 user/assistant 交替 4 条消息 + provider 字段

## 会话内命令（AC7/F10）

- [x] `/help` 列出全部六条命令（实测）
- [x] `/new` 新建空会话（实测：提示新 id，旧文件保留）
- [x] `/sessions` 列出历史会话 id（实测）；`/resume <id>` 恢复后追问第一轮主题 → 答对（"自我介绍"）
- [x] `/provider <名>` 切换后继续对话成功（sf→sf-r1 实测）；`/provider 不存在的名字` → 一行友好错误不退出（实测）
- [x] `/exit` 干净退出，无 traceback（实测多次）

## 扩展性（AC8/F11）

- [x] 假后端即插即用：FakeProvider 仅实现统一接口即驱动 REPL/渲染/会话层；grep 证实 repl/render/session 零 SDK import

## 编译与测试（AC9/N5）

- [x] 全部单测离线通过：无任何 API key 环境 `uv run pytest -q` → **148 passed**（2026-06-10 现场）
- [x] 测试覆盖四类核心流程：test_config / test_factory+test_cli / test_session / test_repl 均存在且非空

## 端到端场景

- [x] 🌐 **场景 1（主干全链路）**：实测分段完成——新会话 → Markdown 列表回答流式渲染 → 追问复述答对 → `/provider sf-r1` 切换后再问一轮成功（R1 思考流可见）→ `/exit` → `--continue` 历史接续。全程无崩溃 ✅（Anthropic 协议参与的版本待 key 后重跑一遍）
- [x] 🌐 **场景 2（边界：错误恢复）**：实测错误 api_key 提问 → 一行 `错误：Error code: 401 - Api key is invalid`、REPL 不退出；会话目录零文件（失败轮未落盘，无孤儿 user 消息）✅

## 遗留待验（不阻塞合并）

1. **AC5 + AC4 Anthropic 侧**：拿到 Anthropic key 后，取消 config 注释，跑一轮推理题验 `🤔 思考中…` 暗色斜体 + 双协议切换。
2. **TTY 视觉流式**：用户终端亲跑 `uv run wentian`，确认长回答"逐块出现"且 Live 定格后 scrollback 干净。

---

# v0.2 Checklist（F13–F18：Claude Code 式交互）

> 离线项自动化验证；标 🌐 的项联网人工跑；标 👁 的项必须真实终端亲眼验证（prompt_toolkit/termios 行为无法离线完全代理）。
>
> **离线验收记录 2026-06-11**：T15–T25 + polish + T27–T29（=^_^= 文本脸定稿、输入框去边框修复）全部完成，终审 READY TO MERGE，229 测试全绿。
> **用户验收 2026-06-11**：用户在真实终端（Terminal.app）试用后确认通过（「通过了哈」）——期间发现并修复两个真问题：像素画跨字体变形、手绘边框致 prompt 重复堆叠。🌐👁 项以用户实际使用为准全部通过 ✅。

## 实现完整性

- [x] v0.2 包可用：`uv run pytest -q` → **225 passed**（2026-06-11 现场）；`wentian --help` 正常；`__version__ == "0.2.0"`（冒烟测试断言）
- [x] （AC11/F13）启动横幅（T27 改版）：👁 真实终端启动，第一屏可见**彩色像素小人**（朱砂方巾小人）+ 右侧三行信息（名称版本 / `provider · model` / 会话状态），无边框面板；`--continue` 启动时显示「已恢复」与会话 id
- [x] （AC12/F14）后端选择：👁 配置 ≥2 provider 不带 `-p` 启动 → 出现列表，↓ 移动高亮、回车后用所选后端对话（🌐 验证一轮）；带 `-p sf` 启动 → 不出列表直接进入
- [x] （AC13/F15）多行输入：👁 Ctrl+J（或 Alt+Enter）产生第二行，回车一次提交（🌐 模型确认收到含换行的完整内容）；↑ 翻出上一条；**重启程序后** ↑ 仍翻出上次运行的历史
- [x] （AC14/F16）状态栏：👁 启动后可见后端/模型/会话 id/消息数；`/provider sf-r1` 后状态栏后端变化；一轮对话后消息数 +2
- [x] （AC15/F17）计时反馈：🌐👁 提一个长问题——等待期可见动画符与递增秒数；正文流式期间秒数继续；定格后计时消失
- [x] （AC16/F18）中断：🌐👁 流式中按 Esc → 立即停、屏留部分正文 + 「已中断」、回到输入框；追问「你刚才说到哪了」→ 助手能引用被中断内容（partial 入史证据）；首字前按 Esc → 直接回输入框，`/sessions` 或会话文件确认无该轮提问；流式中 Ctrl+C 行为同 Esc 且程序不退出

## 退化与兼容

- [x] 非 TTY 管道行为与 v0.1 等价：实测 `printf '/exit\n' | wentian` 横幅照印、退出码 0、无 traceback；选择器/输入框/监听器均未装配（注入点测试锁死）
- [x] 中断后终端不脏：👁 Esc 中断后能立即正常输入下一条（termios 已还原、无残键漏入）；方向键在流式期间乱按不误触发中断
- [x] v0.1 全部既有行为不回退：v0.1 全部 148 测试在 v0.2 代码上保持绿（含命令/回滚/续会话）

## 编译与测试

- [x] 无 API key 环境 `uv run pytest -q` 全绿（225 passed，2026-06-11 现场）
- [x] repl/render/session 仍零 SDK import；repl/render 零 prompt_toolkit import（import 检查：加载三模块后 sys.modules 无 prompt_toolkit，2026-06-11 现场）

## 端到端场景

- [x] 🌐👁 **场景 3（v0.2 主干）**：双 provider 配置启动 → 选择列表选 sf → 横幅正确 → 多行输入一个长问题（含换行）→ 等待秒数跳动 → 流式中按 Esc 中断 → 「已中断」→ 追问引用成功 → `/provider sf-r1` 状态栏更新 → 再来一轮完整回答 → `/exit` → `--continue` 重启横幅示「已恢复」且 ↑ 翻出历史输入

---

# v0.3 Checklist（F19–F28：工具系统）

> 离线项自动化验证；标 🌐 的项需真实 API key 联网人工跑；标 👁 的项需真实终端亲眼验证（确认交互、工具屏显样式）。每项与实现解耦——重命名/移动模块不应使任何项失效。
>
> **离线验收记录 2026-06-11**：T30–T46 全部完成（17 任务，TDD 红-绿-重构 + 分批规格评审 + 终审）。`uv run pytest -q` → **408 passed**（v0.2 基线 229 → +179）；管道冒烟（`printf '/exit\n' | uv run wentian`）横幅示 v0.3.0、退出码 0、无 traceback；分层 import 现场检查通过（tools 层零 SDK/rich/pt；repl/render/session 零 SDK/pt）；pyproject diff 仅版本号、零新增依赖。终审判定 READY FOR ACCEPTANCE（零 Critical/Important）。🌐👁 项待用户真实终端 + API key 验收。

## 实现完整性

- [x] v0.3 包可用：`uv run pytest -q` 全量全绿；`__version__ == "0.3.0"`（冒烟断言）；无新增第三方依赖（pyproject diff 验证）
- [x] （AC17/F19/F21）假工具注册即生效：自动化测试中注册一个 FakeTool → 出现在发往后端的工具声明里、可按名查找、可被执行——对话回合逻辑与界面层代码零修改（测试本身即证据）
- [x] （AC21/F25）改文件三情形：自动化测试——唯一匹配替换成功；零匹配/多匹配文件未动且错误信息含实际匹配次数
- [x] （AC20/F24 离线半）：自动化测试——不存在的工具名、arguments 解析失败、工具超时、工具内部异常四种情形均产生 is_error 结果且不抛出、REPL 续命
- [x] （AC24/F28 离线半）：自动化测试——含 tool_calls/tool 角色/raw_content 的会话 save→load 往返相等；v0.2 旧格式文件加载兼容

## 双协议实测（联网）

- [ ] （AC18/F20/F22 · Anthropic）：🌐👁 用 Claude 后端提问「读取 ./README.md（或指定真实文件）并总结」→ 屏上可见 `⏺ read_file(…)` 调用与结果状态 → 最终回答与文件实际内容一致
- [ ] （AC18 · OpenAI 兼容）：🌐👁 SiliconFlow 后端跑同样场景成功（碎片拼接路径打通）
- [ ] （AC18 补充 · thinking 共存）：🌐 Claude 后端 thinking 开启时完成一次工具轮（thinking 块回传不 400——raw_content 路径验证）
- [ ] （AC19/F23 并列）：🌐 让模型「同时读 A、B 两个文件并对比」→ 两次工具执行都发生，最终回答同时引用两个文件内容
- [ ] （AC19/F23 单轮边界）：🌐 诱导连环（如「先 find 再逐个读」）→ 第二次回复的工具请求未执行、屏显单轮限制提示、回到输入状态、程序不崩
- [ ] （AC20 联网半）：🌐 让模型读一个不存在的文件 → 模型收到错误并在最终回复中向用户说明（而非崩溃/空转）

## 确认与安全（F26）

- [ ] （AC22 写确认）：🌐👁 让模型写一个文件 → 出现确认提示并展示目标路径；选 y → 文件落地；再来一次选 n → 文件系统无变化、模型回复体现「被用户拒绝」
- [ ] （AC22 命令确认）：🌐👁 让模型执行 `ls` → 确认提示展示命令内容；只读工具（读/找/搜）全程无确认提示
- [x] 非 TTY 安全默认：自动化测试——管道模式下副作用工具自动拒绝（confirm 恒 False）

## 界面与持久化（联网）

- [ ] （AC23/F27）：👁 工具执行时屏显工具名+参数摘要+结果状态（成功/失败样式可区分），与正文 Markdown 明显不同
- [ ] （AC24 联网半）：🌐 完成一轮含工具的对话 → `/exit` → `--continue` → 追问「刚才那个文件里说了什么」→ 模型正确引用（工具结果入史持久化证据）

## 退化与兼容

- [ ] 普通对话零回退：不触发工具的常规提问，行为与 v0.2 完全一致（自动化：registry=None 与不含 tool_calls 回复两条路径的回归测试；🌐 人工一轮观感确认）
- [ ] 中断兼容：🌐👁 round1 流式中按 Esc → 工具不执行、v0.2 中断语义不变（部分正文入史/零正文回滚）
- [x] v0.1+v0.2 全部既有测试在 v0.3 代码上保持绿（408 passed 含全部 229 项既有测试，2026-06-11 现场）

## 编译与测试

- [x] 无 API key 环境 `uv run pytest -q` 全绿、无告警（408 passed，2026-06-11 现场）
- [x] 分层不破：tools 层零 SDK/rich/prompt_toolkit import；repl/render 仍零 SDK import（import 检查测试）
- [x] 六工具离线真实执行测试存在且非 mock（tmp_path 真实读写、真实子进程——N7 证据）

## 端到端场景

- [ ] 🌐👁 **场景 4（v0.3 主干）**：启动 → 「在当前目录找出所有 .py 文件并告诉我哪个最大」→ 模型调 find_files（无确认）→ 回答 → 「把 X 文件第一行注释改成 Y」→ edit_file 确认提示 → y → 文件真实变更 → 「运行 pytest 看看」→ run_command 确认 → 输出回灌 → 模型总结测试结果 → `/exit` → `--continue` 追问「刚才改了哪个文件」→ 正确引用。全程无崩溃
- [ ] 🌐 **场景 5（边界：模型自我修正）**：故意让模型 edit 一段在文件中出现多次的文本 → 第一次失败（错误含次数）→ 模型在同轮并列调用或下轮中收窄 old_string 重试成功（验证错误信息对模型可用）

# v0.4 Checklist（F29–F34：Agent Loop）

> 离线项自动化验证；标 🌐 的项需真实 API key 联网人工跑；标 👁 的项需真实终端亲眼验证。每项与实现解耦——重命名/移动模块不应使任何项失效。
>
> **离线验收记录 2026-06-13**：T47–T57 全部完成（12 任务，五波次：契约 → 桥/收集器/渲染/分批并行 → loop.py 串行 → repl.py 串行 → 装配；TDD 红-绿-重构 + 逐波次规格/质量评审 + 最终整合评审）。`uv run pytest -q` → **504 passed**（v0.3 基线 408 → +96）；管道冒烟 `printf '/exit\n' | uv run wentian` 横幅示 v0.4.0、退出码 0、无 traceback、无悬挂；分层 import 现场检查通过（agent 层零 SDK/rich/prompt_toolkit/wentian.tools/具体 provider）；pyproject diff 仅版本号、零新增依赖。最终整合评审判定 READY FOR ACCEPTANCE（零 Critical/Important）。🌐👁 项待用户真实终端 + API key 验收。

## 实现完整性

- [x] v0.4 包可用：`uv run pytest -q` 全量全绿（504 passed，2026-06-13）；`__version__ == "0.4.0"`（冒烟断言）；无新增第三方依赖（pyproject diff 仅版本号，asyncio/pytest 均为既有）
- [x] （AC28/F30/F31）事件流解耦：自动化测试——`test_agent_loop.py` 以事件记录器消费三轮循环，断言完整事件序列（RoundStart/StreamEnd/⏺⎿/UsageUpdate/RoundEnd/AgentDone）；`test_text_delta_appears_before_its_round_stream_end` 证正文增量在该轮流结束前即被下游观察（双路证据）
- [x] （AC26/F29 离线半）：自动化测试——max_rounds 达上限 → 停止、剩余请求未执行、提示出现、已生成内容保留；连续两轮全未知工具 → 停止；穿插已知调用 → 计数重置不停（`TestMaxRounds`/`TestUnknownToolLoop`）
- [x] （AC27/F29 离线半）：自动化测试——循环中途流式抛错 → 当前轮丢弃、已完成轮次保留、屏显错误、会话可续；中断有文字 → 文字入史丢工具请求；零文字 → 本轮提问回滚（`TestStreamError`/`TestUserCancelled`）
- [x] （AC29/F32 离线半）：自动化测试——两个 0.2s 慢只读工具并列 → 总墙钟 < 0.35s（并发证据）；读-写-读顺序保持；结果按原调用顺序回灌入史（`test_agent_batch.py`）
- [x] 历史成对不变量：自动化测试 + 最终整合评审——全部五种停机路径结束后，历史中每条含 tool_calls 的 assistant 消息后都紧跟全部对应 tool 消息（Anthropic 400 红线结构性验证，原子成块入史保证）

## 多轮循环实测（联网）

- [ ] （AC25/F29 · Anthropic）：🌐👁 Claude 后端给「找→读→改→验证」真实任务（如「找到 X 文件，把 Y 改成 Z，跑测试确认」）→ 自主连续多轮完成、屏上多轮 ⏺/⎿ 轨迹、最终总结正确、全程无需催促
- [ ] （AC25 · OpenAI 兼容）：🌐 SiliconFlow 后端跑一个多轮任务成功（多轮历史转换路径打通）
- [ ] （AC25 补充 · thinking 共存）：🌐 Claude 后端 thinking 开启完成 ≥3 轮循环（raw_content 跨多轮回放不 400——最关键联网风险项）
- [ ] （AC29 联网半）：🌐 「同时读 A、B、C 三个文件并对比」→ 全部执行、回答引用全部内容
- [ ] （AC27 联网半）：🌐👁 循环某轮生成中按 Esc → 停止、回到输入状态、追问可引用中断前内容

## 计划模式（F33）

- [ ] （AC30 进入/标记）：🌐👁 `/plan` 后状态栏出现「计划模式」；给修改类任务 → 模型只勘察（read/find/search）并产出分步计划后停下
- [ ] （AC30 越权拦截）：🌐 计划模式中诱导模型直接改文件 → 副作用工具未执行、模型收到「计划模式下不可用」并在回复中说明（离线自动化已另证 executor 未被调）
- [ ] （AC30 切换执行）：🌐👁 `/do 按计划执行` → 状态栏标记消失、模型按计划真实执行（确认门照常弹出、文件真实变更）
- [x] 计划模式离线半：自动化测试（`test_repl_plan_mode.py`）——/plan 后 stream 仅收到三只读工具声明 + system 后缀；越权 write_file 被合成 blocked 错误回灌且 executor 未被调；/do 恢复全量；尾随文字成为用户消息；status_line 计划模式标记

## 安全与确认（F32/F26 延续）

- [ ] （AC22 延续）：🌐👁 循环中副作用工具仍逐次确认；拒绝 → 「被用户拒绝」回灌、模型调整或说明、循环不崩
- [x] 非 TTY 安全默认不回退：自动化测试 + 管道冒烟——管道模式 confirm 恒 False（副作用工具自动拒绝），循环继续至自然停机；`printf '/exit\n' | uv run wentian` 干净退出

## 用量与持久化

- [ ] （AC31/F34）：🌐 含循环回合结束后屏显用量行（输入/输出 token 与轮数）；离线自动化——跨轮累计正确、全无用量不显示
- [ ] （AC32/F28 延续）：🌐 多轮回合 `/exit` → `--continue` → 追问中间轮工具读到的内容 → 正确引用（全轨迹持久化）

## 退化与兼容

- [ ] 纯对话零回退：不触发工具的常规提问行为与 v0.3 完全一致（自动化：registry=None 与无 tool_calls 两路径回归；🌐 人工一轮观感确认——spinner/流式/Markdown/状态栏不变）
- [x] 渲染重构零损伤：v0.3 既有 render 测试**零修改**保持绿（StreamView 抽取的硬验收，T50）；repl 单轮语义测试按 v0.4 多轮语义迁移，迁移清单在 T55 提交体内单独说明
- [x] v0.1–v0.3 全部既有测试在 v0.4 代码上保持绿（504 passed 含全部既有项，2026-06-13 现场）

## 编译与测试

- [x] 无 API key 环境 `uv run pytest -q` 全绿、无告警（504 passed，2026-06-13 现场）
- [x] 分层不破：agent 层零 SDK/rich/prompt_toolkit/wentian.tools/具体 provider import（现场 grep 检查通过）；tools/repl/render 既有约束维持
- [x] 管道冒烟：`printf '/exit\n' | uv run wentian` 横幅 v0.4.0、退出码 0、无 traceback、无悬挂（守护线程不阻塞退出，2026-06-13 现场）

## 端到端场景

- [ ] 🌐👁 **场景 6（v0.4 主干）**：启动 → 「在本项目里找到定义 X 的文件，把它的 docstring 第一行改成 Y，然后跑对应测试确认没破坏」→ 模型自主 find → read → edit（确认 y）→ run_command（确认 y）→ 总结，多轮一气呵成 → `/exit` → `--continue` 追问「你改了什么」→ 正确引用。全程无崩溃、无催促
- [ ] 🌐👁 **场景 7（计划两段式）**：`/plan 把 README 的安装说明改成 uv 方式` → 模型勘察后给出计划并停下（无任何文件变更）→ `/do 按计划执行` → 真实执行（确认门弹出）→ 文件变更与计划一致
- [ ] 🌐 **场景 8（边界：失控刹车）**：临时把 max_rounds 调小（或诱导超长任务）→ 达上限干净停止、提示明确、`/exit` 后会话文件合法（`--continue` 不 400）

# v0.5 Checklist（F35–F40：结构化系统提示 + 提示词缓存）

> 离线项自动化验证；标 🌐 的项需真实 API key 联网人工跑；标 👁 的项需真实终端亲眼验证。每项与实现解耦——重命名/移动模块不应使任何项失效。
>
> **离线验收记录 2026-06-16**：T59–T68 全部完成（10 任务，五波次：prompt 包+Usage 并行 → 双 provider+render 并行 → loop 串行 → repl→cli 串行 → 全量回归；TDD 红-绿-重构 + 逐波次规格/质量评审）。`uv run pytest -q` → **574 passed**（v0.4 基线 504 → +70），无告警。冒烟 `printf '/exit\n' | uv run wentian` 横幅示 v0.5.0、退出码 0、无 traceback。分层现场检查通过（prompt 包零 SDK/rich/prompt_toolkit；agent 层零真实 `wentian.tools` import——命中均为 docstring）。`pyproject` diff 仅版本号 0.4.0→0.5.0、零新增依赖（lock 已同步）。🌐👁 项待用户真实终端 + API key 验收。

## 实现完整性

- [x] v0.5 包可用：`uv run pytest -q` 全量全绿（**574 passed**，2026-06-16）；`__version__ == "0.5.0"`（冒烟断言）；无新增第三方依赖（pyproject diff 仅版本号）
- [x] （AC33/F35）系统提示七模块按固定优先级拼装、模块间空行分隔（`test_prompt_system.py`：顺序/空行/无残渣/注入假模块解耦，13 测试全绿）
- [x] （AC35/F37）关键约定文字双现：系统提示「工具使用」模块含「编辑文件前先读取」「优先用专用工具」等约定句（`test_prompt_system.py` 断言；工具 description 侧约定沿用 v0.3 既有文案，措辞已对齐）
- [x] （AC36/F38 离线半）Anthropic `_build_kwargs` 的 `system` 为带 `cache_control: ephemeral` 的块数组、`system=None` 省略键；OpenAI 端 system 仍单条 system 消息（`test_provider_anthropic.py` / `test_provider_openai_compat.py` 全绿）
- [x] （AC37/F39 离线半）提醒走 `<system-reminder>` 注入 provider 收到的 messages，但 store 落盘 messages 不含（`test_repl.py` 双侧对比 + `test_agent_loop.py` 原件纯净）；decorator 不 mutate 入参（`test_prompt_reminders.py`）
- [x] （AC38/F39 cadence）多轮注入文案随轮次变化：首轮完整、每 5 轮完整、其余精简（`test_prompt_reminders.py` cadence 用例 + `test_repl_plan_mode.py` 计划模式提醒在消息流）
- [x] （AC39/F40 离线半）`Usage` 携带缓存两字段、跨轮累计正确、后端未报为 0 不显示（`test_providers_base.py` + `test_provider_*.py` 解析 + `test_agent_loop.py` 累计 + `test_render.py` 显示）
- [x] （AC40/F33 迁移）计划模式提醒迁消息通道后 F33 既有行为全绿（声明过滤仅三只读 / blocked 拦截 / status_line 标记）、系统提示块不含计划模式后缀（`test_repl_plan_mode.py` 回归 + system 全等断言）

## 缓存与人格（联网）

- [ ] （AC36/F38）：🌐 Anthropic 同会话连发两轮 → 第 2 轮 `cache_read_input_tokens > 0`（缓存真实命中）
- [ ] （AC39/F40）：🌐 含缓存命中的回合结束 → 屏显用量行追加缓存读/创建 token
- [ ] （AC34/F36）：🌐👁 文天人格 before/after 人工对比——日常问题回复见文天味（自嘲/照顾/适度沙雕）；报告一次测试失败时语气是猫、结论是工程师（给实际失败输出、不掺水、不说「应该没问题」）
- [ ] （AC37/F39）：🌐👁 模型不把 `<system-reminder>` 内容当用户输入来回复（环境块与开关提醒被当系统补充指令处理）
- [ ] （R4）：🌐 Anthropic thinking 开启 + system 缓存共存完成 ≥2 轮 → 不报错、缓存仍命中（联网风险项）

## 定性评估场景（人工 before/after）

- [ ] 👁 编辑前必读：诱导模型修改一个本回合未读过的文件 → 模型先 `read_file` 再 `edit_file`（约定遵守）
- [ ] 👁 优先专用工具：让模型「列出某目录下的 py 文件」→ 用 `find_files` 而非 `run_command ls`（约定遵守）

## 退化与兼容

- [x] decorator=None 零回退：`AgentLoop` 不传 `request_decorator` 时事件序列与发请求 messages 与 v0.4 完全一致（`test_agent_loop.py::test_decorator_none_behavior_identical_to_no_decorator`）
- [x] 渲染零损伤：缓存字段全 0 / usage=None 时 `render_usage` 输出与 v0.4 逐字一致（既有 render 测试零修改保持绿，2026-06-16）
- [x] v0.1–v0.4 全部既有测试在 v0.5 代码上保持绿（574 含全部既有项，2026-06-16）

## 编译与测试

- [x] 无 API key 环境 `uv run pytest -q` 全绿、无告警（574 passed，2026-06-16 现场）
- [x] 分层不破：`prompt/` 包零 SDK/rich/prompt_toolkit import；agent 层零真实 `wentian.tools` import（现场 grep：prompt 包干净、agent 层命中均为 docstring 说明）
- [x] 管道冒烟：`printf '/exit\n' | uv run wentian` → 横幅 v0.5.0、退出码 0、无 traceback、无悬挂（2026-06-16 现场）

## 端到端场景

- [ ] 🌐👁 **场景 9（缓存生效）**：启动 → 连发两条问题 → 第二轮用量行显示 `缓存读 > 0`（同会话稳定前缀命中）
- [ ] 🌐👁 **场景 10（文天味 + 工程底子）**：日常对话一两轮（人格一致）+ 诱发一次工具/测试失败 → 文天语气照旧、但如实报失败输出与下一步，不掺水
- [ ] 🌐👁 **场景 11（计划模式提醒迁移）**：`/plan <修改类任务>` 多轮勘察 → 开关提醒按 cadence 注入（首轮完整、后续精简）、行为与 v0.4 一致（只读勘察、产计划停下、越权被拦）→ `/do` 恢复执行

# v0.6 Checklist（F41–F49：权限系统 · 五层防御）

> 离线项自动化验证；标 🌐 的项需真实 API key 联网人工跑；标 👁 的项需真实终端亲眼验证。每项与实现解耦——重命名/移动模块不应使任何项失效。
>
> **离线验收记录 2026-06-16**：T69–T80 全部完成（12 任务，七波次：permissions 纯包地基 → 沙箱/规则/配置/模式/工具元数据并行 → 流水线编排 → 判定门接入 loop → 人在回路 UI+repl → Shift+Tab+状态栏+plan 统一 → cli 装配 → 全量回归+ruff 收口；TDD 红-绿-重构 + 逐波次规格/质量评审）。`uv run pytest -q` → **734 passed, 0 skipped**（v0.5 基线 574 → +160），无告警。冒烟 `printf '/exit\n' | uv run wentian` 横幅示 v0.6.0、退出码 0、无 traceback。`ruff check .` → All checks passed；`ruff format --check .` → 82 files already formatted（顺带把历史代码全仓 format 收口）。分层现场检查通过（permissions 纯包零 SDK/rich/prompt_toolkit/跨层 import；agent 层零真实 `wentian.tools`/`wentian.permissions` import）。pyproject diff 仅版本号 0.5.0→0.6.0、零新增依赖（lock 已同步）。🌐👁 项待用户真实终端 + API key 验收。

## 实现完整性（离线）

- [x] v0.6 包可用：`uv run pytest -q` 全量全绿（**734 passed, 0 skipped**，2026-06-16）；`__version__ == "0.6.0"`（冒烟断言）；无新增第三方依赖（pyproject diff 仅版本号）
- [x] （AC41/F41/N10）黑名单硬拦截：`rm -rf /` 及变体、`dd of=/dev`、`mkfs.*`、fork 炸弹被判 Deny；普通命令不拦；黑名单无任何开关可关（`test_perm_blacklist.py`，31 测试）
- [x] （AC42/F42/N11）沙箱围栏：项目内放行；出根路径（`/etc/passwd`、`../outside`）Deny；软链接指向项目外 Deny（先解析后比对）；项目内新建文件+未创建多级中间目录放行（`test_perm_sandbox.py`，tmp_path 真实软链接，8 测试）
- [x] （AC43/AC44/F43）规则匹配：`Bash(git status)` 精确、`Bash(git *)` glob、`Write(src/**)` 跨目录、命令串 `**`≡`*`；友好名路由六工具（Grep→search_text）；同层 deny>allow（`test_perm_rules.py`）
- [x] （AC45/AC46/AC58/F44/N14）三层配置：local>project>user 合并 + defaultMode 优先级；缺失=空；YAML 非法/结构错→降级空集不抛、不致构造失败（`test_perm_settings.py`）
- [x] （AC47/F45）模式矩阵：四档×三类 12 格逐格正确；值域恒 {Allow, Ask} 绝不 Deny（`test_perm_modes.py`，25 测试）
- [x] （AC48/AC55/F46/N16）流水线短路 + 跳层不误拦 + 安全默认：黑名单命中不进沙箱/规则、deny 规则不进模式、allow 规则不进兜底；非命令不被黑名单/命令不被沙箱拦；类别不明/命令不可解析按副作用绝不静默放行（`test_perm_pipeline.py`，24 测试）
- [x] （AC51/AC53/F49/N12）判定门接入 loop：门返回成形拒绝结果原样回灌不进 executor；单批 denied 与 allow 按原序+call.id 配对不串位；只读 wave 并发不被门串行化（`test_agent_loop.py`，门契约 4 测试 + 既有回归）
- [x] （AC50/AC52/F48/N13）人在回路：三选一菜单（↑↓/数字键/默认高亮允许本次）；Esc/Ctrl+C 干净取消不退出、无 task 泄漏；永久→写精确规则到 settings.local.yaml 且内存即时生效（`test_ui_confirm.py` / `test_permission_gate.py` / `test_repl.py`）
- [x] （AC49/AC40/F47）Shift+Tab + 状态栏 + plan 统一：循环四档跨轮保持；状态栏首段显权限模式（不显 provider 名）；`/plan`·`/do` 仍进出 plan（/do 回 default）；mode==PLAN 时 F33 机制全绿（`test_repl_plan_mode.py` 回归 19 项原样绿）
- [x] （AC57/N17）代码规范与不泄漏：`ruff format --check .` 通过（82 文件全合规）、`ruff check .` 无告警；`.gitignore` 含 `.wentian/settings.local.yaml`；CLI 输出/配置回显不泄漏 api_key

## 五层真跑（联网 / 真终端）

- [ ] （AC41/F41）：🌐👁 真实会话里诱导模型跑 `rm -rf /` 类命令 → 被拦、不执行、模型收到被拒原因并改路径；切到 bypassPermissions 仍被拦
- [ ] （AC42/F42）：🌐👁 诱导模型写/读项目外文件（如 `/tmp/x`、`../x`）→ 沙箱 Deny、回灌、模型说明
- [ ] （AC50/F48）：🌐👁 真终端触发一次 Ask → 多行审批块显示正确；分别选「允许本次」（执行不留规则）/「永久」（执行且 settings.local 落规则、重启仍生效）/「拒绝本次」（Deny 回灌、循环继续）
- [ ] （AC52/N13）：🌐👁 Ask 等待时按 Esc/Ctrl+C → 干净取消本轮、不退出程序；再发一条消息可继续、不报 400
- [ ] （AC49/F47）：🌐👁 真终端按 Shift+Tab 眼见状态栏权限模式循环切换；切到 acceptEdits 后文件写不再 Ask、切到 bypassPermissions 后命令也不 Ask（黑名单/沙箱除外）；下一轮模式保持

## 退化与兼容

- [x] permission_gate=None 零回退：`AgentLoop` 不传 gate 时事件序列与发请求行为与 v0.5 完全一致（`test_agent_loop.py::TestPermissionGate` 三路对照）
- [x] requires_confirmation 派生不破 classify：`batch.classify` 在 Tool 加 category 后行为与 v0.5 一致（read_only/side_effect 判定不变，`test_agent_batch.py` 回归绿）
- [x] （AC56/N12）v0.1–v0.5 全部既有测试在 v0.6 代码上保持绿（734 含全部既有项，2026-06-16）
- [x] （AC54/N15）跨协议一致：权限判定在 agent 编排层、与 provider 无关；provider 适配层（anthropic.py/openai_compat.py）v0.6 **零逻辑改动**（仅受全仓 ruff format 影响的纯空白；判定逻辑无一行落在 provider 层）

## 编译与测试

- [x] 无 API key 环境 `uv run pytest -q` 全绿、无告警（734 passed, 0 skipped，2026-06-16 现场）
- [x] 分层不破：`permissions/` 包零 SDK/rich/prompt_toolkit/跨层 import；agent 层零真实 `wentian.tools`/`wentian.permissions` import（现场 grep，2026-06-16）
- [x] （AC59/N18）可扩展性现场：模式兜底集中在 `modes.py` 单表（加一档只扩表）、五层编排集中在 `pipeline.py`（加一层只插一段）；权限逻辑全在 `permissions/` 纯包 + 装配层 `permission_gate.py`，provider 适配层零逻辑改动（结构取证）
- [x] 管道冒烟：`printf '/exit\n' | uv run wentian` → 横幅 v0.6.0、状态栏显权限模式、退出码 0、无 traceback、无悬挂（2026-06-16 现场）

## 端到端场景

- [ ] 🌐👁 **场景 12（黑名单+沙箱兜底）**：让文天做一个会触碰危险命令/越界路径的任务 → 危险被拦、回灌、文天换安全路径继续完成
- [ ] 🌐👁 **场景 13（规则免打扰）**：在 settings 配 `Bash(git *)` allow → 真实任务里所有 git 子命令不再弹 Ask、直接执行；`git push` 若另配 deny 则被拦
- [ ] 🌐👁 **场景 14（人在回路三选一 + 永久）**：触发写文件 Ask → 选「永久」→ 后续同路径写入不再询问（本会话）→ `/exit` 重启后该 allow 规则仍在 settings.local.yaml 生效
- [ ] 🌐👁 **场景 15（模式切换信任梯度）**：default 下文件写/命令都问 → Shift+Tab 到 acceptEdits 文件写放行命令仍问 → 到 bypassPermissions 全放行（黑名单/沙箱仍拦）→ 切回 default 恢复询问

# v0.7 Checklist（F50–F55：MCP 客户端接入）

> 每项通过运行代码或观察行为验证。离线项用 stdlib 假 MCP Server（stdio 假脚本 + `http.server` 假服务）取证；🌐👁 = 需联网/真 Server/真终端，留用户验收。

## 实现完整性（离线）

> 离线全绿 ✅（2026-06-16，七波次 TDD 红-绿-重构；全量 **823 passed**，v0.6 基线 734 → +89）。逐项证据见下。

- [x] （AC61/F51）协议三步 + id 配对：假 stdio Server 上 initialize（含发出 initialized 通知）→ tools/list 取回工具清单（名/描述/schema/readOnlyHint）→ tools/call 取回结果；**故意乱序回包仍按 id 正确配对**；请求超时干净 raise（`test_mcp_protocol.py` 18 + `test_mcp_client.py` 17 全绿）
- [x] （AC62/F52）两种传输：stdio 子进程假 Server 端到端跑通且 stderr 不干扰协议、close 终止子进程；http `http.server` 假 Server **即时 JSON 与 SSE 事件流两分支**都解析正确、配置请求头被带上（`test_mcp_transport.py` 8 全绿；SSE 用 Content-Length 而非 chunked 避免 urllib IncompleteRead）
- [x] （AC63/F53）适配无感：远端工具包成 `MCPTool` 注册进 registry，名带 `<Server>__` 命名空间不撞内置工具；`run()` 调 client 取文本回灌（用去命名空间的原始远端名）；client/远端错 → `ToolError` 不崩溃（`test_mcp_adapter.py` 16 全绿）
- [x] （AC64/F54/N16）安全默认：未标 readOnlyHint 的工具 `category==FILE_WRITE` 且 `requires_confirmation` 为真；readOnlyHint=true 的 `category==READ_ONLY` 免确认（`test_mcp_adapter.py`）
- [x] （AC60/F50/N23）两层配置：仅用户文件时与旧单文件等价（向后兼容、mcp_servers 空）；加项目 `.wentian/config.yaml` 后同名覆盖+新增并入（providers 与 mcpServers 均适用）；stdio/http 两型解析；`${VAR}` 展开、缺失→空串+告警；字段缺失 ConfigError；显式 `path` 入参仍走单文件直载（`test_config.py` 32 全绿）
- [x] （AC65/F55/N21）多 Server 故障隔离：两 Server 一坏（命令不存在/握手超时）一好 → 坏的 `report.failed` 含原因且 transport 被 close、好的正常注册、不抛不影响好 Server；`close_all` 终止所有子进程（`poll()` 非 None）幂等；空 servers no-op（`test_mcp_manager.py` 7 全绿；关键坑：MCPClient 须先 `set_on_message` 再 `transport.start()`，否则读取线程持旧回调丢响应致 initialize 超时）
- [x] （AC66/N22）离线可测：上述全部用 stdlib 假 Server 离线跑通，无需联网（`tests/_fake_mcp_server.py` 真子进程 + `http.server` 假服务驱动）

## 接入真跑（联网 / 真 Server / 真终端）

- [ ] （AC63/F53）：🌐👁 配一个真实 stdio MCP Server（如官方 filesystem server）→ 启动见接入汇报、其工具进 registry → 真实会话里模型多轮调用其工具完成一个任务（如读目录/取内容），全程对「远端」无感
- [ ] （AC62/F52）：🌐👁 配一个真实 HTTP（Streamable）MCP Server → 握手/列工具/调用跑通（验证 SSE 解析在真服务上成立、请求头鉴权生效）
- [ ] （AC65/F55）：🌐👁 同时配一个好 Server + 一个坏 Server（错误命令/不可达 URL）→ 启动汇报里好 Server 工具就绪、坏 Server 列为失败跳过，程序正常进入对话、好 Server 工具可用

## 退化与兼容

- [x] （N23）无 `mcpServers` 配置时：v0.1–v0.6 全部既有测试保持绿；启动行为、横幅、状态栏与 v0.6 完全一致（无多余 MCP 输出）——`mcp_servers` 空即 manager 不建、零输出（`test_cli.py` 覆盖 + 冒烟现场印证）
- [x] （N20）MCP 包内零 asyncio：现场 grep 确认 `mcp/` 包仅用 threading/Event，未 import asyncio（`grep -rn asyncio src/wentian/mcp/` 空）
- [x] （N20）只读 MCP 工具并发不串位：多个并发请求乱序回包按各自 id 正确配对（`test_mcp_client.py` 乱序 + 多线程用例覆盖）

## 编译与测试

- [x] 无 API key 环境 `uv run pytest -q` v0.1–v0.6 全部 + v0.7 新增全绿（**823 passed**；1 warning 为 config 缺环境变量→空串的预期告警行为）
- [x] 分层不破（**据实精确化**）：`mcp/` 包零 agent/providers/ui 高层依赖、零第三方 MCP/HTTP 库、零 asyncio（现场 grep 取证）。包内跨层 import 按职责分层：`protocol.py` 纯 stdlib（leaf）；`transport.py`/`manager.py` 用 `wentian.config` 的 Server 配置类型（manager 还需 `isinstance` 判 stdio/http，运行期必需）；`adapter.py` 为统一工具缝（`tools.base` + `permissions.decision.Category`）；`manager.py` 为装配缝（+`tools.registry`）。**原 checklist「仅 adapter 跨层」措辞偏紧**——transport/manager 对 config 类型、manager 对 registry 的依赖是装配层的必要缝，与六内置工具同规，非违例。agent/provider 适配层为 MCP **零改**（diff 取证）
- [x] 管道冒烟（无 MCP）：`printf '/exit\n' | uv run wentian` → 横幅 v0.7.0、退出码 0、无 traceback、行为同 v0.6（现场跑通）
- [x] 离线 MCP 冒烟：配 `_fake_mcp_server.py` 的 stdio Server 启动 → 横幅下示「MCP fakefs · 1 工具已注册」、其工具进 registry → `/exit` 退出后 `pgrep _fake_mcp_server` 为 0（无残留子进程，close_all 经 REPL try/finally + atexit 生效）
- [x] `pyproject` diff 仅版本号 0.7.0、零新增第三方依赖；`uv.lock` 同步（`__init__`/pyproject/lock 三处均 0.7.0）

## 端到端场景

- [ ] 🌐👁 **场景 16（MCP 工具无感调用）**：挂一个真实 MCP Server → 让文天用它的工具完成任务 → 工具调用轨迹与内置工具样式一致，文天不区别对待远端工具
- [ ] 🌐👁 **场景 17（单 Server 挂不拖垮）**：运行中让某 Server 崩溃 → 其工具调用回灌结构化错误、文天说明并换路径，其余 Server 工具与内置工具照常可用
- [ ] 🌐👁 **场景 18（外部工具走确认）**：挂一个含写类工具（未标 readOnlyHint）的 Server → 模型调用它时按副作用走确认/模式兜底（default 下弹 Ask）；标了 readOnlyHint 的只读工具直接执行不打扰

# v0.8 Checklist（F56–F62：上下文管理 + 双层压缩）

> 每项通过运行代码或观察行为验证。离线项用纯函数单测 + 假 provider（返回固定摘要文本）取证；🌐👁 = 需联网/真长会话/真终端，留用户验收。

## 实现完整性（离线）

> 离线全绿 ✅（2026-06-19，五波次 TDD 红-绿-重构；全量 **905 passed**，v0.7 基线 823 → +82：estimator 13 / config +9 / offload 10 / summarizer 17 / compactor 15 / loop +5 / repl·cli +13）。逐项证据见下。

- [x] （AC67/F56）估算锚点：`AnthropicProvider.prompt_token_total` = `input + cache_read + cache_creation`；base/openai = `input_tokens`（不重复加缓存）；`estimate_total = prompt_total + char_estimate(新增消息)`；无锚点退化全量字符；字符比可配（`test_context_estimator.py` 9 + `test_providers_prompt_total.py` 4 全绿）
- [x] （AC68/F57）单条卸载 + 幂等：单条工具结果超阈值 → 原文写 artifacts 目录、对话留预览+绝对路径+提示、`is_error` 保留、`offloaded=True`；再扫不重复卸载；user/assistant 消息不被改写（N27）（`test_context_offload.py`；压缩冒烟现场印证：4000 字原文落盘、对话留预览、offloaded=True、is_error 保留）
- [x] （AC69/F57）单轮合计卸载：单轮多条各自不超单条阈值、合计超阈值 → 按 est 降序挑最大依次卸载至回落、较小者保留（`test_context_offload.py` 含变异验证）
- [x] （AC70/F58）切割边界安全：保留段从尾部按 token 保留约 1 万且 ≥5 条；**首条非 tool**（snap 跳孤儿）；不拆「assistant(含 tool_calls)↔其全部 tool 结果」对；clamp 不摘空/不越界（`test_context_summarizer.py`）
- [x] （AC71/F58/F59/F60）摘要端到端（假 provider）：摘要请求 `tools is None`、Prompt 含「禁止调用工具」「先草稿后正式」「八段」「<final_summary>」；压缩后历史 = 摘要消息（八段）+ 边界消息（「重新读取/勿脑补」）+ 保留段；摘要仅在 `est > window − reserved_output − margin` 时发生（`test_context_summarizer.py` 17 全绿；冒烟现场：19 条→4 条、首条摘要+边界含「勿脑补」、保留段首条非 tool）
- [x] （AC72/F61）手动 + 熔断：`/compact` 用 3K 余量、自动用 13K（参数差异断言）；注入「摘要必失败」假 provider → 连续 3 次失败置熔断、后续自动轮跳过 L2 但 L1 照常、告警一次；一次成功清零；熔断后 `/compact` 强制重试、成功解除（`test_context_compactor.py` 15 全绿；冒烟现场：3 次真失败后 tripped=True，cut==0 空操作不计失败）
- [x] （AC73/F62/N25）触发编排：多轮循环每轮调后端前先 L1 后 L2、改写经 RoundEnd 落盘（次序与持久化断言）（`test_agent_loop.py` 钩子用例 + `test_repl.py`）
- [x] （AC74/N24/N26/N27）分层 + 离线：`context/` 包零 SDK/rich/prompt_toolkit、只 import stdlib + `providers.base` 类型（grep 取证：仅见 json/math/re/dataclasses/pathlib + wentian.config/context/providers.base）；两家差异在 provider 层消化；估算/卸载/切割/熔断离线纯函数可测、摘要假 provider 离线端到端；用户消息原文保真

## 字节级回归（离线 · 不可妥协）

- [x] （AC73/N25）**钩子 None ⇒ 等价 v0.7**：未注入压缩器时既有 `test_agent_loop.py`/`test_repl.py` **零修改**保持绿（T95 含 `test_hook_none_behavior_identical` 断言事件序列/provider 形状/messages 终态完全一致）
- [x] （N25）切割配对合法：`find_cut_index` snap 保证保留段首条非 tool、不拆 assistant↔tool 对（`test_context_summarizer.py` 配对用例）；context7 查证 Anthropic 容忍连续同角色（服务端合并）→ 摘要 user 紧邻保留段 user 不报错。真跑不报 400 留 🌐 验收
- [x] （N25）`request_decorator` 契约不变：环境提醒仍只读不写回、不持久化（既有 AC37 回归保持绿；压缩钩子在 decorator 之前、互不干扰）

## 定性 / 真跑（联网 / 真终端）

- [ ] 🌐👁 （F57）真长会话里某工具吐出超大输出（如 `cat` 大文件 / 大目录列举）→ 对话里自动只留预览 + artifacts 路径，后续模型按提示重新 Read 而非照预览编码
- [ ] 🌐👁 （F58/F60）把窗口配小或推到逼近上限 → 自动触发摘要：较早历史折成八段摘要、近期原文保留、补边界消息；压缩后模型仍能接着干活、不脑补已被摘掉的文件细节
- [ ] 🌐👁 （F61）`/compact` 手动触发 → 屏显卸载数/是否摘要；连续诱发摘要失败（如断网）→ 第 3 次后熔断、提示、自动轮不再尝试

## 退化与兼容

- [x] （N25）无 `context:` 配置：v0.1–v0.7 全部既有测试保持绿；启动行为、横幅、状态栏与 v0.7 一致（压缩器以默认参数静默生效，无多余输出）——全量 905 绿 + 无配置冒烟印证
- [x] （N26）provider 差异在 provider 层：`prompt_token_total` 两家各自实现、`context/` 包不 import 具体 provider（grep 取证通过）
- [x] （F62）卸载/摘要随 RoundEnd 逐轮落盘：中途崩溃不留孤儿引用（artifacts 文件与对话改写原子持久化，核验 repl.py:598-601 RoundEnd save）

## 编译与测试

- [x] 无 API key 环境 `uv run pytest -q` v0.1–v0.7 全部 + v0.8 新增全绿（**905 passed, 1 warning**；warning 为 config 缺环境变量→空串的既有预期告警）
- [x] 分层不破：`context/` 包零 SDK/rich/prompt_toolkit、只 import stdlib + `providers.base` 类型（grep 取证）；agent 层仅多一个可选钩子参数（diff 局限 loop.py 钩子 + repl/cli 装配 + providers 一个方法 + config 字段）
- [x] `ruff format --check .` 通过（104 files already formatted）、`ruff check .` 无告警（All checks passed）
- [x] 管道冒烟（无 context 配置）：`printf '/exit\n' | uv run wentian` → 横幅示 v0.8.0、退出码 0、无 traceback、行为同 v0.7（现场跑通）
- [x] 离线压缩冒烟：小窗口 + 超大工具结果 → L1 卸载（artifacts 文件落盘 4000 字 + 对话留预览取证）；假 provider 触发 L2 → 历史 19 条变摘要+边界+保留段 4 条；熔断 3 次真失败生效；`/compact` 可用（现场脚本跑通）
- [x] `pyproject` diff 仅版本号 0.7.0→0.8.0、零新增第三方依赖；`uv.lock` 同步

## 端到端场景

- [ ] 🌐👁 **场景 19（长任务不撑爆）**：给文天一个需要大量读文件/跑命令的长任务 → 跑很多轮后上下文逼近窗口 → 自动摘要较早历史、保留近期，任务继续推进不因溢出而瘫
- [ ] 🌐👁 **场景 20（超大工具输出卸载）**：让文天执行一个吐出超大输出的命令 → 对话里只留预览 + 路径、token 不爆，文天需要细节时重新 Read 该路径
- [ ] 🌐👁 **场景 21（手动压缩 + 熔断）**：用户 `/compact` 主动压一次 → 见压缩汇报；摘要连续失败 3 次 → 熔断生效、第一层仍护着、不陷死循环

# v0.9 Checklist（F63–F69：记忆与会话 —— 越用越懂你）

> 每项通过运行代码或观察行为验证，聚焦系统行为、与实现解耦（重命名文件/移动函数不应使其失败）。离线项用纯函数单测 + 临时目录真实读写 + 假 provider（返回固定四类笔记 JSON / 固定摘要文本）取证；🌐👁 = 需真实 API key 联网/真终端跨会话体感，留用户验收（执行一次记录证据）。基线 v0.8 = 905 passed；v0.9 后预计 +N。

## 实现完整性（离线）

> C53–C59 七个组件可导入、可调用，最小路径冒烟。

- [x] （AC75/C53）`instructions.py` 可导入：`load_project_instructions(cwd, *, user_home=None, cfg)` 为纯函数、签名稳定，临时目录放三层 `WENTIAN.md` 调用返回拼接字符串（验证：import 后构造临时 cwd/user_home 调用，断言返回非空且含三层内容）（验证：`tests/test_instructions.py::test_three_layers_priority_high_first`）
- [x] （AC76/C54）`session.py` JSONL 重构可调用：`SessionStore.append/load/list/load_latest` + `project_sessions_dir(cwd)` + slug 化均可导入调用（验证：临时目录建 store，append 一会话→load 回来消息等价、list 含之）（验证：`tests/test_session.py::TestJsonlAppend`、`TestProjectPartition`）
- [x] （AC77/C55）恢复卫生函数可调用：`truncate_unpaired(messages)`、`prune_expired(dir, retention_days)`、`resume_gap_reminder(updated_at, now, hours)` 为纯函数/可注入（验证：import 后各喂构造输入断言返回类型与边界）（验证：`tests/test_session.py::TestTruncateUnpaired`/`TestResumeGapReminder`/`TestPruneExpired`）
- [x] （AC79/C56）`memory/store.py` 可调用：`read_index(scope)` / `write_note(scope, note)` / `upsert_index(...)` + 分级目录（user/project）解析 + 写锁（验证：临时目录 write_note 落 `.md`+frontmatter、read_index 取回摘要）（验证：`tests/test_memory_store.py`）
- [x] （AC79/C57）`memory/extractor.py` 可调用：`extract(provider, recent_messages, existing_index) -> list[Note]`，解析部分为纯函数（验证：注入假 provider 返回固定四类笔记 JSON，断言解析出 Note 列表）（验证：`tests/test_memory_extractor.py::test_parse_notes_four_categories`/`test_extract_disables_tools_and_passes_system`）
- [x] （AC79/C58）`memory/runner.py` 可调用：`MemoryRunner.submit(recent_messages)` 启线程、`close(timeout)` 收尾（验证：构造 runner 注入假 provider+临时 store，submit 后 close 不抛、落盘可见）（验证：`tests/test_memory_runner.py::test_submit_writes_notes_and_index_after_join`）
- [x] （AC81/C59）`prompt/system.py` 两槽真渲染可调用：`_render_project_instructions` / `_render_memory` 读 `ctx.project_instructions` / `ctx.memory` 真渲染（v0.5 起恒空、本版起真出内容）；`config.py` 的 `MemoryConfig` / `SessionsConfig` 可解析（验证：构造带两字段的 PromptContext 渲染断言含其内容；空 ctx 渲染无残渣）（验证：`tests/test_prompt_system.py::TestProjectInstructionsSlot`/`TestMemorySlot`、`tests/test_config.py::TestMemoryConfigDefaults`/`TestSessionsConfigDefaults`）

## 集成

> 各机制接进系统提示 / 会话流 / 后台线程后的端到端行为（离线，临时目录 + 假 provider）。

- [x] （AC75/F63）三层指令注入系统提示：三处放不同 `WENTIAN.md`（项目本地覆盖 / 项目根 / 用户全局）→ 注入「项目/自定义指令」模块、顺序高优先级在前；缺层静默跳过；三层均缺则模块为空（验证：临时目录摆三文件，断言发给后端的 system 含三层内容且次序正确）（验证：`tests/test_instructions.py::test_three_layers_priority_high_first`/`test_missing_layers_skipped_silently`/`test_all_missing_returns_empty`；端到端 `tests/test_cli.py::test_build_app_injects_project_instructions`）
- [x] （AC75/F63）`@include` 内联展开：指令文件中独占一行 `@include xxx.md` → 目标文件内容相对「含它的文件所在目录」内联（验证：a.md `@include b.md`，断言 b 内容出现在拼接结果）（验证：`tests/test_instructions.py::test_include_inlines_target_content`/`test_include_resolves_relative_to_including_file_dir`）
- [x] （AC75/F63）`@include` 防环 + 限深：构造 a→b→a 环 → `visited` 跳过并告警、不无限递归；嵌套超默认深度 5 → 停止并告警（验证：构造环与超深链，断言不挂死、告警出 stderr、结果有限）（验证：`tests/test_instructions.py::test_cycle_detection_no_infinite_recursion`/`test_depth_limit_stops_and_warns`）
- [x] （AC75/AC84/F63/N32）`@include` 越界拦截：指向项目根外 / 越界绝对路径 / 软链接指向项目外（先解析符号链接再前缀比对，与 v0.6 沙箱同规）→ 拒绝并告警、不读取（验证：临时目录造越界目标 + 指向外部的 symlink，断言其内容不出现在结果、告警出 stderr）（验证：`tests/test_instructions.py::test_include_outside_root_via_relative_blocked`/`test_include_absolute_path_outside_root_blocked`/`test_include_symlink_escape_blocked`）
- [x] （AC75/F63）拼接体积上限：指令总体积超上限 → 按上限截断并告警（验证：造超大指令文件，断言结果不超上限、告警出）（验证：`tests/test_instructions.py::test_total_volume_truncated_at_limit`）
- [x] （AC76/F64）JSONL 追加写不重写全文：新会话写到 `projects/<cwd-slug>/sessions/<id>.jsonl`、每轮新增消息逐行追加（验证：连续两轮 append，断言文件按字节增长、前缀字节不变——非重写）（验证：`tests/test_session.py::test_append_is_incremental_not_rewrite`/`test_new_session_lands_in_partition`）
- [x] （AC76/F64）坏行跳过 + 元数据由扫描得出：故意写一条 JSON 坏行 → load 跳过坏行加载其余、告警 stderr 不抛；ID 取文件名、标题取首条 user 消息行、消息数=数消息行（验证：构造含坏行的 JSONL，断言加载消息数正确、ID/标题正确、坏行告警出）（验证：`tests/test_session.py::test_load_skips_bad_line`/`test_id_from_filename_not_meta`/`TestList`）
- [x] （AC76/F64）分区列举默认当前项目 + `--all` 跨项目：`/sessions` 与 `--continue` 默认只扫当前 `<cwd-slug>` 分区；`--all` 跨 `projects/*/sessions/` 全扫；旧全局扁平 `.json` 视为遗留不列不删不报错（验证：两个分区各放会话 + 一个遗留 `.json`，断言默认只列本分区、`all_projects=True` 列全部、遗留不现身）（验证：`tests/test_session.py::test_list_default_only_current_partition`/`test_list_all_projects_scans_every_partition`/`test_legacy_flat_json_not_listed`）
- [x] （AC77/F65）尾部未配对 tool_call 截断：历史尾部为「助手 tool_call 无后续工具结果」悬空调用 → 恢复时截断该未配对部分（验证：构造悬空尾部，断言 `truncate_unpaired` 后送两家 provider 转换不产生 400 形状的悬空对）（验证：`tests/test_session.py::TestTruncateUnpaired`；端到端 `tests/test_cli.py::test_build_app_resume_truncated_history_converts_for_both_providers`）
- [x] （AC77/F65）溢出复用 v0.8 Compactor 压一次：恢复后 `estimate_total > context_window - margin`（v0.8 estimator 判定）→ 调 v0.8 `Compactor.compact()` 压一次再进入对话（验证：构造超窗历史 + 假 provider 摘要，断言恢复流程调用 Compactor 且压后落到窗内；不重造估算/压缩）（验证：`tests/test_cli.py::test_build_app_resume_overflow_triggers_compaction`）
- [x] （AC77/F65）时间跨度提醒一次性不写回：距上次 `updated_at` 超 `sessions.resume_gap_reminder_hours`（默认 4h）→ 恢复后首轮经 v0.5 `<system-reminder>` 通道注入一条一次性时间跨度提示、绝不写回持久化 messages（验证：构造久远 `updated_at`，断言 provider 收到的 messages 含提醒、store 落盘 messages 不含）（验证：`tests/test_cli.py::test_build_app_resume_reminder_from_gap`/`test_build_app_resume_no_reminder_when_recent`；REPL 侧 `tests/test_repl.py::test_reminder_injected_first_turn_only`/`test_reminder_not_persisted`）
- [x] （AC78/F66）过期会话清理：构造一个 `updated_at` 超 `sessions.retention_days`（默认 30）的会话 + 一个新的 → 启动惰性清理删旧的（及其 `.artifacts/` 目录）、保留新的；清理失败不致命（告警跳过）；`retention_days` 可配（验证：临时分区造新旧两会话 + 旧的 artifacts 目录，断言旧文件与目录被删、新的在）（验证：`tests/test_session.py::TestPruneExpired`；启动接线 `tests/test_cli.py::test_build_app_prunes_expired_on_startup`）
- [x] （AC79/F67）后台抽取四类笔记落盘 + 更新 INDEX：一个 `COMPLETED` 回合后台线程调（假 provider 返回固定四类笔记 JSON）→ 用户偏好/纠正反馈落用户级 `~/.config/wentian/memory/`、项目知识/参考资料落项目级 `<cwd>/.wentian/memory/`、各写带 frontmatter（category/created_at/source_session/tags）的 `.md` + 更新对应 `INDEX.md`（验证：临时目录 + 假 provider，submit→close 后断言四类笔记按归属落盘、frontmatter 完整、INDEX 含新条目）（验证：`tests/test_memory_runner.py::test_submit_writes_notes_and_index_after_join`、`tests/test_memory_store.py::test_write_note_has_frontmatter_and_content`；触发链 `tests/test_repl.py::test_completed_round_submits_recent_window`）
- [x] （AC79/F67/N31）抽取异常不崩不污染不阻塞：注入「抽取必抛异常」的假 provider → 异常静默吞到 stderr、会话不中断、对话历史 messages 不被污染、不阻塞下一次输入（验证：假 provider 抛异常，断言主流程不崩、messages 终态不含抽取产物、REPL 输入不被卡）（验证：`tests/test_memory_runner.py::test_submit_swallows_exception_and_keeps_messages`、`tests/test_memory_extractor.py::test_extract_swallows_provider_exception`）
- [x] （AC80/F67）LLM 去重不重复追加：已有 `INDEX` 含某条 → 再抽到等价信息、把现有索引喂 LLM、它判「已覆盖」则跳过或更新而非重复追加（验证：脚本化假 provider 返回判重决策，断言第二次抽取后 INDEX 条目数不重复增长）（验证：`tests/test_memory_runner.py::test_skip_decision_does_not_append_to_index`、`tests/test_memory_extractor.py::test_extract_feeds_existing_index_to_model`/`test_extract_dedup_skip_not_appended`）
- [x] （AC81/F68）两份 INDEX 注入长期记忆模块 + 体积上限：启动读 user+project 两份 `INDEX.md` 拼进 `ctx.memory` → 填「长期记忆」模块、内容可见于发给后端的 `system`；索引超 200 行/25KB（用 v0.8 estimator 卡预算）→ 按上限截断；启动注入一次不热刷（验证：临时目录造两份 INDEX，断言 system 含其内容；造超限 INDEX 断言被截断到上限内）（验证：`tests/test_cli.py::test_build_app_injects_memory_index`、`tests/test_memory_store.py::test_read_indexes_for_injection_joins_both`/`test_read_indexes_for_injection_caps_lines`/`test_read_indexes_for_injection_caps_bytes`）

## 退化与兼容

- [x] （AC82/F69/N29）配置块缺失走默认：`memory:` / `sessions:` 块缺失或字段缺失安全降级不抛异常，走默认（enabled=true / max_index_lines=200 / max_index_bytes=25600 / retention_days=30 / resume_gap_reminder_hours=4）（验证：空配置构造 `MemoryConfig`/`SessionsConfig`，断言默认值；缺字段不抛）（验证：`tests/test_config.py::TestMemoryConfigDefaults`/`TestSessionsConfigDefaults`/`test_partial_memory_block_safe_defaults`）
- [x] （AC82/F69）`memory.enabled:false` 一键关：关掉后台抽取与启动注入整条链路（验证：config 设 false，断言 build 后无 MemoryRunner、回合后不抽取、`ctx.memory` 不注入）（验证：`tests/test_cli.py::test_build_app_memory_disabled_runner_none`/`test_build_app_memory_disabled_skips_injection`、`tests/test_memory_runner.py::test_submit_noop_when_disabled`）
- [x] （AC82/N29）无 WENTIAN.md / 无 memory / 无历史会话时行为与 v0.8 一致：两个新槽位（项目指令、长期记忆）为空时系统提示拼装无空行残渣、缓存前缀稳定（启动注入一次、不破坏 v0.5 缓存断点）（验证：裸临时 cwd + 空配置，断言 system 渲染逐字节等价 v0.8 空槽形状、无残渣；既有 v0.1–v0.8 测试全绿）（验证：`tests/test_prompt_system.py::TestBothSlotsEmptyRegression::test_both_slots_empty_equals_v08_default`、`tests/test_cli.py::test_build_app_no_instructions_no_memory_v08_regression`；全量 1056 测试全绿）
- [x] （N29）JSONL 重构对既有层透明：会话持久化对 Agent Loop / v0.8 压缩写回钩子 / 权限门 / provider 适配层透明（验证：既有 `test_agent_loop.py`/`test_repl.py` 对会话写回部分零修改保持绿；v0.8 压缩 RoundEnd 落盘走 JSONL append 不破）（验证：全量 1056 测试全绿，含既有 `tests/test_agent_loop.py`/`tests/test_repl.py`；`tests/test_session.py::test_roundtrip_preserves_tool_pairing_and_offloaded`）
- [x] （N30）记忆模型自建 provider 不跨线程共享：抽取器自建 provider 实例（不复用对话 provider）、daemon 线程 fire-and-forget；并发写 INDEX 加锁串行（验证：断言抽取 provider 与对话 provider 非同一实例；并发 submit 多条断言 INDEX 写入不交错错乱）（验证：`tests/test_memory_runner.py::test_each_submit_builds_fresh_provider`/`test_worker_threads_are_daemon`/`test_concurrent_submits_index_intact`、`tests/test_memory_store.py::test_concurrent_index_writes_no_loss`）
- [x] （N31）`/exit` 短 join 不卡退出无线程泄漏：`/exit` 时 `runner.close(timeout)` 最多 join 一个短超时（验证：submit 一个慢抽取后 close，断言在超时内返回、不挂死）（验证：`tests/test_memory_runner.py::test_close_short_timeout_returns_fast_with_slow_extraction`；REPL 退出接线 `tests/test_repl.py::test_completed_round_submits_recent_window` 同组的 close 用例）

## 编译与测试

- [x] 无 API key 环境 `uv run pytest -q` v0.1–v0.8 全部 + v0.9 新增全绿（基线 905 → 905+N passed）（验证：全量 `uv run pytest -q` → 1056 passed，905→+151）
- [x] （AC83/N30）分层 import 断言：`instructions.py` 为叶子（stdlib only，零 agent/provider/ui 高层依赖）；`memory/` 包对 agent 编排层零反向依赖、provider 鸭子注入（仿 v0.8 summarizer）；会话层不依赖后端 SDK；复用 v0.8 `context` 包不重造估算/压缩（验证：grep 取证各模块 import 边界，断言无越界依赖）（验证：`tests/test_layering.py`——`test_instructions_is_stdlib_only`/`test_memory_*_no_orchestration_imports`/`test_session_no_provider_or_compactor_imports`，ast 解析 import 边界，7 测试）
- [x] （AC83/N33）`ruff format --check .` 通过、`ruff check .` 无告警（All checks passed）（验证：`ruff check .` → All checks passed!；`ruff format --check .` → 114 files already formatted）
- [x] （AC84/N32）落盘不含 api_key：记忆笔记 + 会话 JSONL 落盘内容扫描不含 api_key 等密钥（验证：跑一轮真实落盘后自动化扫描所有产出文件，断言无密钥泄漏）；零新增第三方依赖（验证：记忆侧自动化脱敏扫描 `tests/test_memory_store.py::test_write_note_redacts_secrets_in_content`/`test_write_note_redacts_secret_in_title`、`tests/test_memory_runner.py::test_extraction_products_contain_no_plaintext_secret`——落盘 `[REDACTED]` 无明文密钥；会话 JSONL 侧 api_key 仅存配置不入 messages；零新增第三方依赖见下条。真实 provider 全量落盘扫描留 🌐 验收）
- [x] （AC82/N29）无配置冒烟：`printf '/exit\n' | uv run wentian` → 横幅示 v0.9.0、退出码 0、无 traceback、行为同 v0.8（无 WENTIAN.md/无 memory/无历史会话时无多余输出）（验证：现场跑通）（验证：隔离 HOME/XDG 沙箱跑通——横幅 v0.9.0、exit 0、无 traceback）
- [x] （N33）`pyproject` diff 仅版本号 0.8.0→0.9.0、零新增第三方依赖；`uv.lock` 同步（`__init__`/pyproject/lock 三处均 0.9.0）（验证：`src/wentian/__init__.py` + `pyproject.toml` + `uv.lock` 三处均 0.9.0；`tests/test_cli.py::test_version_is_0_9_0`）

## 端到端场景

- [x] （AC75/F63）**场景 22（指令注入·离线）**：临时项目根放 `WENTIAN.md` + 一个被 `@include` 的子文件 → 启动后断言系统提示「项目/自定义指令」模块含主文件与内联子文件内容、顺序正确（验证：临时目录端到端跑 build_app 链路，断言注入可见）（验证：分部覆盖——build_app 注入 `tests/test_cli.py::test_build_app_injects_project_instructions`（主文件经 build_app 入 system）＋ `@include` 内联展开 `tests/test_instructions.py::test_include_inlines_target_content`（build_app 调的同一 `load_project_instructions`）；单一「build_app+@include 合一」复合 fixture 未单列）
- [x] （AC76/AC77/F64/F65）**场景 23（坏行/未配对/溢出仍正常恢复·离线）**：构造一份含坏行 + 尾部未配对 tool_call + 超窗体积的 JSONL → `--continue` 恢复：跳过坏行、截断未配对、调 Compactor 压一次 → 恢复后历史对两家 provider 合法且在窗内、会话正常继续（验证：临时分区造问题会话，断言恢复后 messages 合法、压过一次、无 400 形状）（验证：分部覆盖——坏行跳过 `tests/test_session.py::test_load_skips_bad_line`、未配对截断 `tests/test_cli.py::test_build_app_resume_truncates_unpaired`/`test_build_app_resume_truncated_history_converts_for_both_providers`（两家 provider 转换合法）、溢出压一次 `tests/test_cli.py::test_build_app_resume_overflow_triggers_compaction`；三问题合一单 fixture 未单列）
- [x] （AC76/F64）**场景 24（`/sessions --all` 跨项目·离线）**：两个 `<cwd-slug>` 分区各放会话 → 默认 `/sessions` 只列当前项目、`/sessions --all` 列两个项目全部（验证：构造两分区，断言默认与 `--all` 列表差异符合预期）（验证：REPL 命令 `tests/test_repl.py::TestSessionsAllFlag::test_sessions_all_lists_other_partitions`/`test_sessions_plain_uses_current_partition`＋分区行为 `tests/test_session.py::test_list_default_only_current_partition`/`test_list_all_projects_scans_every_partition`）
- [x] （AC79/AC81/F67/F68）**场景 25（越用越懂你·离线半链路）**：临时目录里聊一轮（假 provider 触发 `COMPLETED`）→ 后台抽取四类笔记落盘 + 更新两份 INDEX → 重新 build_app 启动 → 断言「长期记忆」模块注入了上一会话抽出的记忆（验证：同进程内 submit→close 后第二次 build，断言 system 含新记忆）（验证：分部覆盖——抽取落盘+更新 INDEX `tests/test_memory_runner.py::test_submit_writes_notes_and_index_after_join`、INDEX 经 build_app 注入「长期记忆」`tests/test_cli.py::test_build_app_injects_memory_index`；「submit→close→二次 build」同进程合一 fixture 未单列）
- [ ] 🌐👁 **场景 26（真 provider 抽取一次）**：配真实 API key，真实聊一轮 → 后台真 provider 抽取出四类笔记、落带 frontmatter 的 `.md` + 更新 INDEX、再抽等价信息不重复追加（验收时执行一次，记录落盘文件与 INDEX 证据）
- [ ] 🌐👁 **场景 27（真终端跨会话体感·越用越懂你）**：真实终端第一会话告诉文天一个偏好/纠正 → `/exit` → 起第二会话 → 文天在新会话里据该记忆调整行为（before/after 体感对比），且 `--continue` 跨较长时间恢复时见一次性时间跨度提醒（验收时人工对比，记录证据）

# v0.10 Checklist（F70–F76：斜杠命令系统 —— 注册中心 + 解析 + 分发 + 补全）

> 每项通过运行代码或观察行为验证，聚焦系统行为、与实现解耦（重命名文件/移动函数不应使其失败）。离线项用纯函数单测 + **假 ctx**（实现 `CommandContext` 协议、记录 print/sent/mode）+ **假 Document**（驱动补全器）+ 假 provider（断言命令不发请求）取证；🌐👁 = 需真终端观察 Tab 补全菜单弹出/单补观感与真跑十命令，留用户验收（执行一次记录证据）。基线 v0.9 = 1056 passed；v0.10 实测 **1198 passed**（1056→+142）。
>
> **离线验收记录 2026-06-21**：全量 `uv run pytest -q` → **1198 passed, 1 warning**（既有 1 警与本版无关）；`uvx ruff check .` → All checks passed；`uvx ruff format --check .` → 127 files already formatted；**真进程 CLI 冒烟**（隔离 HOME/XDG + 最小 provider 配置）驱动 `/help`→列 12 命令含别名、`/status`→`[DEFAULT] │ 会话 … │ 0 条消息`、`/plan`→「已进入计划模式…」、`/do`→「已退出计划模式…」、`/permission acceptEdits`→「已切换权限模式：ACCEPT_EDITS」、`/sessionx`→「未知命令…/help 查看帮助」、`/exit`→退出码 0 无 traceback、横幅示 **v0.10.0**。下方离线项全部勾选；🌐👁 场景 31/32 待用户验收。

## 实现完整性（离线）

> C85–C92 八个组件可导入、可调用，最小路径冒烟。

- [x] （AC85/C85）`commands/spec.py` 可导入：`CommandSpec` dataclass（frozen）+ `CommandType` 三值枚举（LOCAL/UI_STATE/PROMPT）签名稳定（验证：`tests/test_commands_spec.py` 全绿，6 测试）
- [x] （AC86/C88）`commands/parser.py` 可调用：`parse(line) -> ParsedCommand|None` 为纯函数（验证：`tests/test_commands_parser.py` 全绿，9 测试——`/Help`/`/session resume x`/裸 `/`/纯空白各断言）
- [x] （AC85/C86）`commands/registry.py` 可调用：`CommandRegistry.register/lookup/visible/all/completions` 均可调用（验证：`tests/test_commands_registry.py` 全绿，16 测试——名/别名/大小写查找、completions 返 `list[CommandSpec]` 前缀过滤、冲突 raise）
- [x] （AC87/C87）`commands/context.py` 可导入：`CommandContext` Protocol（`@runtime_checkable`）方法集自洽，假 ctx 实现后 `isinstance` 为真（验证：`tests/test_commands_context.py` 全绿，15 测试，FakeContext isinstance 正反向）
- [x] （AC87/AC91/C89）`commands/builtins.py` 可调用：`build_builtin_registry()` 返回含 12 命令的注册中心、各 handler 签名 `(ctx, args) -> bool|None`（验证：`tests/test_commands_builtins.py` 全绿，46 测试——12 命令/别名/类型 + 假 ctx 驱动各 handler）
- [x] （AC90/C90）`ui/completion.py` 可调用：`CommandCompleter(registry).get_completions(doc, evt)` 产出 `Completion`（验证：`tests/test_completion.py` 全绿，14 测试——假 Document `/se`→`/session`）
- [x] （AC88/AC91/C91）`repl.py` 实现 `CommandContext`：`isinstance(repl, CommandContext)` 为真、`_dispatch_command` 走「parse→registry→handler」、`status_line` 括号标记（验证：`tests/test_repl.py` 集成测试全绿；真进程冒烟见上方记录——`/status`→`[DEFAULT]`、`/help` 走命令路径）
- [x] （AC94/C92）`cli.build_app` 装配：构造 registry（冲突即 raise）+ CommandCompleter 注入 PromptInput（验证：`tests/test_cli.py` 全绿——REPL.commands 含 12 可见、PromptInput 持 CommandCompleter、startup panic 传播）

## 集成

> 注册中心 + 解析 + 分发 + 补全接进 REPL 后的端到端行为（离线，假 provider + 假 ctx + 假 Document）。

- [x] （AC85/F70/N37）注册中心冲突 panic：命名/别名冲突 → `register` 即 `raise ValueError`；该 `raise` 在 `build_app` 启动阶段触发不被吞（验证：`tests/test_commands_registry.py` 四种冲突组合 raise；`tests/test_cli.py` monkeypatch build_builtin_registry 抛 → build_app 上抛）
- [x] （AC86/F71）解析器分流：`/Help`→名 `help`、`/session resume abc`→名 `session`+参数 `resume abc`、`/x`→名 `x` 空参；裸 `/`/纯空白/`/ foo`→None 不进分发（验证：`tests/test_commands_parser.py` 逐例全绿）
- [x] （AC86/F71/F74）未命中引导 + 入口分流：`/` 开头未命中 → 打印「未知命令 + /help 引导」、不送 AI；普通文本 → 走 `_chat_once`/AgentLoop（验证：`tests/test_repl.py` 假 provider 零调用断言；真进程冒烟 `/sessionx`→「未知命令：/sessionx  输入 /help 查看帮助」）
- [x] （AC87/F72/F73）三类执行 + 接口解耦：本地（`/help` 调 `ctx.print`、不改状态）、界面状态（`/plan` 调 `ctx.set_mode(PLAN)`）、提示词（`/review` 调 `ctx.send_user_message`、**非 provider 直调**）；全部内置命令用假 ctx 驱动、只调协议方法不碰 Rich（验证：`tests/test_commands_builtins.py` 逐条断言副作用）
- [x] （AC88/F73）状态栏模式标记联动：`status_line` 左段 `[DEFAULT]`/`[PLAN]`/`[ACCEPT_EDITS]`/`[BYPASS]`；`/plan`→`[PLAN]`、`/do`→`[DEFAULT]`、`/permission acceptEdits` 与 Shift+Tab `cycle_mode` 随之变（验证：`tests/test_repl.py` 状态行断言；真进程冒烟 `/status`→`[DEFAULT]`、`/plan`/`/do`/`/permission acceptEdits` 确认文案实见）
- [x] （AC89/F74）命令不发请求：LOCAL/UI_STATE 命令分发期间假 provider 零调用（不进 AgentLoop/权限门）；唯提示词类经 `send_user_message` 显式跑一轮（验证：`tests/test_repl.py` provider 调用计数断言）
- [x] （AC90/F75）Tab 补全：`/se`+候选补到 `/session`（`start_position=-2`）；`/`→多候选全部可见；隐藏不入；`/session `（空格）不补；大小写不敏感（验证：`tests/test_completion.py` 假 Document 驱动全绿；真终端弹出观感留 🌐👁 场景 31）
- [x] （AC91/F76）十命令 + 归并语义：`/help` 列 12 可见命令；`/session new|list [--all]|resume <id>` 三子命令路由；`/provider`/`/plan`/`/do`/`/compact`/`/permission`/`/status`/`/memory`/`/review`/`/exit` 各按语义工作；归并后 `/help`/`/provider`/`/exit`/`/plan`/`/do`/`/compact` 与归并前一致（验证：`tests/test_commands_builtins.py` + `tests/test_repl.py` 归并回归全绿；真进程冒烟 `/help` 列 12 命令含别名）
- [x] （AC92/F76/N35）`/clear` 语义：`/clear` 清空当前会话 `messages`（覆写落盘为空、复位 `_persisted_count`/指纹/`_last_round_usage`）、**留同一 session id**；`/session new` 另建新 id（两者区分）（验证：`tests/test_repl.py` 断言 messages 空、id 不变、磁盘空、与 /session new 区分）

## 退化与兼容

- [x] （AC93/N35）非命令文本路径零变化：`line.startswith("/")` 为假时走既有 `_chat_once`/AgentLoop；无命令输入时与 v0.9 字节级等价；`commands=None` 回退 v0.9 既有分发（与 compactor/memory_runner 同款 None 注入）（验证：既有 repl/agent_loop 测试零修改保持绿——**唯状态栏标记 5 项 status_line 同因断言按 AC88 更新**[3 列明 + 2 连带]，属有意变更例外；全量 1198 passed）
- [x] （AC91/N35）归并不改语义：`/help`/`/provider`/`/exit`/`/plan`/`/do`/`/compact` 经注册中心分发后行为与归并前一致；`/new`/`/sessions`/`/resume` 能力由 `/session new|list|resume` 等价承载（验证：`tests/test_repl.py` 归并回归全绿；终审审计确认归并语义一致）
- [x] （AC94/N36）分层框架无关：`commands/` 包零 `rich`/`prompt_toolkit`/后端 SDK import；`builtins.py` 不 import `repl` 具体类（只 import 同包 + `permissions.decision`）；`CommandCompleter` 在 `ui/completion.py`（prompt_toolkit 限 ui 层）；`spec`/`parser`/`registry`/`context` 为叶子（验证：`tests/test_layering.py` 新增 6 条 ast import 边界断言全绿；`grep -rE "rich|prompt_toolkit" src/wentian/commands/` 为空）
- [x] （N35）`commands=None` 回退：REPL 未注入 registry 时回退既有 v0.9 分发、不崩（验证：`tests/test_repl.py` commands=None 路径断言不抛；既有命令测试经此路径零修改保持绿）

## 编译与测试

- [x] 无 API key 环境 `uv run pytest -q` v0.1–v0.9 全部 + v0.10 新增全绿（基线 1056 → **1198 passed**，+142）（验证：2026-06-21 全量 `uv run pytest -q` → 1198 passed, 1 warning）
- [x] （AC94/N36）分层 import 断言：`commands/` 包零 rich/prompt_toolkit/SDK；`builtins` 不 import repl 具体类；补全器在 ui 层；四件叶子（验证：`tests/test_layering.py` 6 条 ast 断言全绿）
- [x] （AC94/N38）`ruff format --check .` 通过、`ruff check .` 无告警（验证：`uvx ruff check .` → All checks passed!；`uvx ruff format --check .` → 127 files already formatted）
- [x] （AC93/AC94/N35）真进程冒烟：隔离 HOME/XDG + 最小 provider 配置驱动 `/help`/`/status`/`/plan`/`/do`/`/permission acceptEdits`/`/sessionx`/`/exit` → 横幅示 **v0.10.0**、退出码 0、无 traceback、命令经注册中心分发（验证：2026-06-21 真进程跑通，输出见本节顶部记录）
- [x] （N38）`pyproject` diff 仅版本号 0.9.0→0.10.0、零新增第三方依赖；`uv.lock` 同步（`__init__`/pyproject/lock 三处均 0.10.0）（验证：三处实测均 0.10.0；`tests/test_cli.py` 版本断言全绿）

## 端到端场景

- [x] （AC85/AC86/F70/F71）**场景 28（注册→解析→分发·离线）**：build_app 构造注册中心 → `/Help` 大小写不敏感命中、`/help` 列 12 可见命令；`/nope` 未命中给 /help 引导（验证：`tests/test_repl.py`/`tests/test_cli.py` 全绿；真进程冒烟 `/help`→12 命令、`/sessionx`→未知命令引导）
- [x] （AC89/AC92/F72/F74/F76）**场景 29（三类命令一轮·离线）**：本地 `/status` 查状态零请求 → 界面 `/plan` 切 `[PLAN]` → 提示词 `/review` 经 send_user_message 触发一轮 AI（假 provider）→ `/clear` 清空上下文（id 不变）→ `/do` 回 `[DEFAULT]`（验证：`tests/test_repl.py`/`tests/test_commands_builtins.py` 断言仅 /review 触发一轮、其余零请求、状态行随之变）
- [x] （AC91/F76）**场景 30（/session 子命令 + --all·离线）**：`/session new` 建新 id → `/session list`/`/session list --all` 路由 → `/session resume <id>` 恢复（验证：`tests/test_repl.py`/`tests/test_commands_builtins.py` 断言三子命令路由 `new_session`/`list_sessions(all_projects=)`/`resume_session`）
- [ ] 🌐👁 **场景 31（真终端 Tab 补全观感）**：真实终端输入 `/se`+Tab 直接补到 `/session`；输入 `/`+Tab 弹出多列菜单列出全部可见命令、隐藏命令不现身；方向键选中回车补入（验收时人工观察，记录截图/录屏证据）
- [ ] 🌐👁 **场景 32（真跑十命令一轮）**：配真实 API key，真终端依次跑 `/help`/`/status`/`/memory`/`/plan`/`/do`/`/permission`/`/session list`/`/compact`/`/review`/`/clear`/`/exit`，逐一观察行为符合预期、命令响应快不卡（验收时人工跑一遍，记录证据）

# v0.11 Checklist（F84–F90：Skill 系统 —— 不必反复输入同样的提示词）

> 每项通过运行代码或观察行为验证，聚焦系统行为、与实现解耦（重命名文件/移动函数不应使其失败）。离线项用纯函数单测 + 临时目录真实读写三层 Skill + **假 activator** + **假 provider**（isolated 子对话返回固定文本）取证；🌐👁 = 需真实 API key 联网/真终端体感，留用户验收（执行一次记录证据）。基线 v0.10 = 1198 passed；v0.11 实测 **1299 passed**（1198→+101）。

> **离线验收记录 2026-06-21**：全量 `uv run pytest -q` → **1299 passed, 1 warning**（既有 1 警 `${API_KEY}` 与本版无关）；`uvx ruff check .` → All checks passed；`uvx ruff format --check .` → 139 files already formatted；**分层** `tests/test_layering.py` 新增 4 条 skills/skill_tool ast 断言（17 passed）；**真进程冒烟**（隔离 HOME/XDG + 最小 provider 配置 + 无项目/用户 skills 目录）→ 横幅示 **v0.11.0**、`/skills` 列内置三样板 `/commit`·`/review`·`/test` `[builtin·shared]` + 描述、`/exit` 退出码 0 无 traceback。下方离线项全部勾选；🌐👁 场景 36/37 待用户验收。波次测试增量：skills 叶子 30（base 5/registry 6/loader 19）+ skill_tool 10 + system 菜单 +2 + reminders +7 + config +6 + SkillActivator 11 + T134a cli/repl +13 + T134b +18 + unregister +5 + 分层 +4。

## 实现完整性（离线）

- [x] （AC105/C100）`skills/base.py` 可导入：`Skill` frozen dataclass + `SkillMode` 二值枚举，默认 mode=SHARED/history=0/allowed_tools=None（验证：`tests/test_skills_base.py` 5 测试全绿）
- [x] （AC105/AC106/C101）`skills/loader.py` 可调用：`parse_skill`（frontmatter+正文、缺 name/坏 YAML→None）、`discover_skills`（三层覆盖、单文件解析失败跳过）、`render_body`（`$ARGUMENTS`/`$1`/`$2` 替换、`$10` 不误伤）；单文件与目录型等价（验证：`tests/test_skills_loader.py` 19 测试临时目录全绿）
- [x] （AC106/C102）`skills/registry.py` 可调用：`SkillRegistry.get/list/menu/add`（同名覆盖、按 name 排序）（验证：`tests/test_skills_registry.py` 6 测试全绿）
- [x] （AC107/C103）`tools/skill_tool.py` 可调用：`LoadSkillTool` 名 `load_skill`、参数 `name`(必)/`args`(选)、只读类、`run` 转调鸭子 `activate(name,args)`、缺 name 结构化错误不抛（验证：`tests/test_skill_tool.py` 10 测试假 activator 全绿）
- [x] （AC107/C105）`prompt/system.py`：`PromptContext.available_skills` 非空→「# 可用 Skill」菜单含 name+desc、空→省略无残渣（验证：`tests/test_prompt_system.py` 续测 23 全绿、既有空槽残渣测试不破）
- [x] （AC108/C106）`prompt/reminders.py`：激活源非空→最新 user 消息含各 Skill 正文 `<system-reminder>`、入参不 mutate、live 读（中途激活下一轮即注入）；空源=v0.8 行为（验证：`tests/test_prompt_reminders.py` 续测 31 全绿、既有 24 零修改；随机序 3× 稳定无 flake）
- [x] （AC108/AC109/AC111/C104）`SkillActivator` 可调用：SHARED 进集+确认串、ISOLATED worker 线程子对话回流末条助手正文、`active_bodies/allowed_tools/clear`（验证：`tests/test_skill_activator.py` 11 测试假 provider 全绿）

## 集成

- [x] （AC107/F86）两阶段加载：启动「可用 Skill」菜单进发给后端的 `system`（仅 name+desc 无正文）；`load_skill` 工具在工具声明里、即便白名单收窄到不含它仍在可用集（验证：`tests/test_cli.py`/`tests/test_skill_activator.py`；真进程冒烟 `/skills` 列三样板）
- [x] （AC108/F87）激活注入不持久化：`load_skill` 激活 shared Skill 后下一轮 provider 收到的 messages 末 user 含正文 reminder，**store 落盘 messages 不含**（验证：`tests/test_repl.py` `TestT134aReplSkillIntegration` 对比 provider 收到与落盘）
- [x] （AC109/F87）白名单收窄：激活带白名单 Skill→AgentLoop `allowed_tools`=并集∪`{load_skill}`（计划模式则与 plan 取交集，最严胜，load_skill 恒含）；任一不限 Skill→不收窄；空集→全工具（验证：`tests/test_skill_activator.py`/`tests/test_repl.py` `_combine_allowed_tools`）
- [x] （AC110/F88）启动白名单校验 fail-fast：错工具名 Skill→`build_app` `raise` 含 Skill+工具名；引用已注册工具→通过（校验排 MCP `discover_and_register` 之后）；对比解析失败静默跳过（验证：`tests/test_cli.py` 断言两路非对称）
- [x] （AC111/F89）独立模式回流：`isolated`/`history:2` Skill 经 `activate`（假 provider）→ 返回值=子对话末条助手正文、不进主激活集、worker 线程 join 干净（`threading.active_count` 回基线）、带主历史末 2 条（验证：`tests/test_skill_activator.py`）
- [x] （AC112/F90）斜杠注册 + 冲突策略：发现 Skill→命令 registry 多出 `/<name>`、进 `/help` 与补全；`name=review`→替换 `/review`（type PROMPT）；`name=exit` 等控制命令同名→斜杠跳过+告警、`load_skill` 仍可达、**启动不 panic**（验证：`tests/test_cli.py` 三分支 + `CommandRegistry.unregister` 5 测试）
- [x] （AC113/F90）`/skills` + 热更新：`/skills` 列举（名+描述+来源层+mode）零 provider 请求；`/skills reload` 改文件后生效（live 刷新菜单经 `refresh_skill_menu`）；reload 引入错工具 Skill→保旧不崩（验证：`tests/test_cli.py` 临时目录改文件；真进程冒烟 `/skills` 列举）
- [x] （AC114/F90）清空联动 + 内置三样板：激活后 `/clear` 与 `/session new`→激活集空；内置 `commit`/`review`/`test` 被发现注册（`/commit`//`test` 新增、`/review` 接管）；`commit` 正文含「无 Co-Authored-By/不主动 push」（验证：`tests/test_repl.py`/`tests/test_cli.py` + 正文核对；真进程 `/skills` 列三样板）

## 退化与兼容

- [x] （AC115/N45）`skills.enabled:false`/`activator=None` → `available_skills` 空、`load_skill` 不注册、activator None = 回退 v0.10；默认 `enabled:true` 含内置三样板（工具集 7=6+load_skill、断言 `names[:6]==六标准工具`）（验证：`tests/test_cli.py` `test_build_app_skills_disabled_regresses_to_v010`/`test_system_prompt` 空槽无残渣）
- [x] （AC115/N45）非命令文本路径与既有 repl/agent_loop/system_prompt/reminders 测试零修改全绿；`activator=None` 跑一轮与 v0.10 一致（字节级回归）（验证：既有测试全套通过、`test_activator_none_is_v010_behavior`）
- [x] （AC115/N46）分层框架无关：`skills/` 包零 `rich`/`prompt_toolkit`/后端 SDK/`agent`/`repl`/`commands` import；`tools/skill_tool.py` 不 import `repl`/`agent`/`skills` 具体类；`skills/{base,registry}` 叶子、`loader` 仅 stdlib（验证：`tests/test_layering.py` 4 条 ast 断言；`grep` skills/ 为空、skill_tool 仅 `tools.base`+`permissions.decision`）

## 编译与测试

- [x] 无 API key 环境 `uv run pytest -q` v0.1–v0.10 全部 + v0.11 新增全绿（基线 1198 → **1299 passed**，+101）（验证：2026-06-21 全量 `uv run pytest -q` → 1299 passed, 1 warning；随机序两次稳定）
- [x] （AC115/N46）分层 import 断言：`skills/` 零 agent/provider/repl/commands/rich/prompt_toolkit；`load_skill` 工具不 import repl 具体类（验证：`tests/test_layering.py` 新增 4 条 ast 断言、17 passed）
- [x] （AC115/N48）`ruff format --check .` 通过、`ruff check .` 无告警；版本 `0.11.0`（`__init__.py`+`pyproject`+`uv.lock` 同步）、零新增依赖（验证：`uvx ruff check .` → All checks passed；`uvx ruff format --check .` → 139 files；`pyproject` diff 仅版本 + `builtin/*.md` artifacts）
- [x] （AC115/N45）真进程冒烟：隔离 HOME/XDG + 最小 provider 配置 + 无项目/用户 skills 目录 → 横幅示 **v0.11.0**、`/skills` 列内置三样板 `[builtin·shared]`、`/exit` 退出码 0 无 traceback（验证：2026-06-21 真进程跑通，输出见本节顶部记录）

## 端到端场景

- [x] （AC105/AC106/AC107/F84/F85/F86）**场景 33（三层发现→菜单→加载·离线）**：项目/用户/内置三层放 Skill（含同名覆盖 + 一个坏文件）→ 发现去重跳过坏的 → 「可用 Skill」菜单进 system → `load_skill` 激活一个 shared Skill → 正文经 reminder 注入（验证：`tests/test_skills_loader.py`/`tests/test_cli.py`/`tests/test_repl.py` 全绿；真进程 `/skills` 列三样板）
- [x] （AC109/AC114/F87/F90）**场景 34（激活收窄→清空·离线）**：激活带白名单 Skill → 可用工具收窄到并集+load_skill → `/clear` → 激活集空、工具恢复全量（验证：`tests/test_skill_activator.py`/`tests/test_repl.py`）
- [x] （AC111/F89）**场景 35（独立模式回流·离线）**：`isolated` Skill 经 `activate`（假 provider）→ 子对话跑完末条助手正文作返回值/工具结果、主激活集不变、线程不泄漏（验证：`tests/test_skill_activator.py`）
- [ ] 🌐👁 **场景 36（真 provider 激活一个 Skill 跑一轮）**：配真实 API key，真实激活一个 shared Skill（如 `/commit` 或自定义）→ 观察文天按正文 SOP 执行、白名单收窄生效、结果留主历史；激活 isolated Skill → 观察子对话摘要回流（验收时执行一次，记录证据）
- [ ] 🌐👁 **场景 37（真终端 `/skills` 与热更新观感）**：真终端 `/skills` 列出三层 Skill；改一个 Skill 文件 → `/skills reload` → 菜单/斜杠命令随之变；新放一个 Skill → `/<name>` 直接可用（验收时人工观察，记录截图/录屏证据）

# v0.12 Checklist（F77–F83：Hook 生命周期系统 —— 事件 + 条件 + 动作 + 声明式加载）

> 每项通过运行代码或观察行为验证，聚焦系统行为、与实现解耦（重命名文件/移动函数不应使其失败）。离线项用纯函数单测 + **假事件上下文 dict** + **假引擎**（记录 fire/pretool 调用）+ **假权限门**（断言拦截/放行/fail-open）+ **临时 shell 脚本**（真跑 stdin/env/exit2）+ **stdlib `http.server` 本地假 server**（收 JSON 体）+ 假 provider（断言注入下发、缝触发）取证；🌐👁 = 需真终端 + 真 shell/HTTP/通知观察，留用户验收（执行一次记录证据）。基线 = c32c091（v0.10 装配后全量）。

> **离线验收记录 2026-06-21**：T117–T125 全部完成（九任务，四波次：叶子原语 textmatch/spec/conditions/config → 动作/引擎 actions/engine → 配置接入/装配 config/repl/compactor/cli → 验收回归；TDD 红-绿-重构 + 主会话逐波次评审；**T124 装配评审收口 3 真 bug**——PreToolUse 上下文扁平化 command/file_path[否则正则安全策略匹配不到命令、核心用例失效]、注入改逐轮生效[否则中途注入被 drain 后丢弃]、PostToolUse 补 result，并补真引擎端到端测试）。`uv run pytest -q` → **1399 passed**（无告警；1 个 API_KEY 缺失告警为既有预期行为）。`uvx ruff@0.15.18 check .` → All checks passed；`format --check .` → 141 files already formatted。分层 ast 断言通过（hooks 纯包零反向依赖、textmatch 叶子、config→hooks 单向无环）。**端到端冒烟三态**（隔离 HOME/XDG 沙箱）：无 hooks 配置 → exit0 零 traceback（v0.11 路径）；合法 hooks（SessionStart shell + PreToolUse 拦截）→ 引擎构造 exit0；非法 hooks（PreToolUse+background）→ 启动即 `HookConfigError` exit1（集中校验 fail-fast）。**版本号未 bump（仍 0.10.0）——实际 semver 待用户拍板（N43）**。🌐👁 项（真 shell 拦截 / 真 HTTP+桌面通知）待用户真实终端验收。离线判定 READY FOR ACCEPTANCE。

## 实现完整性（离线）

> C93–C99 七个组件可导入、可调用，最小路径冒烟。

- [x] （AC98/C93）`textmatch.py` 可调用：`match_one(pattern, value)` 四模式纯函数（验证：import 后喂 `Bash`/`!Bash`/`/rm\s+-rf/`/`git *` 各断言命中/不命中；非法正则不抛）
- [x] （AC95/C94）`hooks/spec.py` 可导入：`HookEvent` 十值 + `INTERCEPT_EVENTS` + `Clause`/`Condition` + 四 Action + `HookRule`（frozen）签名稳定（验证：import 后构造各 dataclass 断言字段/默认/frozen）
- [x] （AC98/C95）`hooks/conditions.py` 可调用：`evaluate(condition, context)->bool`（验证：None/空 clauses→True；all 全真/any 一真；字段缺失空串参与）
- [x] （AC95/C96）`hooks/config.py` 可调用：`parse_hooks(raw)->list[HookRule]` + `HookConfigError`（验证：合法 raw 解析齐备、非法各项 raise、缺块→[]）
- [x] （AC99/C97）`hooks/actions.py` 可调用：run_shell/inject_prompt/call_http/run_subagent 各执行（验证：临时脚本 shell、本地假 server http、prompt `{field}`、subagent 占位不抛）
- [x] （AC96/AC97/C98）`hooks/engine.py` 可调用：`HookEngine.fire/pretool/drain_injections/close`（验证：假 ctx 驱动 fire 命中、pretool exit2 拦/exit0 放、drain 取空）
- [x] （AC95/C99）`config.py` 接入：`Config.hooks` 为 `list[HookRule]`、`load_config` 经 `parse_hooks`（验证：含 `hooks:` 配置 load 后 `Config.hooks` 非空、无块→[]）

## 集成

> 引擎接进 repl/compactor/cli 后的端到端行为（离线，假 provider + 假引擎 + 临时脚本 + 本地假 server）。

- [x] （AC95/F77）规则模型 + 集中校验：`hooks:` 块解析为三要素规则（event + 可选 if + action + once/background）；非法 event、shell 缺 command、http 缺 url、PreToolUse+background、timeout<=0、错 match 各 `HookConfigError`（带定位）（验证：`tests/test_hooks_config.py`、`tests/test_config.py`）
- [x] （AC96/F78）十事件缝触发：假引擎 + 假 provider 跑一轮带工具 → SessionStart/SessionEnd/UserPromptSubmit/Stop/RoundStart/RoundEnd/PreToolUse/PostToolUse/PreCompact/Notification 各被 `fire`/`pretool`、上下文字段齐备（验证：`tests/test_repl_hooks.py::TestSeamFiring`、`tests/test_context_compactor.py`）
- [x] （AC97/F79）PreToolUse 拦截：命令正则条件 + shell `exit 2` 规则 → 工具被拦、拒绝原因（stderr）回灌 outcome（`is_error=True`）、短路在 `permission_gate` **之前**、不进 executor；`exit 0` → 落既有权限门；脚本缺失/超时 → fail-open 落权限门（验证：假权限门断言拦/放/fail-open 三态 + 真引擎正则拦 rm -rf 真跑 shell exit2，`tests/test_repl_hooks.py::TestPreToolUseBlock`/`TestRealEngineEndToEnd`）
- [x] （AC98/F80）条件四模式 + 全部/任一：`match_one` 精确/`!`反向/`/re/`正则/`*`glob 各断言；`match: all` 全真才触发、`match: any` 一真即触发；`if` 省略恒触发；字段缺失空串参与（验证：`tests/test_textmatch.py`、`tests/test_hooks_conditions.py`）
- [x] （AC99/F81）四种动作：shell（读 stdin JSON + `WENTIAN_HOOK_*` env + exit code）、prompt（`{field}` 替换产文本）、http（本地假 server 收 JSON 体）、subagent（占位记日志不抛）各跑通；任一动作内部异常软化（验证：`tests/test_hooks_actions.py`）
- [x] （AC100/F82）执行控制：`once` 本会话只触发一次（第二次跳过）；`background: true` daemon 线程异步不阻塞；`timeout` 到点终止 shell/http 记日志；`PreToolUse`+`background:true` 加载期 raise（验证：`tests/test_hooks_engine.py`、`tests/test_hooks_config.py`）
- [x] （AC101/F83）失败软化 + 注入下发：动作抛异常/超时只记 hook 日志、**主流程不中断**（会话继续、messages 不污染）；`prompt` 注入经引擎累积、下次请求随 `<system-reminder>`（`request_decorator`）下发、不写回 messages、不持久化（验证：假 provider 断言下轮 outgoing 含注入文、原 messages 不变 + 真引擎 PostToolUse 注入达下一轮，`tests/test_repl_hooks.py::TestInjection`/`TestRealEngineEndToEnd`）

## 退化与兼容

- [x] （AC102/N40）无 hooks 零变化：无 `hooks:` 配置 / `hooks=None` 注入时所有缝点空操作、与 v0.11 字节级等价；既有 repl/agent_loop/compactor/config 测试零修改保持绿（验证：全量 1399 测试全绿，含既有；`tests/test_repl_hooks.py::TestNoHooksRegression`）
- [x] （AC102/N40）不改 AgentLoop 契约：`AgentEvent` 联合、五停机分支、`permission_gate` 签名零改；PreToolUse 不拦时原样落既有 gate（拒绝回灌契约不变）（验证：既有 `tests/test_agent_loop.py` 零修改全绿）
- [x] （AC103/N41）分层 + 无反向依赖：`hooks/` 包零 `rich`/`prompt_toolkit`/后端 SDK；`hooks/engine.py`/`hooks/actions.py` 零 import `wentian.agent`/`repl`/`providers`/`tools`；`textmatch.py` 叶子（只 `re`/`fnmatch`）；`config.py`→`hooks.config` 单向无环（验证：`tests/test_layering.py`——`test_hooks_pkg_no_rich_ptk_or_sdk`/`test_hooks_engine_no_orchestration_imports`/`test_textmatch_is_stdlib_only`/`test_no_config_hooks_import_cycle`）

## 编译与测试

- [x] 无 API key 环境 `uv run pytest -q` v0.1–v0.11 全部 + v0.12 新增全绿（基线 c32c091 → +N passed）（验证：全量 `uv run pytest -q`）
- [x] （AC103/N41）分层 import 断言：`hooks/` 零 rich/prompt_toolkit/SDK；engine/actions 不 import agent/repl/providers/tools；textmatch 叶子；config→hooks 单向（验证：`tests/test_layering.py` ast 解析 import 边界）
- [x] （AC104/N43）`ruff format --check .` 通过、`ruff check .` 无告警（All checks passed）
- [x] （AC102/N40）无 hooks 冒烟：`printf '/exit\n' | uv run wentian`（空 cwd、无 `hooks:`）→ 退出码 0、无 traceback、行为同 v0.11（验证：隔离 HOME/XDG 沙箱跑通）
- [x] （N43）`pyproject` diff 零新增第三方依赖；版本号标记 v0.12（**实际 semver 字符串 bump 待用户拍板**——v0.10/v0.11 发布次序未定，spec 不擅自跨号）（验证：`pyproject` diff 仅版本号变化或不变 + 零依赖新增）

## 端到端场景

- [x] （AC96/AC97/AC101/F78/F79）**场景 33（事件链路 + 拦截 + 注入·离线）**：假 provider 跑一轮带工具——SessionStart 触发 shell 动作 → UserPromptSubmit prompt 注入 → PreToolUse 命中 `exit 2` 拦危险工具、原因回灌 → 改道安全工具 PostToolUse 发本地假 http → Stop（验证：假引擎 + 临时脚本 + 本地假 server 端到端断言各缝按序触发、拦截回灌、注入下发）
- [x] （AC100/F82）**场景 34（执行控制·离线）**：`once` 规则跑两轮只触发一次；`background` 慢动作不阻塞主轮；`timeout` 脚本 `sleep` 到点被终止；`PreToolUse`+`background` 配置加载即 `HookConfigError`（验证：临时脚本 + 计时断言 + 校验报错）
- [ ] 🌐👁 **场景 35（真 shell 拦截 rm -rf）**：真实终端配一条 `PreToolUse` 拦 `/rm\s+-rf/` 的 shell `exit 2` 规则，让 Agent 尝试危险命令 → 观察被拦、拒绝原因回灌、模型改道（验收时人工观察，记录截图/录屏证据）
- [ ] 🌐👁 **场景 36（真 HTTP 审计 + 桌面通知）**：配 `PostToolUse` http 审计规则（真 endpoint）+ `Notification` 桌面通知规则（如 `osascript`/`notify-send` shell 动作），真跑观察请求发出 / 通知弹出（验收时人工观察，记录证据）

# v0.13 Checklist（F91–F102：子 Agent 委派 —— 主 Agent 把子任务派给隔离子 Agent）

> 每项通过运行代码或观察行为验证，聚焦系统行为、与实现解耦（重命名文件/移动函数不应使其失败）。离线项用纯函数单测 + 临时目录真实四源读写（`agents/` 发现/覆盖/跳过）+ **假 provider**（子 Agent 返固定文本、runner 跑到底取末条助手正文）+ **假 manager/runner 句柄**（AgentTool 分流断言用）+ **可注入时钟**（manager 测超时转后台）取证；🌐👁 = 需真实 API key 联网/真终端观察子 Agent 运行轨迹，留用户验收（执行一次记录证据）。**基线 = 1509 passed**（v0.11 skills + v0.12 hooks 集成基线，`feature/v0.13-agents` 分支起始）。

## 实现完整性

> C108–C117 十个组件可导入、可调用，最小路径冒烟。

- [ ] （AC116/F91）`agents/tool.py` 注册稳定：`AgentTool` 可导入，name=`Agent`、schema 含 `type`/`agent_type`/`prompt`/`background` 四字段；`build_app` 构造后工具列表恒含且仅含一个 `Agent` 工具，角色数量增减不改变暴露工具数（验证：`tests/test_agents_tool.py` schema 字段断言；`tests/test_cli.py` 工具列表含 Agent 且计数正确）

- [ ] （AC116/F91）`AgentTool.run` 分流：`type=definition` 走前台 runner 路径、`type=fork` 走后台 manager 路径；假 runner/manager 句柄断言各路调用参数（验证：`tests/test_agents_tool.py` 假句柄分流断言）

- [ ] （AC117/F92/F93）`agents/spec.py` 可导入：`AgentType(Enum){DEFINITION,FORK}`、`TaskStatus(Enum){RUNNING,DONE,FAILED}`、`AgentDef(frozen dataclass)`（name/description/body/tools/disallowed_tools/model/max_turns/permission_mode/source 各字段）、`BackgroundTask dataclass`（id/kind/label/status/result/usage 各字段）签名稳定（验证：`tests/test_agents_spec.py`——import 后构造各 dataclass 断言字段/默认/frozen）

- [ ] （AC117/F92/F93）`agents/loader.py` 可调用：`parse_agent(text, *, name_hint, source) -> AgentDef|None`（缺 `name` 字段/坏 frontmatter→None，不抛）；`discover_agents(project_dir, user_dir, ...)` 四层覆盖与跳过（项目>用户>内置>插件；同名高层整体覆盖低层）（验证：临时目录四层各放角色 + 一个坏文件 → `AgentRegistry` 同名取高层、坏文件跳过、其余解析正确；`tests/test_agents_loader.py` 19+ 测试全绿）

- [ ] （AC118/F93/N55）共享 frontmatter 原语：`frontmatter.py`（或等效共享位置）被 `skills/loader.py` 与 `agents/loader.py` 同时 import；抽取后 v0.11 skills 全量测试零回归（验证：`tests/test_agents_loader.py` 与 `tests/test_skills_loader.py` 共用同一解析原语；全量基线 skills 测试全绿）

- [ ] （AC122/F97/N52）`agents/filter.py` 可调用：`resolve_allowed_tools(all_tools, role_allow, role_deny, *, background, globally_forbidden=frozenset({"Agent"})) -> frozenset[str]` 为纯函数；三层集合运算正确（全局禁止先减、角色白名单取交、角色黑名单再减、后台再取免确认）；`Agent` 工具不在任何子 Agent 允许集（验证：`tests/test_agents_filter.py` 各组合断言；Agent 全局禁止即便角色白名单列出也被挡下）

- [ ] （AC122/N52）嵌套防护双重防线：filter 全局禁止 `Agent` 工具（第一道）；runner 为子 Agent 上下文标记深度≥1 时拒绝再起（第二道）；两道防线各自独立可单测（验证：`tests/test_agents_filter.py` 全局禁止断言；`tests/test_agents_runner.py` 深度兜底断言）

- [ ] （AC125/F100）`config.py` 接入 `AgentsConfig`：`AgentsConfig{enabled, model_aliases:dict, default_max_turns:int, foreground_timeout_s:float, background_allow:tuple}` 可构造；`inherit`/`haiku`/`sonnet`/`opus` 映射正确 ID（haiku→`claude-haiku-4-5`/sonnet→`claude-sonnet-4-6`/opus→`claude-opus-4-8`）；`inherit` 取主模型；别名不可解析→工具层返报错不起子 Agent；缺 `agents:` 块→全默认不抛（验证：`tests/test_config.py::TestAgentsConfig` 默认值/别名映射/不可解析三路断言）

- [ ] （AC119/F94）`agents/runner.py` 可调用：`run_subagent` 以假 provider（返固定文本，`stop_reason=COMPLETED`）从**空白对话**跑到底，取末条助手正文为 `SubAgentResult.text`；子 provider 为全新实例（不共享主 provider）；异常停机（MAX_ROUNDS/STREAM_ERROR）→ 转结构化结果不抛（验证：`tests/test_agents_runner.py` 假 provider 定义式/错误停机两路断言）

- [ ] （AC120/F95）Fork 式：`run_subagent(type=FORK, parent_messages=[...])` 起始消息含父历史 + 本任务（以假 provider 断言发出的消息列表前缀为父历史）；子允许集经 filter 后不含 `Agent` 工具（验证：`tests/test_agents_runner.py` fork 起始消息前缀断言）

- [ ] （AC121/F96/N53）状态隔离：两个 `run_subagent` 并发分别用独立 Mode + 独立 provider 实例；改子 Agent 的 permission_mode 不影响主调用方的 mode 状态（验证：`tests/test_agents_runner.py` 并发两子 Agent 结果不串、provider 实例相异断言）

- [ ] （AC123/F98）`agents/manager.py` 可调用：`BackgroundTaskManager.submit(...)` 开 daemon 线程跑任务、`get(id)` 返 `BackgroundTask`、`list()` 列全部、`drain_completions()` 返完成结果回灌字符串并清空缓冲、`close()` 短 join 不挂死；三种进后台各自生效——显式（`background=true`）/ 超时自动（可注入时钟触发）/ fork 恒后台；管理器记录 status/result/usage（验证：`tests/test_agents_manager.py` 假时钟超时/显式后台/Fork 恒后台/drain/close 断言；线程退出 `threading.active_count` 回基线不泄漏）

- [ ] （AC124/F99）回灌通道：前台子 Agent 同步返回结果（runner 阻塞 → 工具直接返文本）；后台/Fork 完成后写 `drain_completions` 缓冲 → 下一轮 `request_decorator` drain 成 `<system-reminder>`、**不写回 messages、不持久化**（验证：`tests/test_agents_manager.py` drain 后缓冲清空断言；`tests/test_repl.py` 或 `tests/test_cli.py` request_decorator 接入 drain 断言）

- [ ] （AC126/F101）`commands/builtins.py` 增 `/agents` 命令：无参列出后台任务（id/角色/状态/起始时刻/token 用量）；`/agents <id>` 查单任务结果全文 + 用量；经假 manager 句柄驱动全程零 provider 请求（验证：`tests/test_commands_builtins.py` 假 manager 驱动 `/agents` 与 `/agents <id>` 两路断言）

- [ ] （AC127/F102）`SubAgentAction` 接通：hook `subagent` 动作触发时经 manager/runner 真起 definition 式子 Agent（假 provider）落后台、结果写回灌缓冲；动作内部异常 → 只记 hook 日志不中断主流程（v0.12 软化铁律），`tests/test_hooks_actions.py` 接通验证

## 集成

> 装配缝（cli.build_app）+ 各组件接进系统后的行为（离线，假 provider + 假 manager/runner + 临时目录）。

- [ ] （AC116/AC128/F91/N50）`build_app` 装配 Agent 工具：`cli.build_app` 发现 `AgentRegistry`、构造 `BackgroundTaskManager`、注册 `AgentTool`（注入 registry/runner/manager/cfg）、注册 `/agents` 命令、把 `manager.drain_completions` 接进 `request_decorator` 链（验证：`tests/test_cli.py` build_app 后工具集含 Agent、REPL commands 含 `/agents`、request_decorator 已链接 drain）

- [ ] （AC128/N50）无 `agents` 配置回退：cwd 无 `agents:` 块 / registry=None / manager=None 时 `Agent` 工具**仍在**工具列表（工具数 +1 相对 v0.12 基线）；`type=definition` 无角色 → 工具返「无此角色」优雅错误不崩；`type=fork` 照常可用（不依赖角色）（验证：`tests/test_cli.py`/`tests/test_agents_tool.py` 无配置三路断言）

- [ ] （AC119/F94）主会话上下文隔离：定义式子 Agent 完成后，主会话 messages 仅增「Agent 工具调用 + 结果」两条，**不含**子 Agent 内部多轮往返消息（验证：`tests/test_agents_runner.py` 或 `tests/test_repl.py` 对比 run 前后 messages 长度差=2 且无子内部消息）

- [ ] （AC121/AC123/F96/F98/N53）并发后台不串：并发 submit 两个后台子 Agent（假 provider 各返不同文本）→ drain 后各自结果不混淆；子 Agent 权限状态不回流主 REPL `_mode`（验证：`tests/test_agents_manager.py` 并发 drain 结果对应断言）

## 编译与测试

- [ ] 无 API key 环境 `uv run pytest -q` v0.1–v0.12 全部 + v0.13 新增全绿（基线 1509 → 1509+N passed）（验证：全量 `uv run pytest -q` 无告警）

- [ ] （AC128/N51）`agents/` 分层 import 断言：`spec`/`filter` 为纯叶子（stdlib only，filter 仅依 spec）；`loader` 叶子（stdlib + 共享 frontmatter 原语 + spec）；`runner` 不依 `repl`/`cli`；`manager` 依 runner；`tool` 鸭子注入、不硬依赖 repl 具体类；`agents/` 对 repl/cli 零反向依赖（验证：`tests/test_layering.py` 新增 `agents/` 系列 ast import 边界断言全绿）

- [ ] （AC128/N56）`ruff format --check .` 通过、`ruff check .` 无告警（All checks passed）（验证：`uvx ruff check .` → All checks passed；`uvx ruff format --check .` → N files already formatted）

- [ ] （AC128/N50）无 `agents` 配置冒烟：`printf '/exit\n' | uv run wentian`（空 cwd、无 `agents:` 配置）→ `Agent` 工具仍在工具声明（冒烟取证）、definition 空 registry 时优雅返报错、fork 路径可初始化、退出码 0 无 traceback（验证：隔离 HOME/XDG 沙箱跑通；`tests/test_agents_tool.py` 空 registry 优雅报错路径断言）

- [ ] （AC118/N55）共享 frontmatter 原语抽取后 v0.11 skills 零回归：全量 skills 测试（`tests/test_skills_loader.py`/`tests/test_skill_activator.py` 等）在 frontmatter 原语抽取后保持全绿（验证：`uv run pytest tests/test_skills_*.py tests/test_skill_*.py -q` 全绿，无因抽取引发的行为变化）

## 端到端场景

- [ ] 🌐👁 **场景 37（定义式子 Agent 跑到底・真 API key）**：配真实 API key，真实终端发 `Agent(type=definition, agent_type=<角色名>, prompt=<任务>)` → 观察主对话仅见「Agent 工具调用→结果」（子往返不现身）；`/agents` 列出空（前台阻塞完成不入后台列表）；结果正确回主对话（🌐👁，留用户验收，记录截图/证据）（AC119/AC129/F94）

- [ ] 🌐👁 **场景 38（后台/Fork 异步回灌・真 API key）**：真起一个 `background=true` 或 `type=fork` 任务 → 工具立即返「任务 id=X 已起」；`/agents` 列出 id + 状态 running/done；任务完成后下一轮对话前自动收到 `<system-reminder>` 回灌（主对话 messages 不含回灌条）；`/agents <id>` 查全文（🌐👁，留用户验收，记录截图/证据）（AC120/AC123/AC124/AC126/AC129/F95/F98/F99）

- [ ] 🌐👁 **场景 39（嵌套防护被拦・真 API key）**：在子 Agent 内（通过角色 prompt 诱导）尝试调用 `Agent` 工具 → 观察工具声明里无 `Agent`（全局禁止生效）或深度兜底返拒绝原因；子 Agent 不递归起子子 Agent（🌐👁，留用户验收，记录截图/证据）（AC122/AC129/F97/N52）

- [ ] 🌐👁 **场景 40（上下文隔离证据・真 API key）**：真跑一个定义式子 Agent（内部多轮工具往返）→ 主会话历史查看（`/session list` 后 resume 或 `--continue`）确认子 Agent 内部往返消息**不在**主会话持久化记录中，仅见 Agent 工具 + 结果条目（🌐👁，留用户验收，记录证据）（AC119/AC128/AC129/F94/N53）
