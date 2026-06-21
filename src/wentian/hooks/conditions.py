"""v0.12 · C95 · F80（任务 T119）— 条件求值叶子。

对外接口：evaluate(condition, context) -> bool

分层铁律（N41）：叶子——仅 import wentian.textmatch + wentian.hooks.spec（+ stdlib）。
零 rich / prompt_toolkit / backend SDK / agent / repl / providers / tools import。
"""

from __future__ import annotations

from wentian.hooks.spec import Condition, Match
from wentian.textmatch import match_one

__all__ = ["evaluate"]


def evaluate(condition: Condition | None, context: dict) -> bool:  # type: ignore[type-arg]
    """对 condition 求值，返回是否命中。

    规则：
    - ``condition is None`` 或 ``condition.clauses`` 为空 → ``True``（无条件恒触发）。
    - 否则对每个 ``Clause`` 取 ``context.get(field, "")``（缺失字段按空串），
      与 ``pattern`` 调用 ``match_one`` 判定；
      按 ``condition.match`` 归约：
        - ``Match.ALL`` → 全部子句命中才 ``True``
        - ``Match.ANY`` → 任一子句命中即 ``True``
    """
    if condition is None or not condition.clauses:
        return True

    results = (
        match_one(clause.pattern, str(context.get(clause.field, "")))
        for clause in condition.clauses
    )

    if condition.match is Match.ALL:
        return all(results)
    else:  # Match.ANY
        return any(results)
