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

- [ ] （AC75/C53）`instructions.py` 可导入：`load_project_instructions(cwd, *, user_home=None, cfg)` 为纯函数、签名稳定，临时目录放三层 `WENTIAN.md` 调用返回拼接字符串（验证：import 后构造临时 cwd/user_home 调用，断言返回非空且含三层内容）
- [ ] （AC76/C54）`session.py` JSONL 重构可调用：`SessionStore.append/load/list/load_latest` + `project_sessions_dir(cwd)` + slug 化均可导入调用（验证：临时目录建 store，append 一会话→load 回来消息等价、list 含之）
- [ ] （AC77/C55）恢复卫生函数可调用：`truncate_unpaired(messages)`、`prune_expired(dir, retention_days)`、`resume_gap_reminder(updated_at, now, hours)` 为纯函数/可注入（验证：import 后各喂构造输入断言返回类型与边界）
- [ ] （AC79/C56）`memory/store.py` 可调用：`read_index(scope)` / `write_note(scope, note)` / `upsert_index(...)` + 分级目录（user/project）解析 + 写锁（验证：临时目录 write_note 落 `.md`+frontmatter、read_index 取回摘要）
- [ ] （AC79/C57）`memory/extractor.py` 可调用：`extract(provider, recent_messages, existing_index) -> list[Note]`，解析部分为纯函数（验证：注入假 provider 返回固定四类笔记 JSON，断言解析出 Note 列表）
- [ ] （AC79/C58）`memory/runner.py` 可调用：`MemoryRunner.submit(recent_messages)` 启线程、`close(timeout)` 收尾（验证：构造 runner 注入假 provider+临时 store，submit 后 close 不抛、落盘可见）
- [ ] （AC81/C59）`prompt/system.py` 两槽真渲染可调用：`_render_project_instructions` / `_render_memory` 读 `ctx.project_instructions` / `ctx.memory` 真渲染（v0.5 起恒空、本版起真出内容）；`config.py` 的 `MemoryConfig` / `SessionsConfig` 可解析（验证：构造带两字段的 PromptContext 渲染断言含其内容；空 ctx 渲染无残渣）

## 集成

> 各机制接进系统提示 / 会话流 / 后台线程后的端到端行为（离线，临时目录 + 假 provider）。

- [ ] （AC75/F63）三层指令注入系统提示：三处放不同 `WENTIAN.md`（项目本地覆盖 / 项目根 / 用户全局）→ 注入「项目/自定义指令」模块、顺序高优先级在前；缺层静默跳过；三层均缺则模块为空（验证：临时目录摆三文件，断言发给后端的 system 含三层内容且次序正确）
- [ ] （AC75/F63）`@include` 内联展开：指令文件中独占一行 `@include xxx.md` → 目标文件内容相对「含它的文件所在目录」内联（验证：a.md `@include b.md`，断言 b 内容出现在拼接结果）
- [ ] （AC75/F63）`@include` 防环 + 限深：构造 a→b→a 环 → `visited` 跳过并告警、不无限递归；嵌套超默认深度 5 → 停止并告警（验证：构造环与超深链，断言不挂死、告警出 stderr、结果有限）
- [ ] （AC75/AC84/F63/N32）`@include` 越界拦截：指向项目根外 / 越界绝对路径 / 软链接指向项目外（先解析符号链接再前缀比对，与 v0.6 沙箱同规）→ 拒绝并告警、不读取（验证：临时目录造越界目标 + 指向外部的 symlink，断言其内容不出现在结果、告警出 stderr）
- [ ] （AC75/F63）拼接体积上限：指令总体积超上限 → 按上限截断并告警（验证：造超大指令文件，断言结果不超上限、告警出）
- [ ] （AC76/F64）JSONL 追加写不重写全文：新会话写到 `projects/<cwd-slug>/sessions/<id>.jsonl`、每轮新增消息逐行追加（验证：连续两轮 append，断言文件按字节增长、前缀字节不变——非重写）
- [ ] （AC76/F64）坏行跳过 + 元数据由扫描得出：故意写一条 JSON 坏行 → load 跳过坏行加载其余、告警 stderr 不抛；ID 取文件名、标题取首条 user 消息行、消息数=数消息行（验证：构造含坏行的 JSONL，断言加载消息数正确、ID/标题正确、坏行告警出）
- [ ] （AC76/F64）分区列举默认当前项目 + `--all` 跨项目：`/sessions` 与 `--continue` 默认只扫当前 `<cwd-slug>` 分区；`--all` 跨 `projects/*/sessions/` 全扫；旧全局扁平 `.json` 视为遗留不列不删不报错（验证：两个分区各放会话 + 一个遗留 `.json`，断言默认只列本分区、`all_projects=True` 列全部、遗留不现身）
- [ ] （AC77/F65）尾部未配对 tool_call 截断：历史尾部为「助手 tool_call 无后续工具结果」悬空调用 → 恢复时截断该未配对部分（验证：构造悬空尾部，断言 `truncate_unpaired` 后送两家 provider 转换不产生 400 形状的悬空对）
- [ ] （AC77/F65）溢出复用 v0.8 Compactor 压一次：恢复后 `estimate_total > context_window - margin`（v0.8 estimator 判定）→ 调 v0.8 `Compactor.compact()` 压一次再进入对话（验证：构造超窗历史 + 假 provider 摘要，断言恢复流程调用 Compactor 且压后落到窗内；不重造估算/压缩）
- [ ] （AC77/F65）时间跨度提醒一次性不写回：距上次 `updated_at` 超 `sessions.resume_gap_reminder_hours`（默认 4h）→ 恢复后首轮经 v0.5 `<system-reminder>` 通道注入一条一次性时间跨度提示、绝不写回持久化 messages（验证：构造久远 `updated_at`，断言 provider 收到的 messages 含提醒、store 落盘 messages 不含）
- [ ] （AC78/F66）过期会话清理：构造一个 `updated_at` 超 `sessions.retention_days`（默认 30）的会话 + 一个新的 → 启动惰性清理删旧的（及其 `.artifacts/` 目录）、保留新的；清理失败不致命（告警跳过）；`retention_days` 可配（验证：临时分区造新旧两会话 + 旧的 artifacts 目录，断言旧文件与目录被删、新的在）
- [ ] （AC79/F67）后台抽取四类笔记落盘 + 更新 INDEX：一个 `COMPLETED` 回合后台线程调（假 provider 返回固定四类笔记 JSON）→ 用户偏好/纠正反馈落用户级 `~/.config/wentian/memory/`、项目知识/参考资料落项目级 `<cwd>/.wentian/memory/`、各写带 frontmatter（category/created_at/source_session/tags）的 `.md` + 更新对应 `INDEX.md`（验证：临时目录 + 假 provider，submit→close 后断言四类笔记按归属落盘、frontmatter 完整、INDEX 含新条目）
- [ ] （AC79/F67/N31）抽取异常不崩不污染不阻塞：注入「抽取必抛异常」的假 provider → 异常静默吞到 stderr、会话不中断、对话历史 messages 不被污染、不阻塞下一次输入（验证：假 provider 抛异常，断言主流程不崩、messages 终态不含抽取产物、REPL 输入不被卡）
- [ ] （AC80/F67）LLM 去重不重复追加：已有 `INDEX` 含某条 → 再抽到等价信息、把现有索引喂 LLM、它判「已覆盖」则跳过或更新而非重复追加（验证：脚本化假 provider 返回判重决策，断言第二次抽取后 INDEX 条目数不重复增长）
- [ ] （AC81/F68）两份 INDEX 注入长期记忆模块 + 体积上限：启动读 user+project 两份 `INDEX.md` 拼进 `ctx.memory` → 填「长期记忆」模块、内容可见于发给后端的 `system`；索引超 200 行/25KB（用 v0.8 estimator 卡预算）→ 按上限截断；启动注入一次不热刷（验证：临时目录造两份 INDEX，断言 system 含其内容；造超限 INDEX 断言被截断到上限内）

## 退化与兼容

- [ ] （AC82/F69/N29）配置块缺失走默认：`memory:` / `sessions:` 块缺失或字段缺失安全降级不抛异常，走默认（enabled=true / max_index_lines=200 / max_index_bytes=25600 / retention_days=30 / resume_gap_reminder_hours=4）（验证：空配置构造 `MemoryConfig`/`SessionsConfig`，断言默认值；缺字段不抛）
- [ ] （AC82/F69）`memory.enabled:false` 一键关：关掉后台抽取与启动注入整条链路（验证：config 设 false，断言 build 后无 MemoryRunner、回合后不抽取、`ctx.memory` 不注入）
- [ ] （AC82/N29）无 WENTIAN.md / 无 memory / 无历史会话时行为与 v0.8 一致：两个新槽位（项目指令、长期记忆）为空时系统提示拼装无空行残渣、缓存前缀稳定（启动注入一次、不破坏 v0.5 缓存断点）（验证：裸临时 cwd + 空配置，断言 system 渲染逐字节等价 v0.8 空槽形状、无残渣；既有 v0.1–v0.8 测试全绿）
- [ ] （N29）JSONL 重构对既有层透明：会话持久化对 Agent Loop / v0.8 压缩写回钩子 / 权限门 / provider 适配层透明（验证：既有 `test_agent_loop.py`/`test_repl.py` 对会话写回部分零修改保持绿；v0.8 压缩 RoundEnd 落盘走 JSONL append 不破）
- [ ] （N30）记忆模型自建 provider 不跨线程共享：抽取器自建 provider 实例（不复用对话 provider）、daemon 线程 fire-and-forget；并发写 INDEX 加锁串行（验证：断言抽取 provider 与对话 provider 非同一实例；并发 submit 多条断言 INDEX 写入不交错错乱）
- [ ] （N31）`/exit` 短 join 不卡退出无线程泄漏：`/exit` 时 `runner.close(timeout)` 最多 join 一个短超时（验证：submit 一个慢抽取后 close，断言在超时内返回、不挂死）

## 编译与测试

- [ ] 无 API key 环境 `uv run pytest -q` v0.1–v0.8 全部 + v0.9 新增全绿（基线 905 → 905+N passed）
- [ ] （AC83/N30）分层 import 断言：`instructions.py` 为叶子（stdlib only，零 agent/provider/ui 高层依赖）；`memory/` 包对 agent 编排层零反向依赖、provider 鸭子注入（仿 v0.8 summarizer）；会话层不依赖后端 SDK；复用 v0.8 `context` 包不重造估算/压缩（验证：grep 取证各模块 import 边界，断言无越界依赖）
- [ ] （AC83/N33）`ruff format --check .` 通过、`ruff check .` 无告警（All checks passed）
- [ ] （AC84/N32）落盘不含 api_key：记忆笔记 + 会话 JSONL 落盘内容扫描不含 api_key 等密钥（验证：跑一轮真实落盘后自动化扫描所有产出文件，断言无密钥泄漏）；零新增第三方依赖
- [ ] （AC82/N29）无配置冒烟：`printf '/exit\n' | uv run wentian` → 横幅示 v0.9.0、退出码 0、无 traceback、行为同 v0.8（无 WENTIAN.md/无 memory/无历史会话时无多余输出）（验证：现场跑通）
- [ ] （N33）`pyproject` diff 仅版本号 0.8.0→0.9.0、零新增第三方依赖；`uv.lock` 同步（`__init__`/pyproject/lock 三处均 0.9.0）

## 端到端场景

- [ ] （AC75/F63）**场景 22（指令注入·离线）**：临时项目根放 `WENTIAN.md` + 一个被 `@include` 的子文件 → 启动后断言系统提示「项目/自定义指令」模块含主文件与内联子文件内容、顺序正确（验证：临时目录端到端跑 build_app 链路，断言注入可见）
- [ ] （AC76/AC77/F64/F65）**场景 23（坏行/未配对/溢出仍正常恢复·离线）**：构造一份含坏行 + 尾部未配对 tool_call + 超窗体积的 JSONL → `--continue` 恢复：跳过坏行、截断未配对、调 Compactor 压一次 → 恢复后历史对两家 provider 合法且在窗内、会话正常继续（验证：临时分区造问题会话，断言恢复后 messages 合法、压过一次、无 400 形状）
- [ ] （AC76/F64）**场景 24（`/sessions --all` 跨项目·离线）**：两个 `<cwd-slug>` 分区各放会话 → 默认 `/sessions` 只列当前项目、`/sessions --all` 列两个项目全部（验证：构造两分区，断言默认与 `--all` 列表差异符合预期）
- [ ] （AC79/AC81/F67/F68）**场景 25（越用越懂你·离线半链路）**：临时目录里聊一轮（假 provider 触发 `COMPLETED`）→ 后台抽取四类笔记落盘 + 更新两份 INDEX → 重新 build_app 启动 → 断言「长期记忆」模块注入了上一会话抽出的记忆（验证：同进程内 submit→close 后第二次 build，断言 system 含新记忆）
- [ ] 🌐👁 **场景 26（真 provider 抽取一次）**：配真实 API key，真实聊一轮 → 后台真 provider 抽取出四类笔记、落带 frontmatter 的 `.md` + 更新 INDEX、再抽等价信息不重复追加（验收时执行一次，记录落盘文件与 INDEX 证据）
- [ ] 🌐👁 **场景 27（真终端跨会话体感·越用越懂你）**：真实终端第一会话告诉文天一个偏好/纠正 → `/exit` → 起第二会话 → 文天在新会话里据该记忆调整行为（before/after 体感对比），且 `--continue` 跨较长时间恢复时见一次性时间跨度提醒（验收时人工对比，记录证据）
