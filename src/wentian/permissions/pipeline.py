"""v0.6 · C34 · F46（任务 T74）

Five-layer permission pipeline orchestration — the agent-orchestration-layer
glue that turns the four pure leaf modules (blacklist / sandbox / rules / mode
fallback) into a single short-circuiting decision for one tool call.

:meth:`PermissionPipeline.decide` runs, *before* a tool actually executes:

1. **黑名单**（仅命令执行类，N10 不可关）：``check_command`` 命中 → 立即
   ``Decision(DENY, BLACKLIST)`` 短路返回；非命令类**跳过**本层。
2. **沙箱**（仅文件类 read-only / file-write）：逐个 path ``check_path``，任一
   逃逸项目根 → 立即 ``Decision(DENY, SANDBOX)`` 短路返回；命令类**跳过**本层。
3. **规则引擎**：``target = command``（命令类）或项目相对路径（文件类）；
   ``settings.rules.match`` → ALLOW→``Decision(ALLOW, RULE)``、DENY→
   ``Decision(DENY, RULE)``、None→继续。
4. **模式兜底**：``mode_fallback(mode, category)`` → ``Decision(ALLOW, MODE)`` 或
   ``Decision(ASK, MODE)``（ASK 交给第 5 层人在回路门，由调用方处理）。

任一层给出 Allow/Deny 即短路；被跳过的层视为「未拦、继续下一层」（F46/AC48）。
模式兜底层值域严格 {ALLOW, ASK}——Deny 只可能来自黑名单 / 沙箱 / 显式 deny
规则 / 人在回路（F46）。

**安全默认（N16/AC55）：** ``decide`` 的入参 ``category`` 由调用方判定；但若调用
方声明为命令执行类却给不出可解析的 ``command``（``None`` / 空白），本层**绝不
静默放行**——既不进黑名单（无串可查）、也不查规则（无 target 可匹配），而是按
有副作用工具走模式兜底（``ASK`` 或在 bypass 档下仍兜回 ``ASK`` 而非 ``ALLOW``）。
``decide`` 恒返回 ``Decision``（三态之一），绝不返回 ``None`` / 静默。

分层铁律: 只 import stdlib 与 :mod:`wentian.permissions.*` 同包模块——不碰任何
backend SDK / 终端 UI / provider / agent / tools。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wentian.permissions.blacklist import check_command
from wentian.permissions.decision import (
    Category,
    Decision,
    Mode,
    Source,
    Verdict,
)
from wentian.permissions.modes import mode_fallback
from wentian.permissions.sandbox import check_path
from wentian.permissions.settings import Settings

__all__ = ["PermissionPipeline"]

#: 文件类工具（走沙箱 + 路径 target 规则匹配）。
_FILE_CATEGORIES = (Category.READ_ONLY, Category.FILE_WRITE)


@dataclass
class PermissionPipeline:
    """五层判定流水线（层 1-4 在此短路编排；层 5 人在回路留给门）。"""

    project_root: Path
    settings: Settings

    def decide(
        self,
        *,
        friendly: str,
        category: Category,
        mode: Mode,
        command: str | None,
        paths: tuple[str, ...],
    ) -> Decision:
        """对单次工具调用给出 Allow / Deny / Ask 判定（逐层短路，见模块文档）。"""
        is_command = category is Category.COMMAND_EXEC
        has_command = bool(command and command.strip())

        # --- 层 1：黑名单（仅命令执行类；无可解析命令则跳过——交安全默认兜底）---
        if is_command and has_command:
            hit = check_command(command)  # type: ignore[arg-type]
            if hit is not None:
                return hit

        # --- 层 2：沙箱（仅文件类；逐个 path 逃逸即短路 Deny）---
        if category in _FILE_CATEGORIES:
            for path in paths:
                hit = check_path(path, self.project_root)
                if hit is not None:
                    return hit

        # --- 层 3：规则引擎 ---
        #
        # 安全默认（N16）：命令执行类但 command 不可解析时**不查规则**——没有可信
        # target 可匹配，绝不让 allow-all 规则把不可解析的命令放行；直接落兜底。
        if not (is_command and not has_command):
            rule_decision = self._match_rules(
                friendly=friendly,
                is_command=is_command,
                command=command,
                paths=paths,
            )
            if rule_decision is not None:
                return rule_decision

        # --- 层 4：模式兜底（值域严格 {ALLOW, ASK}）---
        #
        # 安全默认（N16）：命令执行类但 command 不可解析时，按有副作用处理——绝不
        # 静默 ALLOW（即便 bypass 档也兜回 ASK 交人在回路）。
        if is_command and not has_command:
            return Decision(
                verdict=Verdict.ASK,
                source=Source.MODE,
                reason="无法解析待执行命令，出于安全默认按有副作用工具处理，需人工确认。",
            )

        verdict = mode_fallback(mode, category)
        return Decision(verdict=verdict, source=Source.MODE)

    # ------------------------------------------------------------------
    # 层 3 内部：构造 target 并裁决（命令串 / 文件相对路径）。
    # ------------------------------------------------------------------
    def _match_rules(
        self,
        *,
        friendly: str,
        is_command: bool,
        command: str | None,
        paths: tuple[str, ...],
    ) -> Decision | None:
        """规则层裁决：命中 ALLOW/DENY → Decision；未命中 → None。"""
        if is_command:
            target = command or ""
            verdict = self.settings.rules.match(
                friendly=friendly, target=target, is_path=False
            )
            return self._rule_verdict_to_decision(verdict, target=target)

        # 文件类：逐个 path 用项目相对路径作 target；deny 优先（同层 RuleSet 已保证
        # deny 先于 allow），任一 path 命中即返回。
        allow_hit: Decision | None = None
        for path in paths:
            target = self._project_relative(path)
            verdict = self.settings.rules.match(
                friendly=friendly, target=target, is_path=True
            )
            if verdict is Verdict.DENY:
                return self._rule_verdict_to_decision(verdict, target=target)
            if verdict is Verdict.ALLOW and allow_hit is None:
                allow_hit = self._rule_verdict_to_decision(verdict, target=target)
        return allow_hit

    @staticmethod
    def _rule_verdict_to_decision(
        verdict: Verdict | None, *, target: str
    ) -> Decision | None:
        if verdict is Verdict.ALLOW:
            return Decision(verdict=Verdict.ALLOW, source=Source.RULE)
        if verdict is Verdict.DENY:
            return Decision(
                verdict=Verdict.DENY,
                source=Source.RULE,
                reason=f"目标 {target!r} 被显式 deny 规则拦截。",
            )
        return None

    def _project_relative(self, path: str) -> str:
        """把 path 规整为相对项目根的 POSIX 路径（用于规则 glob 匹配）。

        逃逸 / 无法相对化的路径返回原始绝对路径字符串——此时其早已被沙箱层
        Deny 短路，不会走到规则层；这里仅为健壮性兜底，绝不放权。
        """
        target = Path(path)
        if not target.is_absolute():
            target = self.project_root / target
        root = self.project_root.resolve()
        resolved = target.resolve() if target.exists() else target
        try:
            return resolved.relative_to(root).as_posix()
        except ValueError:
            return str(resolved)
