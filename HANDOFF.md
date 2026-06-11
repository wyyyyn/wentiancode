# WentianCode 会话交接（HANDOFF）

> 新 session 接手须知：先读本文件，再读 `spec/spec.md`，即可无缝继续。

最后更新：2026-06-11

---

## 0. 一句话现状

**v0.2（Claude Code 式交互）开发完成，离线验收全绿（225 测试），终审 READY TO MERGE。** 代码在 `feature/v0.2-ui` 分支（基于 feature/v0.1-core，17+ commits）。等：① 用户真实终端跑 `spec/checklist.md` v0.2 的 🌐👁 项（横幅/选择器/多行输入/状态栏/计时/Esc 中断）；② 合并决策（v0.1-core + v0.2-ui → main）。全局命令已重装至 0.2.0（`uv tool install --editable . --reinstall` 已执行，`wentian`/`wt` 可直接用）。

**v0.2 新增**：F13 横幅（ui/banner.py）、F14 后端选择器（ui/select.py）、F15 多行输入框+跨重启历史（ui/input.py）、F16 状态栏（repl.status_line + bottom_toolbar）、F17 等待/流式计时（ui/spinner.py + render Group 合成）、F18 Esc/Ctrl+C 中断（ui/interrupt.py EscListener + render._StreamPump 泵线程；partial 入史/零正文回滚）。教学隔离规约：一任务一提交 `[T#/C#/F#]`、一组件一文件、docstring 标记——见 spec/task.md 末尾。

**v0.2 关键技术事实**：rich Live(get_renderable=) 由内部刷新线程驱动秒数（主线程阻塞也跳）；双 Live 必先 stop 再 open（孤儿 Live 会劫持 stdout——spinner.start 已自带 guard）；prompt_toolkit 测试用 create_pipe_input+DummyOutput；裸 Esc 与方向键转义序列靠 50ms 二次 select 区分（\x1b\x1b 同窗不触发，已知取舍）；Darwin tcsetattr 复原 ICANON 会瞬时置 PENDIN（测试需 mask）；NullListener.__enter__ 返回 None → render 走直接迭代路径（保测试确定性）。

## 1. 项目是什么

- **WentianCode（文天）**，命令 `wentian` / `wt`。yuning 自己的同伴型命令行 AI 助手（类 Claude Code）。
- v0.1 范围：终端滚动式 REPL 多轮流式对话内核（多后端 + 流式 + thinking + Markdown 渲染 + 跨会话持久化）。不做 tool use / 文件操作 / 全屏 TUI / 多模态。

## 2. 已完成（全部四文档获批 → TDD 开发 → 双阶段评审 → 终审）

- `spec/` 四文档全部获批（2026-06-10），含后补的 F12 Markdown 渲染 + AC10。
- 实现：`src/wentian/`（base / config / factory / session / render / repl / cli / anthropic / openai_compat），148 个测试全离线通过。
- 每个任务都走了 implementer → spec 合规评审 → 代码质量评审，发现并修复的真问题包括：T7 Live 流式未实装（spy 测试锁死）、thinking/Live 撞行、usage None 崩溃、openai 尾部 usage chunk 未测等。
- 最终整体评审：READY TO MERGE，F1-F12 全覆盖。
- 验证证据（2026-06-10 现场）：无 API key 环境 `uv run pytest -q` → 148 passed；`wentian --help`/`wt --help` OK；repl/render/session 零 SDK import；git log 无 Co-Authored-By。

## 3. 待办（下一步）

1. **联网验收**：用户配置 `~/.config/wentian/config.yaml`（含真实 key）后跑 `spec/checklist.md` 标 🌐 的项（流式、thinking、Markdown、双后端、续会话、两个端到端场景）。
2. **合并决策**：merge `feature/v0.1-core` → main。
3. **v0.3 候选**（v0.2 终审留下的 + v0.1 遗留）：
   - 流式期间 type-ahead（现在 EscListener 丢弃非 Esc 按键，不能预输入下一问）
   - 输入框 Ctrl+C 二次确认退出（现在一次就退；Claude Code 是按两次）
   - Ctrl+C 微秒级竞态加固（listener __enter__ 与 render try 之间的 KeyboardInterrupt 会带 traceback 退出）
   - slash 命令自动补全菜单（spec 已明确留后）
   - resume/continue 时恢复 session.provider；usage（token 数）展示；system 人格接线（~/.claude/soul.md）

## 4. 关键技术事实（开发中确认，勿凭记忆推翻）

- model id 精确 `claude-opus-4-8`；thinking 必须 `{"type":"adaptive","display":"summarized"}`（缺 display 思考文本为空）；Opus 4.8 禁发 temperature/top_p/top_k/budget_tokens（400）。
- anthropic 用 `client.messages.stream()` 上下文管理器；openai 兼容用 `chat.completions.create(stream=True)` + `with resp:`，`reasoning_content` getattr 容错；真实流的 usage 常在尾部空 choices chunk。
- 渲染：TTY 下 Live(transient) 逐 delta 重渲 + 结束定格打印；thinking 与 Live 交错时 stop-print-reopen；非 TTY 只定格（测试路径）。
- REPL 错误回滚依赖 provider.stream 是生成器（异常在 render 迭代中浮出，assistant 消息不可能半 append）。

## 5. 工作流（沿用）

mew-spec：任何改动先改 `spec/` 再动代码；TDD 红绿重构；完成前验证铁律。
