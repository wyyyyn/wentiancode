---
name: test
description: 跑测试并报告结果
mode: shared
allowed_tools: [run_command, read_file]
---
# Skill: test

探测并运行本项目的测试套件，带证据报告通过/失败。$ARGUMENTS

## 步骤

1. **探测测试栈**：先看项目根有什么——
   - Python：`pyproject.toml` / `pytest.ini` / `tests/` ⇒ 多用 `uv run pytest -q`
     或 `pytest -q`（有 uv 优先 uv）。
   - Node：`package.json` 里的 `scripts.test` ⇒ `npm test` / `pnpm test` / `yarn test`。
   - 其他：`Makefile` 的 `test` 目标、`cargo test`、`go test ./...` 等。
   `read_file` 读配置确认正确的命令，不要凭猜。
2. **运行**：用探测到的命令跑全量测试（用户在 $ARGUMENTS 指定了范围/路径则只跑该范围）。
3. **报告结果**，附**当场新鲜证据**：
   - 通过：贴通过数 / 用时等汇总行（如 `1264 passed in 25s`）。
   - 失败：贴失败用例名 + 关键报错栈，定位到 `file:line`，简述失败原因；
     必要时给出最小修复方向。
   - 跑不起来（命令缺失/依赖未装）：如实说明卡在哪，不谎报通过。

## 红线

- **没有当场跑出的证据，不声称「测试通过」**（完成前验证铁律）。
