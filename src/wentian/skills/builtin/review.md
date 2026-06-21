---
name: review
description: 评审未提交的改动
mode: shared
allowed_tools: [run_command, read_file, search_text, find_files]
---
# Skill: review

评审当前工作区里尚未提交的改动，按严重度报告问题。$ARGUMENTS

## 步骤

1. **取出 diff**：`git status` 看改了哪些文件，`git diff`（含未暂存）和
   `git diff --staged`（已暂存）一起看全本次改动。必要时 `read_file` 读改动文件的
   上下文、`search_text` / `find_files` 查相关调用点与依赖。
2. **逐项审查**，重点关注：
   - **Bug / 正确性**：逻辑错误、边界条件、空值/异常处理、并发与资源泄漏。
   - **安全 / 风险**：注入、越权、密钥硬编码、危险的删除/覆盖/外发操作。
   - **风格 / 可维护性**：命名、重复、坏味道、与周边代码风格不一致、缺测试。
3. **按严重度分级报告**：分 `Blocker` / `Major` / `Minor` / `Nit` 四档，每条给出
   `file:line` 定位 + 一句话问题 + 建议修法。没问题的部分也明说「这块没问题」。

## 约定

- 只评审、不改代码（除非用户另外要求）。
- 实事求是：拿不准的标「待确认」，不编造不存在的问题、也不放过真问题。
