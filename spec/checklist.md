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
> **离线验收记录 2026-06-11**：T15–T25 + polish 全部完成，每任务过两阶段评审，终审 READY TO MERGE。离线项全部当场取证 ✅；🌐👁 项待用户真实终端验收。

## 实现完整性

- [x] v0.2 包可用：`uv run pytest -q` → **225 passed**（2026-06-11 现场）；`wentian --help` 正常；`__version__ == "0.2.0"`（冒烟测试断言）
- [ ] （AC11/F13）启动横幅（T27 改版）：👁 真实终端启动，第一屏可见**彩色像素小人**（朱砂方巾小人）+ 右侧三行信息（名称版本 / `provider · model` / 会话状态），无边框面板；`--continue` 启动时显示「已恢复」与会话 id
- [ ] （AC12/F14）后端选择：👁 配置 ≥2 provider 不带 `-p` 启动 → 出现列表，↓ 移动高亮、回车后用所选后端对话（🌐 验证一轮）；带 `-p sf` 启动 → 不出列表直接进入
- [ ] （AC13/F15）多行输入：👁 Ctrl+J（或 Alt+Enter）产生第二行，回车一次提交（🌐 模型确认收到含换行的完整内容）；↑ 翻出上一条；**重启程序后** ↑ 仍翻出上次运行的历史
- [ ] （AC14/F16）状态栏：👁 启动后可见后端/模型/会话 id/消息数；`/provider sf-r1` 后状态栏后端变化；一轮对话后消息数 +2
- [ ] （AC15/F17）计时反馈：🌐👁 提一个长问题——等待期可见动画符与递增秒数；正文流式期间秒数继续；定格后计时消失
- [ ] （AC16/F18）中断：🌐👁 流式中按 Esc → 立即停、屏留部分正文 + 「已中断」、回到输入框；追问「你刚才说到哪了」→ 助手能引用被中断内容（partial 入史证据）；首字前按 Esc → 直接回输入框，`/sessions` 或会话文件确认无该轮提问；流式中 Ctrl+C 行为同 Esc 且程序不退出

## 退化与兼容

- [x] 非 TTY 管道行为与 v0.1 等价：实测 `printf '/exit\n' | wentian` 横幅照印、退出码 0、无 traceback；选择器/输入框/监听器均未装配（注入点测试锁死）
- [ ] 中断后终端不脏：👁 Esc 中断后能立即正常输入下一条（termios 已还原、无残键漏入）；方向键在流式期间乱按不误触发中断
- [x] v0.1 全部既有行为不回退：v0.1 全部 148 测试在 v0.2 代码上保持绿（含命令/回滚/续会话）

## 编译与测试

- [x] 无 API key 环境 `uv run pytest -q` 全绿（225 passed，2026-06-11 现场）
- [x] repl/render/session 仍零 SDK import；repl/render 零 prompt_toolkit import（import 检查：加载三模块后 sys.modules 无 prompt_toolkit，2026-06-11 现场）

## 端到端场景

- [ ] 🌐👁 **场景 3（v0.2 主干）**：双 provider 配置启动 → 选择列表选 sf → 横幅正确 → 多行输入一个长问题（含换行）→ 等待秒数跳动 → 流式中按 Esc 中断 → 「已中断」→ 追问引用成功 → `/provider sf-r1` 状态栏更新 → 再来一轮完整回答 → `/exit` → `--continue` 重启横幅示「已恢复」且 ↑ 翻出历史输入
