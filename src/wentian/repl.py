"""REPL — main interactive loop for wentian.

Responsibilities:
- Read user input, dispatch slash commands or run one chat round.
- Maintain conversation history in the session; persist after each round.
- Roll back user message if the provider raises any exception (T10).
- All dependencies injected: provider, session, store, renderer, console,
  input_fn — fully testable offline.

No anthropic/openai/yaml imports here.
"""
from __future__ import annotations

from collections.abc import Callable

from rich.console import Console

from wentian.config import ConfigError
from wentian.providers.base import Message, Provider
from wentian.render import Renderer
from wentian.session import Session, SessionStore

__all__ = ["REPL"]

_PROMPT = "文天> "

_HELP_TEXT = """\
Available commands:
  /help               — show this message
  /new                — start a new session
  /sessions           — list saved sessions
  /resume <id>        — resume a session by id
  /provider <name>    — switch provider
  /exit               — quit
"""


class REPL:
    """Interactive REPL.

    Parameters
    ----------
    provider:
        Active LLM backend (replaceable via /provider).
    session:
        Active conversation session.
    store:
        SessionStore for persistence.
    renderer:
        Renderer for displaying stream events.
    provider_factory:
        Callable(name) -> Provider — called by /provider; may raise ConfigError.
    input_fn:
        Callable used to read a line of user input (default: builtins.input).
    system:
        Optional system prompt passed to provider.stream(). Default None.
    """

    def __init__(
        self,
        provider: Provider,
        session: Session,
        store: SessionStore,
        renderer: Renderer,
        *,
        provider_factory: Callable[[str], Provider],
        input_fn: Callable[..., str] = input,
        system: str | None = None,
    ) -> None:
        self._provider = provider
        self._session = session
        self._store = store
        self._renderer = renderer
        self._provider_factory = provider_factory
        self._input_fn = input_fn
        self._system = system
        self._console: Console = renderer.console

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Enter the REPL loop; returns when the user types /exit or sends EOF."""
        while True:
            try:
                raw = self._input_fn(_PROMPT)
            except (EOFError, KeyboardInterrupt):
                self._console.print()
                return

            line = raw.strip()

            if not line:
                continue

            if line.startswith("/"):
                should_exit = self._dispatch_command(line)
                if should_exit:
                    return
            else:
                self._chat_once(line)

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    def _chat_once(self, user_text: str) -> None:
        """Run one conversation turn.

        Appends the user message, calls the provider, renders the stream,
        appends the assistant message, and saves. On any exception from
        stream or render the user message is popped and nothing is saved.
        """
        user_msg: Message = {"role": "user", "content": user_text}
        self._session.messages.append(user_msg)

        try:
            events = self._provider.stream(
                self._session.messages, system=self._system
            )
            body = self._renderer.render_stream(events)
        except Exception as exc:
            # Roll back the user message; don't save; print one-line error.
            self._session.messages.pop()
            self._console.print(f"[red]错误：{exc}[/red]")
            return

        assistant_msg: Message = {"role": "assistant", "content": body}
        self._session.messages.append(assistant_msg)
        self._store.save(self._session)

    # ------------------------------------------------------------------
    # Slash command dispatch
    # ------------------------------------------------------------------

    def _dispatch_command(self, line: str) -> bool:
        """Parse and execute a slash command.

        Returns True if the REPL should exit, False otherwise.
        """
        parts = line.split(maxsplit=1)
        cmd = parts[0]
        args = parts[1] if len(parts) > 1 else ""

        handlers: dict[str, Callable[[str], bool | None]] = {
            "/help": self._cmd_help,
            "/new": self._cmd_new,
            "/sessions": self._cmd_sessions,
            "/resume": self._cmd_resume,
            "/provider": self._cmd_provider,
            "/exit": self._cmd_exit,
        }

        handler = handlers.get(cmd)
        if handler is None:
            self._console.print(f"[yellow]未知命令：{cmd}  输入 /help 查看帮助[/yellow]")
            return False

        result = handler(args)
        return bool(result)

    # ------------------------------------------------------------------
    # Command handlers — all take args: str, return truthy to exit
    # ------------------------------------------------------------------

    def _cmd_help(self, args: str) -> None:
        self._console.print(_HELP_TEXT)

    def _cmd_new(self, args: str) -> None:
        self._session = self._store.create(provider=self._provider.name)
        self._console.print(f"[green]新会话已创建：{self._session.id}[/green]")

    def _cmd_sessions(self, args: str) -> None:
        sessions = self._store.list()
        if not sessions:
            self._console.print("[dim]暂无保存的会话[/dim]")
            return
        for sid, updated_at, summary in sessions:
            preview = f"  {summary[:40]}" if summary else ""
            self._console.print(f"  {sid}  {updated_at}{preview}")

    def _cmd_resume(self, args: str) -> None:
        sid = args.strip()
        if not sid:
            self._console.print("[yellow]用法：/resume <id>[/yellow]")
            return
        try:
            self._session = self._store.load(sid)
            self._console.print(f"[green]已恢复会话：{sid}[/green]")
        except FileNotFoundError:
            self._console.print(f"[red]找不到会话：{sid}[/red]")

    def _cmd_provider(self, args: str) -> None:
        name = args.strip()
        if not name:
            self._console.print("[yellow]用法：/provider <名称>[/yellow]")
            return
        try:
            new_provider = self._provider_factory(name)
            self._provider = new_provider
            self._session.provider = new_provider.name
            self._store.save(self._session)
            self._console.print(f"[green]已切换 provider：{name}[/green]")
        except Exception as exc:
            self._console.print(f"[red]切换 provider 失败：{exc}[/red]")

    def _cmd_exit(self, args: str) -> bool:
        return True

    # ------------------------------------------------------------------
    # v0.2 · C2 · F16（任务 T17）— bottom toolbar 状态行数据源
    # ------------------------------------------------------------------

    def status_line(self) -> str:
        """v0.2 · C2 · F16（任务 T17）— bottom toolbar 状态行数据源。

        Format: ``{provider.name}:{provider.model} │ 会话 {session.id} │ {n} 条消息``
        If the provider's model is empty/missing the ``:{model}`` part is omitted.
        Reads live self._provider / self._session so /provider, /new, /resume
        are automatically reflected without any extra wiring.
        """
        model = getattr(self._provider, "model", "")
        if model:
            backend = f"{self._provider.name}:{model}"
        else:
            backend = self._provider.name
        n = len(self._session.messages)
        return f"{backend} │ 会话 {self._session.id} │ {n} 条消息"
