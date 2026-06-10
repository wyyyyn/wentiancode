# WentianCode 会话交接（HANDOFF）

> 新 session 接手须知：先读本文件，再读 `spec/spec.md`，即可无缝继续。本文件记录截至上次会话的**全部已定决策与进度**，不依赖对话历史。

最后更新：2026-06-10

---

## 0. 一句话现状

WentianCode v0.1 正在按 **mew-spec（× superpowers 合并版）** 走 spec 驱动流程。
**当前卡在：阶段一 `spec.md` 已生成，等用户审批。** 用户一旦「通过」，进阶段二写 `plan.md`。

## 1. 项目是什么

- 名字：**WentianCode（文天）**，主命令 `wentian`，别名 `wt`，口头称呼“闻天/WT”。给 yuning 自己用的同伴型命令行 AI 助手（类 Claude Code）。
- 人格：复用 `~/.claude/soul.md`，和主对话同一个声音。
- v0.1 范围：终端**滚动式 REPL** 多轮**流式**对话内核。打通「多后端 + 流式 + extended thinking + 跨会话持久化」，**不做** tool use / 文件操作 / 代码编辑 / 全屏 TUI / Markdown 渲染 / 多模态。

## 2. 工作流（必须遵守）

- 用合并版技能 **mew-spec**：`~/.claude/skills/mew-spec/SKILL.md`（原版备份在其 `_originals/`）。
- 文档顺序：`spec.md`（做什么）→ `plan.md`（怎么做，含**测试策略**段）→ `task.md`（TDD 形态任务）→ `checklist.md`（行为验收）。**每份逐一审批后才进下一阶段。**
- 三条铁律：① 四文档没全过→不动代码；② 没有先失败的测试→不写生产代码（TDD 红-绿-重构）；③ 没有当场新鲜证据→不许声称“完成”。
- 阶段零：动代码前先建隔离工作区（`superpowers:using-git-worktrees` 或至少开分支），不在 main 上裸开发。
- 执行：有子代理→`superpowers:subagent-driven-development`（implementer + spec 合规评审 + 代码质量评审）；无→`superpowers:executing-plans`。
- 验收：`superpowers:verification-before-completion` → 跑 checklist 取证 → `superpowers:requesting-code-review` → `superpowers:finishing-a-development-branch`。

## 3. 已锁定的技术决策（brainstorming 阶段定的）

| 项 | 决定 |
|----|------|
| 语言 | Python |
| 界面 | 滚动式 REPL + Rich（非全屏 TUI）|
| 通信 | 官方 SDK：`anthropic`、`openai`（不手写 HTTP/SSE）|
| 协议 | 只两种：`anthropic` / `openai`；国产模型走 openai 兼容 + 改 base_url |
| 配置 | YAML，多 provider 列表 + `default`；每个 provider 四字段 `protocol/model/base_url/api_key`，Anthropic 可选 `thinking: true` |
| 默认模型 | `claude-opus-4-8`，不擅自降级 |
| extended thinking | 即 adaptive thinking：`thinking={"type":"adaptive","display":"summarized"}`；旧 `budget_tokens` 已废弃不可用 |
| 思考展示 | thinking_delta 用暗色斜体（前缀“🤔 思考中…”），text_delta 逐字 print |
| 会话记忆 | 跨会话持久化；默认开新会话，`--continue` 续上次，`--resume <id>` 指定 |
| 路径 | XDG：配置 `~/.config/wentian/config.yaml`，会话 `~/.local/share/wentian/sessions/<id>.json` |
| 依赖管理 | `uv` |
| 命令行库 | `typer`；渲染 `rich`；YAML `pyyaml` |
| 斜杠命令 | `/help` `/new` `/sessions` `/resume <id>` `/provider <名>` `/exit` |

## 4. 计划中的架构（待 plan.md 正式确认，仅供参考，勿当定论）

- 包布局 `src/wentian/`：`cli.py`（入口/装配/启动）、`config.py`（读 YAML+校验+选 provider）、`providers/`（`base.py` 抽象接口 + 统一事件、`anthropic.py`、`openai_compat.py`、`factory.py`）、`session.py`（消息+持久化）、`repl.py`（REPL+Rich+流式）。`tests/`。
- 统一流式事件（草案）：`ThinkingDelta(text)` / `TextDelta(text)` / `Done(usage)`；接口 `Provider.stream(messages, *, system) -> Iterator[StreamEvent]`。
- anthropic.py 用 `client.messages.stream()` 映射 thinking_delta/text_delta；openai_compat.py 用 `chat.completions.create(stream=True)`，`delta.content`→TextDelta，`delta.reasoning_content`→ThinkingDelta。
- 测试：FakeProvider 吐预设事件流，离线测 REPL/会话/配置。

## 5. 已产出文件

- `spec/spec.md` —— 阶段一正式文档（11 F + 5 N + 6 不做 + 9 AC），**待审批**。
- `spec/README.md` —— spec 目录索引 + 进度表。
- `HANDOFF.md` —— 本文件。

## 6. 下一步（new session 该做的）

1. 读 `spec/spec.md`，向用户复述要点，走 mew-spec 阶段一**审批门**（问：功能完整？边界遗漏？不做的事合理？验收可观测？）。
2. 用户「通过」后 → 阶段二写 `plan.md`（带测试策略段，逐段确认）。
3. 依次 task.md → checklist.md → 阶段零隔离 → 阶段五 TDD 开发 → 阶段六验收。
4. 全程参考 `claude-api` skill 确认 Anthropic SDK 细节（model id、adaptive thinking、streaming）。
5. 收尾按全局 CLAUDE.md：每完成一个功能点提交（commit 不带 Co-Authored-By）；报告类产物才归档 report-hub（本项目是代码,不归档）。

## 7. 待办/未决

- spec.md 尚未获批（最高优先）。
- 用户可能对「不做 Markdown 渲染」「续会话策略」等取舍有调整。
