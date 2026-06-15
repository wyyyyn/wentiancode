"""v0.5 · C21/C22（任务 T59/T60）— 系统提示组装与动态提醒拼装包。

子模块（均为纯函数、零后端 SDK / 零 rich / 零 prompt_toolkit 依赖）：

- ``system``    — 七固定模块 + 可选槽位的有序拼装：``build_system_prompt``。
- ``reminders`` — 环境信息与会话开关的 ``<system-reminder>`` 注入：
  ``EnvInfo`` / ``build_request_decorator``（请求时拼装、永不持久化）。

包内不在 ``__init__`` 重导出，调用方按全路径 import（如
``from wentian.prompt.system import build_system_prompt``）——避免并行开发期
对本文件的写入竞争，也让依赖关系在导入处一目了然。
"""

from __future__ import annotations

__all__: list[str] = []
