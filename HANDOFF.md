# WentianCode 会话交接（HANDOFF）

> 新 session 接手须知：先读本文件，再读 `spec/spec.md`，即可无缝继续。

最后更新：2026-06-10

---

## 0. 一句话现状

**v0.1 开发完成，离线验收全绿，终审 READY TO MERGE。** 代码在 `feature/v0.1-core` 分支（20 commits），等两件事：① 用户提供真实 API key 跑 `spec/checklist.md` 的 🌐 联网项；② 合并决策（merge 到 main / 继续留分支）。

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
3. **v0.2 候选**（终审/评审留下的，spec 未要求）：
   - resume/continue 时恢复 `session.provider`（现在总是用 default/flag，会话存的 provider 是 write-only）
   - usage（token 数）展示——provider 已解析，render 弃用中
   - system 人格接线（机制已通，cli.py `system=None` 占位；人格文件 `~/.claude/soul.md`）
   - Live 长内容 vertical_overflow 体验优化

## 4. 关键技术事实（开发中确认，勿凭记忆推翻）

- model id 精确 `claude-opus-4-8`；thinking 必须 `{"type":"adaptive","display":"summarized"}`（缺 display 思考文本为空）；Opus 4.8 禁发 temperature/top_p/top_k/budget_tokens（400）。
- anthropic 用 `client.messages.stream()` 上下文管理器；openai 兼容用 `chat.completions.create(stream=True)` + `with resp:`，`reasoning_content` getattr 容错；真实流的 usage 常在尾部空 choices chunk。
- 渲染：TTY 下 Live(transient) 逐 delta 重渲 + 结束定格打印；thinking 与 Live 交错时 stop-print-reopen；非 TTY 只定格（测试路径）。
- REPL 错误回滚依赖 provider.stream 是生成器（异常在 render 迭代中浮出，assistant 消息不可能半 append）。

## 5. 工作流（沿用）

mew-spec：任何改动先改 `spec/` 再动代码；TDD 红绿重构；完成前验证铁律。
