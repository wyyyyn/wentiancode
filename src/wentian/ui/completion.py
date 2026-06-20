"""v0.10 · C90 · F75（任务 T113）— 斜杠命令 Tab 补全器。

``CommandCompleter`` 继承 ``prompt_toolkit.completion.Completer``，
为以 ``/`` 开头的输入提供命令名候选，显示规范名（``/cmd``）及命令摘要。

分层铁律：
- 住 ui 层，可 import prompt_toolkit。
- 通过**鸭子** registry（只调 ``.completions``）取候选——
  ``commands/`` 包绝不依赖本文件（方向 ui→commands，不反向）。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

__all__ = ["CommandCompleter"]


class CommandCompleter(Completer):
    """斜杠命令补全器。

    仅对以 ``/`` 开头、且尚未键入空格（即仍处于命令名录入阶段）的输入产生候选。
    每个候选项：

    - ``text``           — 规范名（无斜杠，用于替换光标前的前缀）
    - ``start_position`` — ``-len(prefix)``（替换已键入的前缀字符）
    - ``display``        — ``/name``（含斜杠，用于菜单显示）
    - ``display_meta``   — 命令 summary（一行描述）

    Parameters
    ----------
    registry:
        鸭子类型的命令注册表，需提供：

        - ``.completions(prefix: str) -> list[CommandSpec]``
          返回可见命令中规范名以 *prefix* 开头的 CommandSpec 列表（按注册顺序）。
    """

    def __init__(self, registry: Any) -> None:
        self._registry = registry

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent | None,
    ) -> Iterator[Completion]:
        """生成补全候选。

        逻辑：
        1. 取光标前文本（``document.text_before_cursor``）。
        2. 若不以 ``/`` 开头 → 不补（return）。
        3. 若含空格 → 不补（用户已在键入参数）。
        4. 取前缀 = ``text[1:].lower()``；向 registry 查 CommandSpec 列表。
        5. 对每个 spec，直接取 name/summary，yield ``Completion``。
        """
        text = document.text_before_cursor

        # 条件 1：不以 "/" 开头
        if not text.startswith("/"):
            return

        # 条件 2：含空格（已在键入参数）
        if " " in text:
            return

        prefix = text[1:].lower()  # 去掉 "/" 并小写化

        for spec in self._registry.completions(prefix):
            yield Completion(
                text=spec.name,
                start_position=-len(prefix),
                display=f"/{spec.name}",
                display_meta=spec.summary,
            )
