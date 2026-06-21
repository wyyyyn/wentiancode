---
name: commit
description: 暂存改动并写一条 conventional commit
mode: shared
allowed_tools: [run_command, read_file]
---
# Skill: commit

把当前工作区里相关的改动暂存，并写一条规范的 conventional commit。$ARGUMENTS

## 步骤

1. **看清现状**：先 `git status` 看哪些文件改了、`git diff --staged` 看已暂存内容；
   未暂存但属于本次改动的，用 `git add <相关文件>` 暂存进来。只暂存与本次主题相关的
   文件，不要一把 `git add -A` 把无关改动混进同一个 commit。
2. **判断主题**：从 diff 归纳出这次改动的单一主题（feat / fix / refactor / docs /
   test / chore 等）。一个 commit 只表达一件事；若 diff 跨多个主题，提醒用户拆分。
3. **写一条 commit 信息**：用 conventional commit 格式
   `type(scope): 简述`，语言跟随仓库 CLAUDE.md 约定（本项目要求中文则用中文）。
   简述用祈使句、不加句号；必要时空一行后补正文说明动机/影响。
4. **提交**：`git commit -m "<信息>"`。

## 红线

- **绝不加 `Co-Authored-By`**：commit message 里任何情况下都不附带
  `Co-Authored-By` 落款。
- **不主动 push**：提交后不要 `git push`，除非用户明确要求推送到远端。
- 遵循仓库根 `CLAUDE.md` 里的提交约定（提交语言、消息风格、是否分支等）；
  与本 SOP 冲突时以仓库 CLAUDE.md 为准。
