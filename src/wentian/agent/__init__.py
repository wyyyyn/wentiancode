"""v0.4 · Agent Loop 包（任务 T47 起）。

asyncio 多轮 Agent 循环：事件契约（events）、流桥接、轮次执行、循环调度等
模块陆续落在本包内。

本包不做任何 re-export——上层一律从具体子模块（如 ``wentian.agent.events``）
直接 import，避免后续并行任务改动 ``__init__`` 时互相冲突。
"""
