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
    UsageUpdate,
)
from wentian.agent.loop import AgentLoop
from wentian.memory import extractor as _memory_extractor

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
    ("/compact", "压缩当前对话上下文"),
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


def _format_compaction_report(result) -> str:
    """v0.8 · C52 · F61/F62（任务 T96）— 把 CompactionResult 渲染成可读汇报。

    汇报顺序：卸载条数（第一层）→ 是否摘要（第二层）→ 熔断/失败状态。
    duck-typed：只读 ``offloaded`` / ``summarized`` / ``tripped`` /
    ``failed_this_call`` 四个字段，不 import context 包类型。
    """
    parts: list[str] = []
    n_off = len(getattr(result, "offloaded", []) or [])
    if n_off:
        parts.append(f"已卸载 {n_off} 条超大工具结果")
    if getattr(result, "summarized", False):
        parts.append("已摘要较早历史")
    elif getattr(result, "failed_this_call", False):
        parts.append("本次摘要失败")
    if getattr(result, "tripped", False):
        parts.append("重量摘要已熔断（后续自动轮跳过）")
    if not parts:
        parts.append("当前上下文无需压缩")
    return " · ".join(parts)


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
        # v0.7 · C46 · F55/N23（任务 T88）— MCPManager for lifecycle management.
        # None when no mcpServers configured (N23: zero behavior change).
        mcp_manager: object | None = None,
        # v0.8 · C52 · F61/F62（任务 T96）— two-layer context compactor
        # (duck-typed: only .compact / .set_provider / .set_artifacts_dir used).
        # None ⇒ no compaction, byte-level v0.7 behavior (N25, regression-safe).
        compactor: object | None = None,
        # v0.9 · C58 · F67/N29（任务 T106）— background memory runner (duck-typed:
        # only .submit / .close used). None ⇒ no extraction, v0.8 behavior.
        memory_runner: object | None = None,
        # v0.9 · C55 · F65/N29（任务 T106）— one-shot resume time-gap reminder
        # string injected via the <system-reminder> channel on the FIRST turn
        # after resume, then cleared. Never written back to session.messages /
        # persisted. None ⇒ no reminder, v0.8 behavior.
        resume_reminder: str | None = None,
        # v0.10 · C91 · F73/F76/N35（任务 T114）— 斜杠命令注册中心（duck-typed：
        # 仅用 .lookup / .visible）。None ⇒ 回退 v0.9 硬编码 dict 分发（回归安全）。
        commands: object | None = None,
        # v0.10 · C91 · F74（任务 T114）— 长期记忆存储（duck-typed：仅用
        # .read_indexes_for_injection / .user_dir / .project_dir）供 /memory 展示。
        # None ⇒ /memory 显示「未启用长期记忆」。
        memory_store: object | None = None,
        # v0.11 · C104 · F73/F87（任务 T134a）— Skill 激活编排器（duck-typed：仅用
        # .active_bodies / .allowed_tools / .clear）。None ⇒ 不注入 skill 正文、
        # 不收窄 skill 白名单、/clear · /new 不清激活集——逐字节 v0.10 行为（回归安全）。
        activator: object | None = None,
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
        # v0.7 · C46 · F55/N23（任务 T88）— MCPManager 生命周期持有。
        # None 时 run() 退出路径的 close_all 调用静默跳过（N23）。
        self._mcp_manager = mcp_manager
        # v0.8 · C52 · F61/F62（任务 T96）— 上下文压缩器（duck-typed）。None ⇒
        # 不注入 pre_round_compact 钩子、字节级等价 v0.7（N25）。_last_round_usage
        # 由 _consume_agent 在 UsageUpdate 时刷新，作为下一轮压缩估算的锚点；
        # 初值 None（首轮无锚点，估算降级为全量字符折算）。
        self._compactor = compactor
        self._last_round_usage: object | None = None
        # v0.9 · C58 · F67（任务 T106）— 后台记忆抽取（duck-typed）。None ⇒ 不抽取。
        self._memory_runner = memory_runner
        # v0.9 · C55 · F65（任务 T106）— 一次性恢复时间跨度提醒；首回合注入后清空。
        self._resume_reminder = resume_reminder
        # v0.10 · C91 · F73/F74（任务 T114）— 命令注册中心 + 记忆存储（duck-typed）。
        self._commands = commands
        self._memory_store = memory_store
        # v0.11 · C104 · F73/F87（任务 T134a）— Skill 激活编排器（duck-typed）。
        # None ⇒ 不喂 skill 正文、不收窄 skill 白名单、/clear · /new 不清激活集。
        self._activator = activator
        # v0.9 · C54 · F64（任务 T106）— 追加写游标：已落盘消息数。恢复的会话
        # 以当前内存消息数为基（这些行已在磁盘上），新会话为 0。RoundEnd / 回合末
        # 改用 store.append(messages[cursor:]) 增量追加（F64：崩溃只丢最后一行）。
        self._persisted_count = len(session.messages)
        # v0.9 review fix（Major #1）— 已落盘前缀的内容指纹。常态纯追加时它随
        # 游标推进；当上下文压缩（offload）把游标**之下**的消息 content 原地改写
        # （列表长度不变、追加路径侦测不到）时，指纹会变 → 触发一次全量原子 save，
        # 否则磁盘留旧原文、恢复时整段回灌、offload 失效。
        self._persisted_fingerprint: list[int] = self._fingerprint(
            session.messages[: self._persisted_count]
        )

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
    # v0.10 · C91 · F73/F74/F76（任务 T114）— CommandContext 协议实现
    #
    # REPL 作装配/界面层实现 commands.context.CommandContext 协议（鸭子，无需显式
    # 继承——@runtime_checkable 的结构化检查即真）；commands/ 包的各 handler 只依赖
    # 本协议面，不直接 import REPL/agent/provider。打印职责见下方各方法 docstring。
    # ------------------------------------------------------------------

    def print(self, renderable: object) -> None:
        """在终端输出 *renderable*（字符串或 Rich Renderable）——交给 console。"""
        self._console.print(renderable)

    def send_user_message(self, text: str) -> None:
        """把 *text* 作为用户消息送入对话，触发一轮 AI（复用 :meth:`_chat_once`）。

        PROMPT 类命令（如 /review）的唯一出口；语义与用户直接输入文本全等。
        """
        self._chat_once(text)

    def set_mode(self, mode: Mode) -> None:
        """切换权限模式（纯操作、不打印——确认文案由对应 handler 负责）。"""
        self._mode = mode

    def token_usage(self) -> object | None:
        """返回上一轮 token 用量快照（``self._last_round_usage``；无历史为 None）。"""
        return self._last_round_usage

    def memory_summary(self) -> str:
        """返回长期记忆目录 + 各域 INDEX 摘要的只读字符串。

        注入 ``memory_store`` 时拼「记忆目录（user_dir / project_dir）+
        read_indexes_for_injection() 文本（截断到合理长度）」；未注入返回
        「（未启用长期记忆）」字样。store 为鸭子：读公有 user_dir / project_dir。
        """
        if self._memory_store is None:
            return "（未启用长期记忆）"
        store = self._memory_store
        user_dir = getattr(store, "user_dir", None)
        project_dir = getattr(store, "project_dir", None)
        lines = ["长期记忆目录："]
        lines.append(f"  user    : {user_dir}")
        lines.append(f"  project : {project_dir}")
        index_text = ""
        try:
            index_text = store.read_indexes_for_injection() or ""
        except Exception:  # noqa: BLE001 — 读 INDEX 失败不致命，仅缺正文。
            index_text = ""
        index_text = index_text.strip()
        if index_text:
            # 截断到合理长度（避免一屏刷不完；INDEX 已是一行一摘要，2000 字够看）。
            if len(index_text) > 2000:
                index_text = index_text[:2000] + "…（已截断）"
            lines.append("")
            lines.append(index_text)
        else:
            lines.append("")
            lines.append("（暂无记忆条目）")
        return "\n".join(lines)

    def visible_commands(self) -> list:
        """返回当前可见命令列表（``commands.visible()``；未注入返回 []）。"""
        return self._commands.visible() if self._commands is not None else []

    def clear_context(self) -> None:
        """/clear 语义：清空当前会话 messages，**保留同一会话 id**（AC92）。

        纯操作、不打印（确认输出由 ``_h_clear`` 负责）：清空 ``session.messages``、
        复位追加写游标 / 指纹 / 上轮用量，并以空历史覆写落盘（同 id 不变）。
        """
        self._session.messages.clear()
        self._persisted_count = 0
        self._persisted_fingerprint = []
        self._last_round_usage = None
        self._store.save(self._session)
        # v0.11 · C104 · F73/F87（任务 T134a）— 清空 Skill 激活集（duck-typed）。
        if self._activator is not None:
            self._activator.clear()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Enter the REPL loop; returns when the user types /exit or sends EOF.

        v0.7 · C46 · F55/N23（任务 T88）— All exit paths (normal /exit,
        EOFError/KeyboardInterrupt, unexpected exception) call
        ``_mcp_manager.close_all()`` via try/finally so MCP subprocess
        connections are never leaked.  When ``_mcp_manager`` is None the
        finally block is a no-op (N23).
        """
        import atexit

        # atexit 兜底：防止 finally 来不及执行（如 os._exit / 外部 kill）。
        # v0.9 · C58（任务 T106）— memory runner 一并兜底关闭（短 join、daemon
        # 线程不卡退出）。
        if self._mcp_manager is not None or self._memory_runner is not None:
            _manager_ref = self._mcp_manager
            _runner_ref = self._memory_runner

            def _atexit_close() -> None:
                if _manager_ref is not None:
                    try:
                        _manager_ref.close_all()
                    except Exception:  # noqa: BLE001
                        pass
                if _runner_ref is not None:
                    try:
                        _runner_ref.close()
                    except Exception:  # noqa: BLE001
                        pass

            atexit.register(_atexit_close)

        try:
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
        finally:
            if self._mcp_manager is not None:
                self._mcp_manager.close_all()
            # v0.9 · C58（任务 T106）— 所有退出路径短 join 后台抽取线程。
            if self._memory_runner is not None:
                try:
                    self._memory_runner.close()
                except Exception:  # noqa: BLE001 — 退出清理失败不致命
                    pass

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
        # v0.11 · C104 · F73/F87（任务 T134a）— 有 activator 时，把它的 active_bodies
        # 绑定方法（live 回调）喂给 decorator：每轮请求实时读已激活 skill 正文，经
        # <system-reminder> 通道注入最后一条 user（绝不写回 session.messages）。
        active_skill_bodies = (
            self._activator.active_bodies if self._activator is not None else None
        )
        base_decorator = build_request_decorator(
            env=env,
            plan_mode=self._plan_mode,
            active_skill_bodies=active_skill_bodies,
        )

        # v0.9 · C55 · F65（任务 T106）— 一次性恢复时间跨度提醒：恢复后首回合
        # 经 <system-reminder> 通道注入一次后清空。绝不写回 session.messages、
        # 不持久化（与 env/plan 提醒同构——只活在本次请求拷贝里）。
        reminder = self._resume_reminder
        self._resume_reminder = None  # 取出即清空：仅本回合注入一次
        if reminder is None:
            decorator = base_decorator
        else:
            reminder_block = f"<system-reminder>\n{reminder}\n</system-reminder>"

            def decorator(messages: list[Message], round_index: int) -> list[Message]:
                result = base_decorator(messages, round_index)
                # 仅在本回合第 1 轮注入（同一回合后续轮不重复）。
                if round_index != 1:
                    return result
                # 追加到最后一条 user 消息的 content（请求拷贝，绝不动原 dict）。
                last_user_idx: int | None = None
                for i, msg in enumerate(result):
                    if msg.get("role") == "user":
                        last_user_idx = i
                if last_user_idx is None:
                    return result
                copied = dict(result[last_user_idx])
                copied["content"] = copied.get("content", "") + "\n" + reminder_block
                new_result = list(result)
                new_result[last_user_idx] = copied
                return new_result

        user_msg: Message = {"role": "user", "content": user_text}
        self._session.messages.append(user_msg)
        baseline = len(self._session.messages)

        # v0.8 · C52 · F61/F62/N25（任务 T96）— 把压缩器的 compact（manual=False，
        # 自动余量）作为 loop 的 pre_round_compact 写回钩子。compactor 为 None ⇒
        # 不传钩子，AgentLoop 与 v0.7 字节级等价（回归安全）。
        pre_round_compact = (
            self._compactor.compact if self._compactor is not None else None
        )

        tools_enabled = self._registry is not None and self._executor is not None
        if tools_enabled:
            # v0.4 · C19 · F33（任务 T56）— 计划模式双保险之二：同名单作
            # allowed_tools 注入循环，名单外调用由 loop 合成 blocked 结果
            # 拦截（声明过滤挡引导，blocked 拦截挡硬闯）。
            # v0.11 · C104 · F73/F87（任务 T134a）— 与 skill 白名单组合（交集 =
            # 最严胜，load_skill 始终保留），见 _combine_allowed_tools。
            plan_allowed = frozenset(self._plan_tools) if self._plan_mode else None
            skill_allowed = (
                self._activator.allowed_tools() if self._activator is not None else None
            )
            allowed_tools = self._combine_allowed_tools(plan_allowed, skill_allowed)
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
                        pre_round_compact=pre_round_compact,
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

        if len(self._session.messages) == baseline:
            # 零进展 → 回滚未答之问，不落盘、不抽取记忆。
            self._session.messages.pop()
            return

        # 回合末追加落盘（F64：增量 append；逐轮已在 RoundEnd 写过的不重复）。
        self._persist_pending()

        # v0.9 · C58 · F67（任务 T106）— COMPLETED 回合后 fire-and-forget 后台抽取。
        # 鸭子调用：runner 为 None 跳过（回归 v0.8）；submit 立即返回、绝不阻塞、
        # 抽取异常由 runner 内部静默吞，不影响本回合。
        if (
            self._memory_runner is not None
            and done is not None
            and done.stop_reason is StopReason.COMPLETED
        ):
            try:
                window = _memory_extractor.build_recent_window(self._session.messages)
                self._memory_runner.submit(window)
            except Exception:  # noqa: BLE001 — 抽取派发绝不影响对话主流程
                pass

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

    @staticmethod
    def _combine_allowed_tools(
        plan_allowed: frozenset[str] | None,
        skill_allowed: frozenset[str] | None,
    ) -> frozenset[str] | None:
        """v0.11 · C104 · F73/F87（任务 T134a）— 组合计划模式与 skill 白名单。

        规则（最严胜，``load_skill`` 始终保留）：

        - 两者皆 None → None（不收窄）。
        - 恰一个为 None → 返回另一个（单边收窄）。
        - 两者皆集合 → ``(plan & skill) | {"load_skill"}``（交集 = 最严，
          但 ``load_skill`` 永远可调，让模型随时能切换/加载 Skill）。

        计划模式只读限制与 skill 白名单互不豁免：plan 在场时只读约束照旧成立，
        skill 白名单在此基础上进一步收窄。
        """
        if plan_allowed is None and skill_allowed is None:
            return None
        if plan_allowed is None:
            return skill_allowed
        if skill_allowed is None:
            return plan_allowed
        return (plan_allowed & skill_allowed) | {"load_skill"}

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
        终值。UsageUpdate（v0.8 · C52 · F61/F62 · 任务 T96）→ 存入
        ``self._last_round_usage`` 作下一轮压缩估算锚点；总量仍由
        ``AgentDone.usage`` 经 ``render_usage`` 一次性屏显（外显不变）。

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
                    # 逐轮落盘：副作用已真实发生，崩溃不可丢。v0.9 改追加写
                    # （F64：增量 append、崩溃只丢最后一行）。
                    self._persist_pending()
            elif isinstance(ev, UsageUpdate):
                # v0.8 · C52 · F61/F62（任务 T96）— 存单轮 usage 作下一轮压缩
                # 估算锚点；屏显总量仍走 AgentDone.usage（此前刻意忽略此事件，
                # 现仅多存一个字段，外显行为不变）。
                self._last_round_usage = ev.round_usage
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

        v0.10 · C91 · F73/F76/N35（任务 T114）— 注入 ``commands`` 注册中心后走
        「parse → lookup → handler(self, args)」路径：handler 以本 REPL（实现
        :class:`~wentian.commands.context.CommandContext` 协议）为 ctx 调用，返回
        真值即退出。``commands`` 为 None 时回退 v0.9 硬编码 dict 分发（逐字不动、
        回归安全）。命令本地可信——分发不进 AgentLoop / 权限门。
        """
        if self._commands is not None:
            from wentian.commands.parser import parse

            parsed = parse(line)
            if parsed is None:
                self._console.print(
                    "[yellow]请输入命令名，输入 /help 查看帮助[/yellow]"
                )
                return False
            spec = self._commands.lookup(parsed.name)
            if spec is None:
                self._console.print(
                    f"[yellow]未知命令：/{parsed.name}  输入 /help 查看帮助[/yellow]"
                )
                return False
            result = spec.handler(self, parsed.args)
            return bool(result)

        # ----------------------------------------------------------------
        # commands=None → v0.9 硬编码 dict 分发（逐字保留、回归路径）。
        # ----------------------------------------------------------------
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
            "/compact": self._cmd_compact,
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
        """legacy 薄壳：复用 :meth:`new_session`（注册中心路径走 _h_session new）。"""
        self.new_session()

    def _cmd_sessions(self, args: str) -> None:
        """legacy 薄壳：复用 :meth:`list_sessions`（``--all`` 跨分区）。"""
        self.list_sessions(all_projects=args.strip() == "--all")

    def _cmd_resume(self, args: str) -> None:
        """legacy 薄壳：复用 :meth:`resume_session`（裸 /resume 给用法提示）。"""
        sid = args.strip()
        if not sid:
            self._console.print("[yellow]用法：/resume <id>[/yellow]")
            return
        self.resume_session(sid)

    def _cmd_provider(self, args: str) -> None:
        """legacy 薄壳：复用 :meth:`switch_provider`（裸 /provider 给用法提示）。"""
        name = args.strip()
        if not name:
            self._console.print("[yellow]用法：/provider <名称>[/yellow]")
            return
        self.switch_provider(name)

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

    def _cmd_compact(self, args: str) -> None:
        """legacy 薄壳：复用 :meth:`compact_now` 取汇报串后打印。"""
        self._console.print(f"[dim]{self.compact_now()}[/dim]")

    def _cmd_exit(self, args: str) -> bool:
        return True

    # ------------------------------------------------------------------
    # v0.10 · C91 · F76（任务 T114）— 老命令归并的共享逻辑（ctx 方法）
    #
    # 既有 _cmd_new/_cmd_sessions/_cmd_resume/_cmd_provider/_cmd_compact 的逻辑
    # 迁进这里；legacy 薄壳与注册中心 handler 共享同一份实现，零重复。打印职责：
    # builtins 的 _h_session new/resume、_h_provider 是纯路由不打印确认 → 这里的
    # new_session/resume_session/switch_provider 自己打印动态确认/错误；_h_compact
    # 负责打印 → compact_now 只返回汇报串。
    # ------------------------------------------------------------------

    def new_session(self) -> None:
        """创建并切换到新会话，打印新 id 确认（沿用旧 _cmd_new 文案）。"""
        self._session = self._store.create(provider=self._provider.name)
        self._update_compactor_session()
        self._reset_persist_cursor()
        # v0.11 · C104 · F73/F87（任务 T134a）— 新会话清空 Skill 激活集（duck-typed）。
        if self._activator is not None:
            self._activator.clear()
        self._console.print(f"[green]新会话已创建：{self._session.id}[/green]")

    def list_sessions(self, *, all_projects: bool) -> None:
        """列举已保存会话（旧 _cmd_sessions 的打印逻辑；``all_projects`` 跨分区）。"""
        sessions = self._store.list(all_projects=all_projects)
        if not sessions:
            self._console.print("[dim]暂无保存的会话[/dim]")
            return
        for sid, updated_at, summary in sessions:
            preview = f"  {summary[:40]}" if summary else ""
            self._console.print(f"  {sid}  {updated_at}{preview}")

    def resume_session(self, sid: str) -> None:
        """按 id 恢复历史会话，打印确认/找不到（沿用旧 _cmd_resume 文案）。"""
        try:
            self._session = self._store.load(sid)
            self._update_compactor_session()
            self._reset_persist_cursor()
            self._console.print(f"[green]已恢复会话：{sid}[/green]")
        except FileNotFoundError:
            self._console.print(f"[red]找不到会话：{sid}[/red]")

    def switch_provider(self, name: str) -> None:
        """按名称切换 provider，打印切换成功/失败（沿用旧 _cmd_provider 文案）。"""
        try:
            new_provider = self._provider_factory(name)
            self._provider = new_provider
            self._session.provider = new_provider.name
            self._store.save(self._session)
            self._update_compactor_provider(new_provider)
            self._console.print(f"[green]已切换 provider：{name}[/green]")
        except Exception as exc:  # noqa: BLE001 — provider_factory 可抛任意错。
            self._console.print(f"[red]切换 provider 失败：{exc}[/red]")

    def compact_now(self) -> str:
        """v0.8 · C52 · F61/F62（任务 T96）— 手动触发一次重量压缩，返回可读汇报串。

        以 ``manual=True`` 调压缩器（无视熔断强制重试、收窄余量、更激进），随后
        补一次显式 :meth:`SessionStore.save`（压缩原地改写了 ``session.messages``），
        最后**返回** :class:`CompactionResult` 的可读汇报（打印由调用方负责）。
        未注入压缩器时返回友好不可用提示（compactor=None ⇒ v0.7 行为）。
        """
        if self._compactor is None:
            return "/compact 不可用：未启用上下文压缩"
        result = self._compactor.compact(
            self._session.messages, self._last_round_usage, manual=True
        )
        self._store.save(self._session)
        return _format_compaction_report(result)

    # ------------------------------------------------------------------
    # v0.8 · C52 · F61/F62（任务 T96）— 压缩器随 provider/session 切换更新
    # ------------------------------------------------------------------

    def _update_compactor_provider(self, new_provider) -> None:
        """切 provider 后同步压缩器的后端与窗口（compactor=None ⇒ no-op）。

        新窗口优先取新 provider 暴露的 ``context_window``（duck-typed），否则
        沿用压缩器当前窗口（REPL 不持有 config，无法重算默认窗口；保守保持）。
        """
        if self._compactor is None:
            return
        window = getattr(new_provider, "context_window", None)
        if not isinstance(window, int) or window <= 0:
            window = getattr(self._compactor, "context_window", 0) or 0
        self._compactor.set_provider(new_provider, window)

    def _update_compactor_session(self) -> None:
        """切 session（/new、/resume）后把压缩器的产物目录指向新会话。

        产物目录 = ``<sessions_dir>/<session_id>.artifacts/``（与 build_app 装配
        时一致）。compactor=None ⇒ no-op。
        """
        if self._compactor is None:
            return
        sessions_dir = self._store._path(self._session.id).parent
        artifacts_dir = sessions_dir / f"{self._session.id}.artifacts"
        self._compactor.set_artifacts_dir(artifacts_dir)

    # ------------------------------------------------------------------
    # v0.9 · C54 · F64（任务 T106）— 追加写持久化
    # ------------------------------------------------------------------

    @staticmethod
    def _fingerprint(messages: list[Message]) -> list[int]:
        """Per-message content fingerprint of the already-persisted prefix.

        Cheap ``hash`` of each message's ``content`` (coerced to str so non-str
        tool payloads are covered). Used to detect an **in-place rewrite of the
        persisted prefix** — e.g. v0.8 offload shrinking a tool result's content
        below the cursor without changing ``len(messages)`` (the pure-append
        path can't see that; the fingerprint can).
        """
        return [hash(str(m.get("content", ""))) for m in messages]

    def _persist_pending(self) -> None:
        """Append messages beyond the persisted cursor to the session JSONL.

        交付 F64「追加、崩溃只丢最后一行」：常态走 ``store.append`` 增量写。
        两种「已落盘前缀失效」情形退回一次原子全量 ``store.save`` 并重置游标 +
        指纹（正确性优先）：

        - **缩短**：压缩把 ``session.messages`` 改短（``n < cursor``）；
        - **原地改写**（v0.9 review fix · Major #1）：offload 把游标**之下**已落盘
          消息的 content 原地替换为预览（列表长度不变、追加路径侦测不到）——靠
          已落盘前缀的内容指纹比对发现，变了即全量重写，否则磁盘留旧原文、恢复
          时整段回灌、offload 失效。

        常态（纯追加、前缀指纹不变）仍走 ``store.append`` 增量写。
        """
        n = len(self._session.messages)
        prefix_len = min(n, self._persisted_count)
        prefix_fingerprint = self._fingerprint(self._session.messages[:prefix_len])
        prefix_rewritten = (
            prefix_fingerprint != self._persisted_fingerprint[:prefix_len]
        )

        if n < self._persisted_count or prefix_rewritten:
            # History was rewritten in place / shrank → full atomic rewrite.
            self._store.save(self._session)
            self._persisted_count = len(self._session.messages)
            self._persisted_fingerprint = self._fingerprint(self._session.messages)
            return
        new = self._session.messages[self._persisted_count :]
        if not new:
            return
        self._store.append(self._session, new)
        self._persisted_count = n
        self._persisted_fingerprint = self._fingerprint(self._session.messages)

    def _reset_persist_cursor(self) -> None:
        """Switch to a new/resumed session: cursor + fingerprint = on-disk state."""
        self._persisted_count = len(self._session.messages)
        self._persisted_fingerprint = self._fingerprint(
            self._session.messages[: self._persisted_count]
        )

    # ------------------------------------------------------------------
    # v0.2 · C2 · F16（任务 T17）— bottom toolbar 状态行数据源
    # ------------------------------------------------------------------

    def status_line(self) -> str:
        """v0.2 · C2 · F16（任务 T17）— bottom toolbar 状态行数据源。

        v0.6 · C38 · F47（任务 T78）— **首段由 provider:model 改为当前权限模式**：
        占据原 provider 名的位置、**不再展示 provider 名**（AC49）。读 live
        self._mode / self._session，Shift+Tab、/new、/resume 后下一次工具栏重算
        自动反映，无需额外通知。

        v0.10 · C91 · F74/AC88（任务 T114）— 模式标记改括号式 ``[{mode.name}]``
        （``[DEFAULT]`` / ``[ACCEPT_EDITS]`` / ``[PLAN]`` / ``[BYPASS]``）；其余段
        （会话 id、消息数、`` │ 计划模式`` 后缀）保持不变。

        v0.4 · C19 · F33（任务 T56）— 计划模式时追加 `` │ 计划模式``：plan 档已在
        首段以 ``plan`` 显示，此后缀保留为冗余的中文提示（F33 既有 status_line
        行为不变；plan-mode 回归断言「计划模式」字样照旧命中）。
        """
        n = len(self._session.messages)
        line = f"[{self._mode.name}] │ 会话 {self._session.id} │ {n} 条消息"
        if self._plan_mode:
            line += " │ 计划模式"
        return line
