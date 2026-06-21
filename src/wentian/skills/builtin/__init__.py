"""v0.11 · C107a · F73（任务 T134a）— 打包内置 Skill 层。

本包仅作为打包资源容器：``*.md`` 内置 Skill 文件随 wheel 一并分发，由
``wentian.skills.loader._packaged_builtin_dir`` 经 ``importlib.resources`` 读出。
不含任何可执行逻辑。
"""
