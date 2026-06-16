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
import datetime
import platform
import subprocess
from collections.abc import Callable
from pathlib import Path

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

# v0.6 · C38 · F47（任务 T78）— 装配/UI 层允许 import permissions（纯叶子模块）。
from wentian.permissions.decision import MODE_CYCLE, Mode
from wentian.prompt.reminders import EnvInfo, build_request_decorator
from wentian.providers.base import Message, Provider, TextDelta, ThinkingDelta
from wentian.render import Renderer
from wentian.session import Session, SessionStore
from wentian.ui.confirm import Cancelled as _Cancelled
from wentian.ui.interrupt import InterruptListener, NullListener

__all__ = ["REPL"]

# v0.6 · C37 · F48（任务 T77）— 友好名 → 配置规则前缀（写永久规则用）。
# 与 permissions.rules.FRIENDLY_TO_TOOL 同义（这里只需正向友好名集合）。
_FRIENDLY_NAMES = frozenset({"Bash", "Read", "Write", "Edit", "Glob", "Grep"})

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


def _current_git_branch() -> str | None:
    """返回当前 Git 分支名；非 git 仓库或任何错误静默返回 None。

    使用 subprocess 调用 ``git rev-parse --abbrev-ref HEAD``；
    超时 1 秒、不继承 stdin/stderr（测试环境或非 git 目录下安全降级）。
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=1,
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
            return branch if branch else None
        return None
    except Exception:  # noqa: BLE001 — FileNotFoundError, TimeoutExpired, etc.
        return None


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


def _persist_allow_rule(project_root: Path, rule_str: str) -> None:
    """v0.6 · C37 · F48（任务 T77）— 把精确 allow 规则幂等写入本地层配置。

    目标文件 ``<project_root>/.wentian/settings.local.yaml`` 的
    ``permissions.allow`` 列表。文件/目录不存在则创建；保留已有内容；同一
    规则已存在则不重复加（幂等）。任何 I/O / 解析错误静默吞掉——永久落盘失败
    不应中断对话（本会话内存规则已即时生效）。
    """
    import yaml  # 局部 import：repl 模块顶层保持无 yaml 依赖（分层惯例）。

    try:
        wt = project_root / ".wentian"
        wt.mkdir(parents=True, exist_ok=True)
        path = wt / "settings.local.yaml"

        data: dict = {}
        if path.exists():
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded

        perms = data.get("permissions")
        if not isinstance(perms, dict):
            perms = {}
            data["permissions"] = perms
        allow = perms.get("allow")
        if not isinstance(allow, list):
            allow = []
            perms["allow"] = allow

        if rule_str not in allow:
            allow.append(rule_str)
            path.write_text(
                yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
    except (OSError, yaml.YAMLError):
        # 永久落盘失败不致命：内存规则已生效，本会话不受影响。
        return


def _rule_string(friendly: str, target: str) -> str:
    """把 (友好名, 目标) 拼成配置规则串：``Friendly(target)`` 或裸 ``Friendly``。"""
    if target:
        return f"{friendly}({target})"
    return friendly


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
        # v0.6 · C37 · F48（任务 T77）— 权限流水线（duck-typed：用 decide /
        # project_root / settings）。None ⇒ 无权限门、v0.5 行为（回归安全）。
        pipeline: object | None = None,
        # v0.6 · C37 · F48（任务 T77）— 人在回路审批 async 回调；默认包装
        # ui.confirm.confirm_action，测试可注入假回调返回 Choice / 抛 Cancelled。
        confirm_fn: Callable[..., object] | None = None,
        # v0.6 · C38 · F47（任务 T78）— 初始权限模式；T79 由 settings.default_mode
        # 注入，默认 Mode.DEFAULT。模式存于 REPL 状态 → 跨轮保持（不随回合重置）。
        default_mode: Mode = Mode.DEFAULT,
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
        # v0.6 · C38 · F47（任务 T78）— 权限模式统一为单一 REPL 状态：
        # Shift+Tab 在 MODE_CYCLE 上循环；/plan·/do 是 plan 档的专用入出口。
        # 模式是界面策略（不持久化、不随 /new //resume //provider 重置），存于
        # REPL 状态 → 天然跨轮保持（AC49）。原 self._plan_mode 收编为
        # ``self._mode == Mode.PLAN`` 的派生属性（见下方 property）。
        self._mode: Mode = default_mode
        # v0.6 · C37 · F48（任务 T77）— 权限门装配料。
        self._pipeline = pipeline
        self._confirm_fn = confirm_fn
        self._console: Console = renderer.console

    # ------------------------------------------------------------------
    # v0.6 · C38 · F47（任务 T78）— 权限模式状态
    # ------------------------------------------------------------------

    @property
    def _plan_mode(self) -> bool:
        """计划模式派生属性：``self._mode == Mode.PLAN``。

        F33 的所有读点（声明过滤 / allowed_tools / 计划提醒 decorator）继续读这个
        布尔，行为不变——只是真值来源从独立布尔收编为统一的 ``self._mode``。
        """
        return self._mode is Mode.PLAN

    def get_mode(self) -> Mode:
        """返回当前权限模式（权限门 get_mode 回调直接复用，见 _build_gate）。"""
        return self._mode

    def cycle_mode(self) -> None:
        """Shift+Tab：把 self._mode 推进到 MODE_CYCLE 的下一档（到尾回首）。

        模式存于 REPL 状态 → 跨轮保持（AC49）。bottom toolbar 是每次 prompt
        重算的 callable（读 live status_line），切换后下一次渲染自动反映。
        """
        idx = MODE_CYCLE.index(self._mode)
        self._mode = MODE_CYCLE[(idx + 1) % len(MODE_CYCLE)]

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
        tools = self._effective_tools()
        system = self._system

        # v0.5 · C22 · F35/F39（任务 T66）— 构造 request_decorator：每回合
        # 发出前将环境信息 + 计划模式开关提醒注入消息通道（<system-reminder>
        # 标签），绝不写回 session.messages（持久化纯净，AC37）。
        env = EnvInfo(
            cwd=Path.cwd(),
            os=platform.system(),
            date=datetime.date.today().isoformat(),
            git_branch=_current_git_branch(),
        )
        decorator = build_request_decorator(env=env, plan_mode=self._plan_mode)

        user_msg: Message = {"role": "user", "content": user_text}
        self._session.messages.append(user_msg)
        baseline = len(self._session.messages)

        tools_enabled = self._registry is not None and self._executor is not None
        if tools_enabled:
            # v0.4 · C19 · F33（任务 T56）— 计划模式双保险之二：同名单作
            # allowed_tools 注入循环，名单外调用由 loop 合成 blocked 结果
            # 拦截（声明过滤挡引导，blocked 拦截挡硬闯）。
            allowed_tools = frozenset(self._plan_tools) if self._plan_mode else None
            agent = AgentLoop(
                self._provider,
                registry=self._registry,
                executor=self._executor,
                interrupt_listener=self._interrupt_listener,
                max_rounds=self._max_rounds,
                allowed_tools=allowed_tools,
                # v0.6 · C37 · F48（任务 T77）— 有 pipeline 才装权限门；
                # None ⇒ 无门、v0.5 行为（回归安全）。
                permission_gate=self._build_gate(),
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
                    agent.run(
                        self._session.messages,
                        system=system,
                        tools=tools,
                        request_decorator=decorator,
                    ),
                    limit_notice=tools_enabled,
                )
            )
        except KeyboardInterrupt:
            # 循环按轮原子入史 ⇒ 此刻历史成对一致；走同一收尾规则。
            done = None
        except _Cancelled:
            # v0.6 · C37 · F48/N13（任务 T77）— 人在回路按 Esc/Ctrl+C 取消：
            # 干净结束本轮、不退出程序、不泄漏 task（asyncio.run 已收束本轮
            # 事件循环与挂起任务）。历史按轮原子入史 ⇒ 此刻成对一致，走同一
            # len-baseline 收尾规则（零进展则回滚未答之问）。
            done = None
            self._console.print("[yellow dim]已取消本次工具确认[/yellow dim]")
        del done  # 提示性返回值（停机提示已在 _consume_agent 内打印）

        if len(self._session.messages) == baseline:
            # 零进展 → 回滚未答之问，不落盘。
            self._session.messages.pop()
            return
        self._store.save(self._session)

    def _effective_tools(self) -> list | None:
        """v0.5 · C22 · F35/F39（任务 T66）— 本回合生效的 tools 声明。

        无 registry → None（纯 v0.2 行为，计划模式开关此时无效果）。
        registry 存在且计划模式开启 → specs 按名过滤为 ``self._plan_tools``
        （声明过滤挡引导）；否则全量 specs。

        注意：v0.4 时此方法曾同时返回 (tools, system)，并在计划模式下给
        system 追加后缀（F33 旧实现）。v0.5 起 system 保持稳定——计划模式
        提醒改由 build_request_decorator 产生的 <system-reminder> 消息通道
        承载（AC40）；故此方法已收窄为只决定 tools。
        """
        if self._registry is None:
            return None
        specs = self._registry.specs()
        if not self._plan_mode:
            return specs
        allowed = set(self._plan_tools)
        return [spec for spec in specs if spec.name in allowed]

    # ------------------------------------------------------------------
    # v0.6 · C37 · F48（任务 T77）— 人在回路权限门装配
    # ------------------------------------------------------------------

    def _build_gate(self):
        """构造注入 AgentLoop 的 async ``permission_gate``，或 None（无 pipeline）。

        有 pipeline 时，以 ``ui.confirm``（或注入的 ``confirm_fn``）做 ask 回调
        （含 Esc/Ctrl+C 干净取消本轮，N13），用 :func:`build_permission_gate`
        造闭包；``get_mode`` 直接返回统一的 ``self._mode``（v0.6 · C38 · F47 ·
        任务 T78：去掉 T77 临时的 plan 布尔映射）。无 pipeline 返回 None ⇒ v0.5
        行为。
        """
        if self._pipeline is None:
            return None

        # 装配层 import（permission_gate 模块跨层、可 import permissions+tools+ui）。
        from wentian.permission_gate import build_permission_gate

        def get_mode() -> Mode:
            return self._mode

        async def ask(call, decision):
            # 关键参数预览：命令串或路径（从 arguments 抽，回退到全量 args）。
            preview = self._preview_args(call)
            return await self._confirm(
                tool_name=call.name,
                preview=preview,
                reason=getattr(decision, "reason", "") or "需要你确认本次工具调用",
            )

        def on_allow_always(friendly: str, target: str, is_path: bool) -> None:
            self._persist_always_rule(friendly, target, is_path)

        return build_permission_gate(
            pipeline=self._pipeline,
            registry=self._registry,
            ask=ask,
            get_mode=get_mode,
            on_allow_always=on_allow_always,
        )

    async def _confirm(self, *, tool_name: str, preview: str, reason: str):
        """调用注入的 confirm_fn，否则用默认 ui.confirm.confirm_action。"""
        if self._confirm_fn is not None:
            return await self._confirm_fn(
                tool_name=tool_name, preview=preview, reason=reason
            )
        from wentian.ui.confirm import confirm_action

        return await confirm_action(tool_name=tool_name, preview=preview, reason=reason)

    @staticmethod
    def _preview_args(call) -> str:
        """从工具调用参数里挑一个简短的可读预览串。"""
        args = getattr(call, "arguments", None)
        if not isinstance(args, dict) or not args:
            return ""
        # 优先 command / path / 第一个字符串值。
        for key in ("command", "path", "pattern", "file_path"):
            value = args.get(key)
            if isinstance(value, str) and value:
                return value
        for value in args.values():
            if isinstance(value, str) and value:
                return value
        return ""

    def _persist_always_rule(self, friendly: str, target: str, is_path: bool) -> None:
        """ALLOW_ALWAYS 落盘 + 内存即时生效。

        - 永久：精确规则写入本地层 settings.local.yaml（幂等）；
        - 即时：追加到 pipeline 的 LayeredRules.local（本会话立即生效）。
        """
        if friendly not in _FRIENDLY_NAMES:
            return
        rule_str = _rule_string(friendly, target)

        # 1) 永久落盘（项目根来自 pipeline.project_root）。
        project_root = getattr(self._pipeline, "project_root", None)
        if project_root is not None:
            _persist_allow_rule(Path(project_root), rule_str)

        # 2) 内存即时生效：追加 Rule 到 local 层。
        try:
            from wentian.permissions.decision import Verdict
            from wentian.permissions.rules import Rule

            rules = self._pipeline.settings.rules  # LayeredRules
            local = rules.local  # RuleSet (mutable)
            pattern = target if target else None
            new_rule = Rule(friendly=friendly, pattern=pattern, effect=Verdict.ALLOW)
            if new_rule not in local.allow:
                local.allow.append(new_rule)
        except Exception:  # noqa: BLE001 — 内存追加失败不致命（已落盘）。
            return

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
            self._console.print(
                f"[yellow]未知命令：{cmd}  输入 /help 查看帮助[/yellow]"
            )
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

        v0.6 · C38 · F47（任务 T78）— plan 统一为一档：进 plan 即把 self._mode
        置为 Mode.PLAN（Shift+Tab 也可达此档），F33 机制全部 re-key 于 mode==PLAN。
        """
        self._mode = Mode.PLAN
        self._console.print("[green]已进入计划模式（只读工具）。用 /do 退出[/green]")
        text = args.strip()
        if text:
            self._chat_once(text)

    def _cmd_do(self, args: str) -> None:
        """v0.4 · C19 · F33（任务 T56）— 退出计划模式，恢复全部工具。

        尾随文字即刻作为下一条用户消息发出（如 ``/do 按计划执行``）；
        裸 /do 仅切换不发消息。

        v0.6 · C38 · F47（任务 T78）— /do 固定切回 Mode.DEFAULT（不恢复进入
        plan 前的旧档）。
        """
        self._mode = Mode.DEFAULT
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

        v0.6 · C38 · F47（任务 T78）— **首段由 provider:model 改为当前权限模式**：
        ``{mode.value} │ 会话 {session.id} │ {n} 条消息``。占据原 provider 名的位置、
        **不再展示 provider 名**（AC49）。读 live self._mode / self._session，
        Shift+Tab、/new、/resume 后下一次工具栏重算自动反映，无需额外通知。

        v0.4 · C19 · F33（任务 T56）— 计划模式时追加 `` │ 计划模式``：plan 档已在
        首段以 ``plan`` 显示，此后缀保留为冗余的中文提示（F33 既有 status_line
        行为不变；plan-mode 回归断言「计划模式」字样照旧命中）。
        """
        n = len(self._session.messages)
        line = f"{self._mode.value} │ 会话 {self._session.id} │ {n} 条消息"
        if self._plan_mode:
            line += " │ 计划模式"
        return line
