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
from wentian.ui.interrupt import InterruptListener, NullListener

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
    interrupt_listener:
        v0.2 · C6 · F18（任务 T23）— InterruptListener entered around each
        chat round; the Event (or None) it yields is forwarded to
        ``render_stream(interrupt=...)``. Default None → NullListener
        (yields None → direct render path, v0.1 behavior preserved).
    registry:
        v0.3 · C12 · F23（任务 T42/T43）— optional ToolRegistry whose
        ``specs()`` is advertised to the provider. None → tools disabled,
        pure v0.2 behavior (the provider receives ``tools=None``).
    executor:
        v0.3 · C12 · F23（任务 T42/T43）— optional ToolExecutor used to run
        tool calls inside the single tool round. Required (paired with
        ``registry``) for tools to actually execute; None → tools disabled.
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
        interrupt_listener: InterruptListener | None = None,
        # Typed as object on purpose: repl stays decoupled from wentian.tools
        # (duck-typed at call sites; see plan.md C12 layering note).
        registry: object | None = None,
        executor: object | None = None,
    ) -> None:
        self._provider = provider
        self._session = session
        self._store = store
        self._renderer = renderer
        self._provider_factory = provider_factory
        self._input_fn = input_fn
        self._system = system
        # Default resolved here (not in the signature) to avoid a shared
        # mutable default instance across REPLs.
        self._interrupt_listener: InterruptListener = (
            interrupt_listener if interrupt_listener is not None else NullListener()
        )
        # v0.3 · C12 · F23（任务 T42/T43）— tools are enabled only when both a
        # registry and an executor are injected; either missing → v0.2 path.
        self._registry = registry
        self._executor = executor
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

        v0.2 · C5 · F17（任务 T21）改造：RenderResult — render_stream now
        returns a RenderResult; the assistant message uses ``result.text``.

        v0.2 · C6 · F18（任务 T23）— 中断语义 (AC16): the interrupt
        listener is entered around stream+render; the Event (or None) it
        yields is forwarded to ``render_stream(interrupt=...)``. On
        interrupt with partial text → partial enters history and is saved
        (the on-screen 「已中断」 marker is render-layer only, never part of
        the message content). On zero-text interrupt → the user message is
        rolled back and nothing is saved (历史无未答之问). Either way the
        REPL loop continues with the next input.

        Appends the user message, calls the provider, renders the stream,
        appends the assistant message, and saves. On any exception from
        stream or render the user message is popped and nothing is saved
        (the with-block guarantees the listener's __exit__ still runs).

        v0.3 · C12 · F23（任务 T42/T43）— single tool round: when a registry
        and executor are wired and round 1 returns tool calls (and was not
        interrupted), the calls are executed and a second round is issued.
        See :meth:`_run_tool_round`.
        """
        # v0.3 · C12 · F23（任务 T42/T43）— always pass tools= (None disables).
        tools = self._registry.specs() if self._registry else None

        user_msg: Message = {"role": "user", "content": user_text}
        self._session.messages.append(user_msg)

        try:
            with self._interrupt_listener as interrupt_event:
                events = self._provider.stream(
                    self._session.messages, system=self._system, tools=tools
                )
                result = self._renderer.render_stream(
                    events, interrupt=interrupt_event
                )
        except Exception as exc:
            # Roll back the user message; don't save; print one-line error.
            self._session.messages.pop()
            self._console.print(f"[red]错误：{exc}[/red]")
            return

        if result.interrupted and not result.text:
            # Zero-text interrupt: roll back the unanswered user message so
            # neither history nor disk keeps a question without an answer.
            self._session.messages.pop()
            return

        # v0.3 · C12 · F23（任务 T42/T43）— tool round only when tools are
        # enabled, round 1 produced tool calls, and it was NOT interrupted.
        # Interrupted rounds (even with partial text) discard tool_calls and
        # fall through to the v0.2 text-only path below (历史无未答之工具).
        if (
            self._executor is not None
            and result.tool_calls
            and not result.interrupted
        ):
            self._run_tool_round(result, tools)
            return

        assistant_msg: Message = {"role": "assistant", "content": result.text}
        self._session.messages.append(assistant_msg)
        self._store.save(self._session)

    # ------------------------------------------------------------------
    # v0.3 · C12 · F23（任务 T42/T43）— single tool round
    # ------------------------------------------------------------------

    def _run_tool_round(self, round1, tools) -> None:
        """v0.3 · C12 · F23（任务 T42/T43）— execute one tool round, then
        issue a single follow-up turn.

        Preconditions (checked by the caller): tools enabled, ``round1`` has
        tool_calls, and round 1 was not interrupted.

        Steps:

        1. Append the round-1 assistant message carrying ``round1.text`` plus
           the serialized tool_calls (unparseable ``arguments=None`` coerced
           to ``{}`` in stored history — contract: bad calls never persist),
           and ``raw_content`` when present.
        2. For each call: display it, execute it (confirmation lives inside
           the executor; the interrupt listener is NOT armed here), display
           the outcome, and append the tool result message. The executor
           still receives the original ``arguments`` (incl. None) so it can
           produce its own error result.
        3. Save — tool execution causes real side effects, persist before the
           second round so a crash can't lose them.
        4. Issue round 2 (same tools=, interrupt listener re-armed). Round 2
           text (if any) enters history; round-2 tool_calls are NEVER stored
           (an unanswered tool_use would 400 the next request) and trigger a
           single-round limitation notice. A round-2 exception leaves all the
           already-real messages in place, prints an error, saves, returns.
        """
        # 1. Round-1 assistant message with serialized tool calls.
        stored_calls = [
            {
                "id": call.id,
                "name": call.name,
                # Unparseable calls never persist with None args (contract).
                "arguments": call.arguments if call.arguments is not None else {},
            }
            for call in round1.tool_calls
        ]
        assistant_msg: Message = {
            "role": "assistant",
            "content": round1.text,
            "tool_calls": stored_calls,
        }
        if round1.raw_content:
            assistant_msg["raw_content"] = round1.raw_content
        self._session.messages.append(assistant_msg)

        # 2. Execute each call (executor gets the ORIGINAL arguments).
        for call in round1.tool_calls:
            self._renderer.render_tool_call(call)
            outcome = self._executor.execute(call.id, call.name, call.arguments)
            self._renderer.render_tool_result(outcome)
            tool_msg: Message = {
                "role": "tool",
                "tool_call_id": call.id,
                "content": outcome.content,
                "is_error": outcome.is_error,
            }
            self._session.messages.append(tool_msg)

        # 3. Persist real side effects before round 2.
        self._store.save(self._session)

        # 4. Round 2 — same tools=, interrupt listener re-armed.
        try:
            with self._interrupt_listener as interrupt_event:
                events = self._provider.stream(
                    self._session.messages, system=self._system, tools=tools
                )
                round2 = self._renderer.render_stream(
                    events, interrupt=interrupt_event
                )
        except Exception as exc:
            # Tool messages are already real — do NOT roll back. Report and
            # save so the executed work is not lost; the REPL keeps running.
            self._console.print(f"[red]错误：{exc}[/red]")
            self._store.save(self._session)
            return

        if round2.tool_calls:
            self._console.print(
                "[yellow dim]本版仅支持单轮工具调用，后续工具请求未执行[/yellow dim]"
            )

        # Text only — NEVER store round-2 tool_calls. Empty round-2 text (e.g.
        # a zero-text interrupt) appends no assistant message; history may end
        # on a tool message, which is acceptable.
        if round2.text:
            self._session.messages.append(
                {"role": "assistant", "content": round2.text}
            )
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
