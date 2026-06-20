"""v0.10 · C85 · F70/F72（任务 T108）— 命令规格：CommandType 枚举 + CommandSpec 数据类。

分层铁律：纯叶子模块，仅 stdlib（enum / dataclasses / collections.abc）。
handler 的 CommandContext 参数类型用 TYPE_CHECKING 前向引用，避免与
context.py 形成循环依赖。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wentian.commands.context import CommandContext

__all__ = ["CommandType", "CommandSpec"]


class CommandType(Enum):
    """命令执行语义分类。"""

    LOCAL = "local"
    """纯本地：跑完即返回、不扩展对话历史。"""

    UI_STATE = "ui_state"
    """影响界面 / 会话状态（如切换权限模式、清屏）。"""

    PROMPT = "prompt"
    """把预设提示词送进对话，交给 AI 跑一轮。"""


@dataclass(frozen=True)
class CommandSpec:
    """单条斜杠命令的静态规格描述，构造后不可变。

    Parameters
    ----------
    name:
        规范名，无斜杠、小写（如 ``"help"``、``"session"``）。
    summary:
        /help 列表里的一行说明。
    usage:
        用法示例字符串（如 ``"/session resume <id>"``）。
    type:
        执行语义分类，见 :class:`CommandType`。
    handler:
        命令处理函数，签名 ``(ctx: CommandContext, args: str) -> bool | None``。
        返回真值时 REPL 退出；返回 ``None`` / ``False`` 则继续。
    aliases:
        可选别名 tuple（如 ``("quit", "q")``）。
    arg_hint:
        补全提示字符串（如 ``"<session-id>"``）；空串表示无参数。
    hidden:
        ``True`` 时不出现在 /help 列表；默认 ``False``。
    """

    name: str
    summary: str
    usage: str
    type: CommandType
    handler: Callable[[CommandContext, str], bool | None]
    aliases: tuple[str, ...] = field(default_factory=tuple)
    arg_hint: str = ""
    hidden: bool = False
