"""v0.10 · C88 · F71（任务 T109）— 斜杠命令行解析器。

分层铁律：纯叶子模块，仅 stdlib（dataclasses）。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ParsedCommand", "parse"]


@dataclass(frozen=True)
class ParsedCommand:
    """解析结果——规范化命令名与原始参数串。

    Parameters
    ----------
    name:
        规范名（小写），无斜杠。
    args:
        命令名之后的参数串（strip 后）；无参数时为空串。
    """

    name: str
    args: str


def parse(line: str) -> ParsedCommand | None:
    """解析一行以 ``/`` 开头的输入，返回 :class:`ParsedCommand` 或 ``None``。

    调用方保证 *line* 已 strip 且以 ``/`` 开头。
    裸斜杠（``/``）或斜杠后紧跟空白（``/   ``、``/ foo``）返回 ``None``。

    Parameters
    ----------
    line:
        已 strip 的用户输入行，如 ``"/help"`` 或 ``"/session resume abc"``。

    Returns
    -------
    ParsedCommand | None
        解析成功返回 ParsedCommand；命令名为空时返回 None。
    """
    body = line[1:]  # 去掉开头的 /
    head, _, rest = body.partition(" ")
    head = head.strip()
    if not head:
        # 裸斜杠 / 或 "/   " 或 "/ foo"（斜杠后紧接空格导致 head 为空）
        return None
    return ParsedCommand(name=head.lower(), args=rest.strip())
