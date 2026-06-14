"""REPL — main interactive loop for wentian.

Responsibilities:
- Read user input, dispatch slash commands or run one chat turn.
- v0.4 · C19 · F29（任务 T55）— a chat turn is one multi-round
  :class:`~wentian.agent.loop.AgentLoop` run driven via ``asyncio.run``;
  :meth:`REPL._consume_agent` is the single meeting point between async
  agent events and the Rich renderer.
- Maintain conversation history in the session; persist per tool round
  (RoundEnd) and once at turn end; roll back the user message on zero
  progress (len-baseline rule).
- All dependencies injected: provider, session, store, renderer, console,
  input_fn — fully testable offline.

No anthropic/openai/yaml imports here, and NEVER ``wentian.tools`` —
registry/executor stay duck-typed; the AgentLoop coupling is by design
(repl drives the loop, the loop never imports UI).
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable

from rich.console import Console, Group
from rich.table import Table
from rich.text import Text

from wentian.agent.events import (
    AgentDone,
    RoundEnd,
    RoundStart,
    StopReason,
    StreamEnd,
    ToolCallStarted,
    ToolResultReady,
)
from wentian.agent.loop import AgentLoop
from wentian.config import ConfigError
from wentian.providers.base import Message, Provider, TextDelta, ThinkingDelta
from wentian.render import Renderer
from wentian.session import Session, SessionStore
from wentian.ui.interrupt import InterruptListener, NullListener

__all__ = ["REPL"]

_PROMPT = "文天> "

#: v0.4 · C19 · F33（任务 T56）— 计划模式只读工具显式名单。
_PLAN_MODE_TOOLS = ("read_file", "find_files", "search_text")

#: 斜杠命令 (调用串, 说明) —— /help 的渲染数据源，顺序即显示顺序。
_COMMANDS: tuple[tuple[str, str], ...] = (
    ("/help", "显示这份帮助"),
    ("/new", "开启一个新会话"),
    ("/sessions", "列出已保存的会话"),
    ("/resume <id>", "按 id 恢复某个会话"),
    ("/provider <name>", "切换后端 provider"),
    ("/plan [text]", "进入计划模式（只读工具）"),
    ("/do [text]", "退出计划模式（恢复全部工具）"),
    ("/exit", "退出文天"),
)

#: 朱砂——与 banner / 猫脸 / 工具圆点共用的品牌强调色。
_CINNABAR = "#C84B31"


def _build_help() -> Group:
    """构建 /help 的样式化渲染体：朱砂命令名 + dim 中文说明的对齐双列。

    命令名整列单独成列、原文不换行，保证 ``/provider <name>`` 这类长命令也
    对齐；测试只断言命令串出现，配色不影响（非 TTY/管道下自动降级为纯文本）。
    """
    grid = Table.grid(padding=(0, 3))
    grid.add_column(no_wrap=True)
    grid.add_column()
    for invocation, desc in _COMMANDS:
        grid.add_row(
            Text(invocation, style=f"bold {_CINNABAR}"),
            Text(desc, style="dim"),
        )
    return Group(Text("可用命令", style="bold"), grid)


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
        v0.2 · C6 · F18（任务 T23）— InterruptListener; v0.4 · C19 · F29
        （任务 T55）起移交 AgentLoop 构造注入，由循环在每轮流阶段武装。
        Default None → NullListener (yields None → 无中断语义).
    registry:
        v0.3 · C12 · F23（任务 T42/T43）— optional ToolRegistry whose
        ``specs()`` is advertised to the provider. None → tools disabled,
        pure v0.2 behavior (the provider receives ``tools=None``).
    executor:
        v0.3 · C12 · F23（任务 T42/T43）— optional ToolExecutor，v0.4 起
        由 AgentLoop 在多轮循环里调用。Required (paired with ``registry``)
        for tools to actually execute; None → tools disabled.
    max_rounds:
        v0.4 · C19 · F29（任务 T55）— AgentLoop 单回合轮数上限（失控刹车）。
    plan_tools:
        v0.4 · C19 · F33（任务 T56）— 计划模式只读工具名单：/plan 后回合
        的 tools 声明按名过滤为该名单，且同名单作 ``allowed_tools`` 注入
        AgentLoop（双保险）。构造参数可覆盖供测试。
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
        max_rounds: int = 20,
        plan_tools: tuple[str, ...] = _PLAN_MODE_TOOLS,
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
        # v0.4 · C19 · F29（任务 T55）— loop 配置。
        self._max_rounds = max_rounds
        self._plan_tools = tuple(plan_tools)
        # v0.4 · C19 · F33（任务 T56）— 计划模式：REPL 内存态界面策略，
        # 不持久化、且不随 /new //resume //provider 重置（非会话数据，
        # plan.md C19 已记）。
        self._plan_mode: bool = False
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
        """v0.4 · C19 · F29（任务 T55）— 一个用户回合 = 一次 AgentLoop 运行。

        无工具回合就是「第 1 轮即 COMPLETED」的循环——不再有单独的纯对话
        路径。流程：append user → 记 len-baseline → ``asyncio.run`` 驱动
        :meth:`_consume_agent` 消费循环事件 → 按 len-baseline 规则收尾：

        - 循环结束后 messages 长度仍 == baseline ⇒ 零进展（零文字中断 /
          首轮流错误），弹出未答之问、不落盘（历史无未答之问，AC16 续承）；
        - 否则落盘。逐轮落盘（工具副作用已真实发生，崩溃不可丢）发生在
          :meth:`_consume_agent` 的 RoundEnd 处。

        中断/停机语义全部住在 AgentLoop（USER_CANCELLED 部分文字只存文本、
        STREAM_ERROR 整轮丢弃且异常绝不逃逸、MAX_ROUNDS/UNKNOWN_TOOL_LOOP
        刹车）；本方法只负责回滚与持久化。``KeyboardInterrupt`` 兜底：循环
        按轮原子入史，此刻历史必成对一致，按同一 len-baseline 规则收尾。

        executor 缺席决策（保持 v0.3 外显行为）：registry 存在时 tools=
        照常透传给 provider（声明 ≠ 执行，v0.3 既有契约），但循环以
        registry=None / executor=None / max_rounds=1 运行——若模型仍请求
        工具，第 1 轮即触发 MAX_ROUNDS 刹车：只存文本、不执行、不入未答
        tool_use，与 v0.3「executor=None ⇒ 忽略 tool_calls 走纯文本路径」
        全等；对应的上限提示以 ``limit_notice=False`` 抑制（v0.3 此场景
        本就静默）。
        """
        tools, system = self._effective_tools_and_system()

        user_msg: Message = {"role": "user", "content": user_text}
        self._session.messages.append(user_msg)
        baseline = len(self._session.messages)

        tools_enabled = self._registry is not None and self._executor is not None
        if tools_enabled:
            # v0.4 · C19 · F33（任务 T56）— 计划模式双保险之二：同名单作
            # allowed_tools 注入循环，名单外调用由 loop 合成 blocked 结果
            # 拦截（声明过滤挡引导，blocked 拦截挡硬闯）。
            allowed_tools = (
                frozenset(self._plan_tools) if self._plan_mode else None
            )
            agent = AgentLoop(
                self._provider,
                registry=self._registry,
                executor=self._executor,
                interrupt_listener=self._interrupt_listener,
                max_rounds=self._max_rounds,
                allowed_tools=allowed_tools,
            )
        else:
            # 纯对话循环（见 docstring 的 executor 缺席决策）。
            agent = AgentLoop(
                self._provider,
                registry=None,
                executor=None,
                interrupt_listener=self._interrupt_listener,
                max_rounds=1,
                allowed_tools=None,
            )

        try:
            done = asyncio.run(
                self._consume_agent(
                    agent.run(self._session.messages, system=system, tools=tools),
                    limit_notice=tools_enabled,
                )
            )
        except KeyboardInterrupt:
            # 循环按轮原子入史 ⇒ 此刻历史成对一致；走同一收尾规则。
            done = None
        del done  # 提示性返回值（停机提示已在 _consume_agent 内打印）

        if len(self._session.messages) == baseline:
            # 零进展 → 回滚未答之问，不落盘。
            self._session.messages.pop()
            return
        self._store.save(self._session)

    def _effective_tools_and_system(self) -> tuple[list | None, str | None]:
        """v0.4 · C19 · F29/F33（任务 T55；T56 计划模式过滤）— 本回合生效的
        (tools, system)。

        无 registry → (None, system) 即纯 v0.2 行为（计划模式开关此时
        无效果）。registry 存在且计划模式开启 → specs 按名过滤为
        ``self._plan_tools``（声明过滤挡引导），system 追加计划模式后缀；
        否则维持 T55 行为：全量 specs、system 原样。
        """
        if self._registry is None:
            return None, self._system
        specs = self._registry.specs()
        if not self._plan_mode:
            return specs, self._system
        allowed = set(self._plan_tools)
        tools = [spec for spec in specs if spec.name in allowed]
        names = "、".join(self._plan_tools)
        suffix = (
            f"计划模式：只有只读工具可用（{names}）。先勘察代码，"
            "再给出分步执行计划后停下，不要做任何修改；用户将用 /do "
            "切到执行模式。"
        )
        system = (self._system or "") + "\n\n" + suffix
        return tools, system

    async def _consume_agent(
        self, events, *, limit_notice: bool = True
    ) -> AgentDone | None:
        """v0.4 · C19 · F29（任务 T55）— async 事件 → 渲染/持久化映射器。

        async 世界与 Rich 的唯一交汇点。映射：RoundStart → 新 StreamView
        武装 spinner；Thinking/TextDelta → view.feed；StreamEnd →
        view.finish（中断标记在这里落屏）；ToolCallStarted/ToolResultReady
        → ⏺/⎿ 行；RoundEnd（有工具结果）→ 逐轮落盘；AgentDone → 捕获为
        终值。UsageUpdate 此处刻意忽略——总量由 ``AgentDone.usage`` 经
        ``render_usage`` 一次性屏显。

        循环收束后按停机原因打印提示：STREAM_ERROR 红错误行（与 v0.3 的
        「错误：…」同款式）、MAX_ROUNDS 黄提示（``limit_notice=False`` 时
        抑制，见 _chat_once 的 executor 缺席决策）、UNKNOWN_TOOL_LOOP 黄
        提示；最后 render_usage（usage=None 自动不打印）。
        """
        view = None
        final: AgentDone | None = None
        async for ev in events:
            if isinstance(ev, RoundStart):
                view = self._renderer.new_stream_view()
                view.start()
            elif isinstance(ev, (ThinkingDelta, TextDelta)):
                view.feed(ev)
            elif isinstance(ev, StreamEnd):
                view.finish(interrupted=ev.interrupted)
                view = None
            elif isinstance(ev, ToolCallStarted):
                self._renderer.render_tool_call(ev.call)
            elif isinstance(ev, ToolResultReady):
                self._renderer.render_tool_result(ev.outcome)
            elif isinstance(ev, RoundEnd):
                if ev.tool_results:
                    # 逐轮落盘：副作用已真实发生，崩溃不可丢。
                    self._store.save(self._session)
            elif isinstance(ev, AgentDone):
                final = ev

        if view is not None:
            # STREAM_ERROR 轮没有 StreamEnd：只清 spinner/Live、不打终稿
            # （与 render_stream 错误路径的 _stop_displays 用法一致）。
            view._stop_displays()

        if final is None:
            return None
        if final.stop_reason is StopReason.STREAM_ERROR:
            self._console.print(f"[red]错误：{final.error}[/red]")
        elif final.stop_reason is StopReason.MAX_ROUNDS and limit_notice:
            self._console.print(
                f"[yellow dim]已达本轮工具循环上限（{self._max_rounds} 轮），"
                "剩余工具请求未执行[/yellow dim]"
            )
        elif final.stop_reason is StopReason.UNKNOWN_TOOL_LOOP:
            self._console.print(
                "[yellow dim]模型连续调用未知工具，已停止本轮循环[/yellow dim]"
            )
        self._renderer.render_usage(final.usage, final.rounds)
        return final

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
            "/plan": self._cmd_plan,
            "/do": self._cmd_do,
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
        self._console.print(_build_help())

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

    def _cmd_plan(self, args: str) -> None:
        """v0.4 · C19 · F33（任务 T56）— 进入计划模式（幂等）。

        尾随文字即刻作为下一条用户消息发出（该回合已按计划模式过滤）。
        """
        self._plan_mode = True
        self._console.print("[green]已进入计划模式（只读工具）。用 /do 退出[/green]")
        text = args.strip()
        if text:
            self._chat_once(text)

    def _cmd_do(self, args: str) -> None:
        """v0.4 · C19 · F33（任务 T56）— 退出计划模式，恢复全部工具。

        尾随文字即刻作为下一条用户消息发出（如 ``/do 按计划执行``）；
        裸 /do 仅切换不发消息。
        """
        self._plan_mode = False
        self._console.print("[green]已退出计划模式，恢复全部工具[/green]")
        text = args.strip()
        if text:
            self._chat_once(text)

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

        v0.4 · C19 · F33（任务 T56）— 计划模式时追加 `` │ 计划模式``
        （PromptInput 工具栏自动拾取）。
        """
        model = getattr(self._provider, "model", "")
        if model:
            backend = f"{self._provider.name}:{model}"
        else:
            backend = self._provider.name
        n = len(self._session.messages)
        line = f"{backend} │ 会话 {self._session.id} │ {n} 条消息"
        if self._plan_mode:
            line += " │ 计划模式"
        return line
