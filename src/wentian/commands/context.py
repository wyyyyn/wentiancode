"""v0.10 · C87 · F73/N34（任务 T111）— 命令上下文协议（CommandContext）。

REPL 实现此协议后，commands/ 包的各命令 handler 便可通过协议接口
与界面/会话层交互，而无需直接依赖 REPL 具体类。

分层铁律：纯叶子模块；Mode / CommandSpec 均用 TYPE_CHECKING 前向引用，
运行时零依赖（stdlib only）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, runtime_checkable

from typing import Protocol

if TYPE_CHECKING:
    from wentian.commands.spec import CommandSpec
    from wentian.permissions.decision import Mode

__all__ = ["CommandContext"]


@runtime_checkable
class CommandContext(Protocol):
    """命令 handler 与 REPL 之间的界面控制协议。

    实现此协议的对象由 REPL 在调用命令时注入；commands/ 包内的所有
    handler 只依赖此协议，不直接 import REPL / agent / provider。

    ``@runtime_checkable`` 允许在测试里用 ``isinstance(obj, CommandContext)``
    做结构化检查（duck-typing style）。
    """

    def print(self, renderable: object) -> None:
        """在终端输出 *renderable*（字符串或 Rich Renderable）。"""
        ...

    def send_user_message(self, text: str) -> None:
        """把 *text* 作为用户消息送入对话，触发一轮 AI 响应。

        主要供 PROMPT 类命令使用。
        """
        ...

    def get_mode(self) -> Mode:
        """返回当前权限模式。"""
        ...

    def set_mode(self, mode: Mode) -> None:
        """切换权限模式。"""
        ...

    def token_usage(self) -> object | None:
        """返回上一轮的 token 用量快照；无历史时返回 ``None``。"""
        ...

    def status_line(self) -> str:
        """返回当前状态栏文本（供 /status 类命令展示）。"""
        ...

    def memory_summary(self) -> str:
        """返回长期记忆目录及各域 INDEX 摘要（只读字符串）。"""
        ...

    def visible_commands(self) -> list[CommandSpec]:
        """返回当前可见的命令列表（用于 /help 渲染）。"""
        ...

    def clear_context(self) -> None:
        """清空当前会话的 messages，保留同一会话 id。"""
        ...

    def new_session(self) -> None:
        """创建并切换到新会话。"""
        ...

    def list_sessions(self, *, all_projects: bool) -> None:
        """列出会话历史。

        Parameters
        ----------
        all_projects:
            ``True`` 时跨项目列出所有会话；``False`` 时仅当前项目。
        """
        ...

    def resume_session(self, sid: str) -> None:
        """按会话 id 恢复历史会话。"""
        ...

    def switch_provider(self, name: str) -> None:
        """按名称切换 AI 提供商。"""
        ...

    def compact_now(self) -> str:
        """触发重量上下文压缩，返回可读的压缩汇报字符串。"""
        ...
