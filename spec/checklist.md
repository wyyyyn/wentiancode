# WentianCode（文天）v0.1 Checklist

> 每项通过运行代码或观察行为验证，聚焦系统行为，与实现解耦。离线项全部可自动化；标 🌐 的项需要真实 API key 联网人工跑（验收时执行一次并记录证据）。

## 实现完整性

- [ ] 包可导入、入口可用（验证：`uv run python -c "import wentian"` 无错；`uv run wentian --help` 与 `uv run wt --help` 输出帮助，含 `--provider` `--continue` `--resume` 说明）
- [ ] （AC1）启动进入 REPL：🌐 `uv run wentian` 后看到提示符；输入一个问题回车，程序发起请求并开始输出
- [ ] （AC2/F3）流式可观测：🌐 提一个需较长回答的问题，回复逐块出现而非停顿后整段弹出
- [ ] （AC5/F8）thinking 可区分：🌐 用配置了 `thinking: true` 的 Claude 后端提一个推理题，先看到 `🤔 思考中…` + 暗色斜体思考文本，再看到正文；两者视觉可区分
- [ ] （AC10/F12）Markdown 渲染：🌐 让模型"用一个标题、一个列表和一段 python 代码块回答"，最终终端显示为渲染后格式（代码块有边框样式、列表有圆点、标题有强调），看不到裸 ```` ``` ```` 围栏；生成过程中内容逐步出现
- [ ] （AC3/F4）上下文记忆：🌐 第一轮问任意问题，第二轮问"我刚才问的是什么"，回答正确引用第一轮内容

## 多后端与配置

- [ ] （AC4/F5/F6）双协议同配置可用：🌐 同一份 `config.yaml` 配 Anthropic + 一个 OpenAI 兼容后端（如 DeepSeek），分别对话各成功一轮
- [ ] （AC4/F7）启动覆盖默认：🌐 `uv run wentian --provider <非默认名>` 后对话走该后端（可由回答中自报模型名或思考字段差异佐证）
- [ ] 配置错误友好报错：把 config.yaml 的 `default` 改成不存在的名启动 → 一行人类可读错误 + 非 0 退出码，无 traceback（验证：离线即可跑）

## 会话持久化

- [ ] （AC6/F9 续上次）：🌐 对话一轮后 `/exit`；`uv run wentian --continue` 启动后 `/sessions` 或直接追问"我刚问了什么"，能接续之前历史
- [ ] （AC6 新会话）：`uv run wentian`（无 --continue）启动后追问历史 → 模型不知道，证明是空白新会话；且 `~/.local/share/wentian/sessions/` 下旧文件仍在
- [ ] 会话文件落地正确：对话一轮后检查 `~/.local/share/wentian/sessions/<id>.json` 存在，JSON 含 user+assistant 两条消息（验证：`cat` + 肉眼或 `python -m json.tool`）

## 会话内命令（AC7/F10）

- [ ] `/help` 列出全部六条命令
- [ ] `/new` 后追问旧话题 → 模型不知道（新空会话）
- [ ] `/sessions` 列出历史会话 id；`/resume <其中一个id>` 后追问该会话内容 → 能接上
- [ ] `/provider <名>` 切换后端后能继续对话；`/provider 不存在的名` → 报错不退出
- [ ] `/exit` 干净退出，无 traceback

## 扩展性（AC8/F11）

- [ ] 假后端即插即用：测试套件中 FakeProvider 仅实现 `Provider` 统一接口即被 REPL/渲染/会话层完整驱动，未改动这三层任何代码（验证：`uv run pytest tests/test_repl.py tests/test_render.py -q` 全绿，且 grep 确认 repl.py/render.py/session.py 不 import anthropic/openai）

## 编译与测试（AC9/N5）

- [ ] 全部单测离线通过：断网或不设任何 API key 环境下 `uv run pytest -q` → 0 failed
- [ ] 测试覆盖四类核心流程：配置解析（test_config）、后端选择（test_factory/test_cli）、会话存读（test_session）、界面循环（test_repl）各文件存在且非空

## 端到端场景

- [ ] 🌐 **场景 1（主干全链路）**：删除/清空配置外的会话目录 → `uv run wentian` → 问"用列表介绍你自己" → 流式渲染出 Markdown 列表 → 追问"我刚问了什么"答对 → `/provider <openai兼容名>` 切换再问一轮成功 → `/exit` → `uv run wentian --continue` → 历史可接续。全程无崩溃。
- [ ] 🌐 **场景 2（边界：错误恢复）**：把当前 provider 的 api_key 改错启动并提问 → 一行错误提示、REPL 不退出 → `/provider <正常后端>` → 再提问成功；`/exit` 后检查会话文件中**不含**那条失败轮的孤儿 user 消息。
