"""v0.6 · C34 · F46（任务 T74）

RED-first tests for the five-layer permission pipeline orchestration
(:class:`PermissionPipeline.decide`). These assert the *behaviour* of the
short-circuit + skip-layer semantics demanded by F46 / AC48 / AC55 / N16:

* 黑名单命中 → 短路返回，不再进沙箱/规则/模式（source=BLACKLIST）；
* deny 规则命中 → 不进模式兜底；allow 规则命中 → 直接放行不进模式兜底；
* 跳层不误拦——非命令类不被黑名单拦、命令类不被沙箱拦，均继续进后续层；
* 规则未命中 → 落模式兜底，返回该格 Allow/Ask；
* 安全默认（N16/AC55）——命令类但 command 不可解析（None/空）时不静默放行，
  按有副作用处理走 Ask（绝不 ALLOW，即便 bypass 档）。

层 1-4 全纯函数；用真实 leaf 模块 + 在 tmp_path 下写真实路径构造沙箱场景，
规则层用真实 ``Settings``/``LayeredRules``/``RuleSet`` 装配。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wentian.permissions.decision import (
    Category,
    Decision,
    Mode,
    Source,
    Verdict,
)
from wentian.permissions.pipeline import PermissionPipeline
from wentian.permissions.rules import LayeredRules, Rule, RuleSet
from wentian.permissions.settings import Settings


# ---------------------------------------------------------------------------
# Helpers — assemble a pipeline with explicit rules / mode.
# ---------------------------------------------------------------------------


def _settings(
    *,
    allow: list[Rule] | None = None,
    deny: list[Rule] | None = None,
    default_mode: Mode = Mode.DEFAULT,
) -> Settings:
    """Build a real ``Settings`` whose local layer carries the given rules."""
    local = RuleSet(allow=list(allow or []), deny=list(deny or []))
    return Settings(rules=LayeredRules(local=local), default_mode=default_mode)


def _pipeline(root: Path, settings: Settings | None = None) -> PermissionPipeline:
    return PermissionPipeline(project_root=root, settings=settings or _settings())


# ---------------------------------------------------------------------------
# RED 1 — blacklist hit short-circuits (never reaches sandbox / rules / mode).
# ---------------------------------------------------------------------------


def test_blacklist_hit_short_circuits(tmp_path: Path) -> None:
    """命令类命中黑名单 → 立即 Deny(BLACKLIST)，即便存在 allow 规则 / bypass 档也不放行。"""
    # An allow-all Bash rule + bypass mode would otherwise ALLOW everything;
    # the blacklist must still win and short-circuit.
    settings = _settings(
        allow=[Rule(friendly="Bash", pattern=None, effect=Verdict.ALLOW)],
        default_mode=Mode.BYPASS,
    )
    pipe = _pipeline(tmp_path, settings)

    decision = pipe.decide(
        friendly="Bash",
        category=Category.COMMAND_EXEC,
        mode=Mode.BYPASS,
        command="rm -rf /",
        paths=(),
    )

    assert decision.verdict is Verdict.DENY
    assert decision.source is Source.BLACKLIST
    assert decision.reason  # 黑名单 Deny 必带可读原因


# ---------------------------------------------------------------------------
# RED 2 — rule layer short-circuits the mode fallback.
# ---------------------------------------------------------------------------


def test_deny_rule_does_not_reach_mode(tmp_path: Path) -> None:
    """deny 规则命中 → Deny(RULE)，即便 bypass 档（模式会 ALLOW）也不进兜底。"""
    settings = _settings(
        deny=[Rule(friendly="Bash", pattern="git push", effect=Verdict.DENY)],
        default_mode=Mode.BYPASS,
    )
    pipe = _pipeline(tmp_path, settings)

    decision = pipe.decide(
        friendly="Bash",
        category=Category.COMMAND_EXEC,
        mode=Mode.BYPASS,
        command="git push",
        paths=(),
    )

    assert decision.verdict is Verdict.DENY
    assert decision.source is Source.RULE
    assert decision.reason  # Deny 必带可读原因


def test_allow_rule_does_not_reach_mode(tmp_path: Path) -> None:
    """allow 规则命中 → 直接 Allow(RULE)，即便 default 档命令类本应 Ask 也不进兜底。"""
    settings = _settings(
        allow=[Rule(friendly="Bash", pattern="git status", effect=Verdict.ALLOW)],
        default_mode=Mode.DEFAULT,
    )
    pipe = _pipeline(tmp_path, settings)

    decision = pipe.decide(
        friendly="Bash",
        category=Category.COMMAND_EXEC,
        mode=Mode.DEFAULT,
        command="git status",
        paths=(),
    )

    assert decision.verdict is Verdict.ALLOW
    assert decision.source is Source.RULE


# ---------------------------------------------------------------------------
# RED 3 — skipped layers never wrongly block.
# ---------------------------------------------------------------------------


def test_non_command_skips_blacklist(tmp_path: Path) -> None:
    """非命令类（文件写）即使 command 串看似危险也跳过黑名单，继续后续层落兜底。"""
    pipe = _pipeline(tmp_path)
    inside = tmp_path / "notes.txt"

    decision = pipe.decide(
        friendly="Write",
        category=Category.FILE_WRITE,
        mode=Mode.DEFAULT,
        command="rm -rf /",  # 即便携带危险串，文件类也不该被黑名单拦
        paths=(str(inside),),
    )

    # 黑名单被跳过；沙箱内合法；规则未命中 → default 档文件写兜底 Ask（来源 MODE）
    assert decision.source is not Source.BLACKLIST
    assert decision.verdict is Verdict.ASK
    assert decision.source is Source.MODE


def test_command_skips_sandbox(tmp_path: Path) -> None:
    """命令类即使带项目外路径也跳过沙箱，继续后续层落兜底（不被沙箱误拦）。"""
    pipe = _pipeline(tmp_path)

    decision = pipe.decide(
        friendly="Bash",
        category=Category.COMMAND_EXEC,
        mode=Mode.DEFAULT,
        command="cat /etc/hosts",  # 路径在项目外，但命令类不走沙箱
        paths=("/etc/hosts",),
    )

    # 沙箱被跳过；规则未命中 → default 档命令兜底 Ask（来源 MODE，绝非 SANDBOX）
    assert decision.source is not Source.SANDBOX
    assert decision.verdict is Verdict.ASK
    assert decision.source is Source.MODE


# ---------------------------------------------------------------------------
# RED 3b — sandbox DOES catch file tools escaping the root.
# ---------------------------------------------------------------------------


def test_file_tool_outside_root_denied_by_sandbox(tmp_path: Path) -> None:
    """文件类逃逸项目根 → 沙箱 Deny(SANDBOX) 短路，不进规则/模式。"""
    root = tmp_path / "proj"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    pipe = _pipeline(root)

    decision = pipe.decide(
        friendly="Read",
        category=Category.READ_ONLY,
        mode=Mode.BYPASS,  # bypass 也救不了逃逸——沙箱在规则/模式之前
        command=None,
        paths=(str(outside),),
    )

    assert decision.verdict is Verdict.DENY
    assert decision.source is Source.SANDBOX
    assert decision.reason


# ---------------------------------------------------------------------------
# RED 4 — rule miss falls through to mode fallback (the right cell).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode, category, expected",
    [
        (Mode.DEFAULT, Category.READ_ONLY, Verdict.ALLOW),
        (Mode.DEFAULT, Category.FILE_WRITE, Verdict.ASK),
        (Mode.ACCEPT_EDITS, Category.FILE_WRITE, Verdict.ALLOW),
        (Mode.BYPASS, Category.COMMAND_EXEC, Verdict.ALLOW),
    ],
)
def test_rule_miss_falls_to_mode(
    tmp_path: Path, mode: Mode, category: Category, expected: Verdict
) -> None:
    """规则全未命中 → 落模式兜底，返回该 (模式,类别) 格的 Allow/Ask，来源 MODE。"""
    pipe = _pipeline(tmp_path)  # 空规则集
    if category is Category.COMMAND_EXEC:
        command: str | None = "echo hi"
        paths: tuple[str, ...] = ()
    else:
        command = None
        paths = (str(tmp_path / "f.txt"),)

    decision = pipe.decide(
        friendly="Bash" if category is Category.COMMAND_EXEC else "Read",
        category=category,
        mode=mode,
        command=command,
        paths=paths,
    )

    assert decision.verdict is expected
    assert decision.source is Source.MODE


# ---------------------------------------------------------------------------
# RED 5 — safety default (N16/AC55): unparseable command never silently passes.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_command", [None, "", "   "])
@pytest.mark.parametrize("mode", list(Mode))
def test_command_with_unparseable_command_never_silently_allows(
    tmp_path: Path, bad_command: str | None, mode: Mode
) -> None:
    """命令类但 command 不可解析（None/空白）→ 按副作用处理，绝不静默 ALLOW（含 bypass）。"""
    pipe = _pipeline(tmp_path)

    decision = pipe.decide(
        friendly="Bash",
        category=Category.COMMAND_EXEC,
        mode=mode,
        command=bad_command,
        paths=(),
    )

    # 安全默认：不可解析命令绝不放行；按有副作用走 Ask（或更严的 Deny）。
    assert decision.verdict is not Verdict.ALLOW
    assert decision.verdict in {Verdict.ASK, Verdict.DENY}


def test_unparseable_command_does_not_consult_allow_rule(tmp_path: Path) -> None:
    """安全默认要硬：command 不可解析时，即使有 allow-all 规则也不得被规则放行。"""
    settings = _settings(
        allow=[Rule(friendly="Bash", pattern=None, effect=Verdict.ALLOW)],
        default_mode=Mode.BYPASS,
    )
    pipe = _pipeline(tmp_path, settings)

    decision = pipe.decide(
        friendly="Bash",
        category=Category.COMMAND_EXEC,
        mode=Mode.BYPASS,
        command=None,
        paths=(),
    )

    assert decision.verdict is not Verdict.ALLOW


def test_returns_decision_instance(tmp_path: Path) -> None:
    """decide 永远返回 Decision（三态之一），绝不返回 None/静默。"""
    pipe = _pipeline(tmp_path)
    decision = pipe.decide(
        friendly="Read",
        category=Category.READ_ONLY,
        mode=Mode.DEFAULT,
        command=None,
        paths=(str(tmp_path / "x.txt"),),
    )
    assert isinstance(decision, Decision)
    assert decision.verdict in {Verdict.ALLOW, Verdict.DENY, Verdict.ASK}
