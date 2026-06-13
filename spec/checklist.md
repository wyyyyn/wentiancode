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
