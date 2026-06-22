"""v0.12 · C97 · F81/F82/F83（任务 T121）— 动作执行器。
v0.13 · C117 · F102（任务 T146）— SubAgentAction 接通 HookEngine（真起后台子 Agent）。

四动作执行函数，各自**失败软化**：捕获所有异常 / 超时 → 返回结构化结果或 None，
绝不向调用方冒泡。

分层铁律（N41/N43）：
  仅 import stdlib（subprocess / urllib.request / urllib.error /
  json / os / logging / dataclasses）+ 同包 spec 模块 + agents.spec（纯 stdlib 叶子）。
  零 rich / prompt_toolkit / provider / tools / repl / cli 依赖。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass

from wentian.agents.spec import AgentDef, AgentType
from wentian.hooks.spec import HttpAction, PromptAction, ShellAction, SubAgentAction

__all__ = [
    "ShellResult",
    "SafeDict",
    "run_shell",
    "inject_prompt",
    "call_http",
    "run_subagent_action",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 结果类型
# ---------------------------------------------------------------------------


@dataclass
class ShellResult:
    """run_shell 的结构化结果。"""

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


# ---------------------------------------------------------------------------
# 工具类
# ---------------------------------------------------------------------------


class SafeDict(dict):
    """format_map 用——缺键时保留字面 {key}，不抛 KeyError。"""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


# ---------------------------------------------------------------------------
# 私有辅助
# ---------------------------------------------------------------------------


def _build_env(context: dict) -> dict:
    """构造 env：os.environ + WENTIAN_HOOK_<KEY> 简单标量值。"""
    extra = {
        f"WENTIAN_HOOK_{k.upper()}": str(v)
        for k, v in context.items()
        if isinstance(v, (str, int, float, bool))
    }
    return {**os.environ, **extra}


# ---------------------------------------------------------------------------
# 四动作执行器
# ---------------------------------------------------------------------------


def run_shell(action: ShellAction, context: dict) -> ShellResult | None:
    """执行 shell 命令。

    - stdin 注入 ``json.dumps(context)``
    - env 注入所有简单标量值为 WENTIAN_HOOK_<KEY>
    - 超时 → 返回 timed_out=True 的 ShellResult（不抛）
    - 其他异常 → 返回 None（软化）
    """
    try:
        proc = subprocess.run(
            action.command,
            shell=True,
            input=json.dumps(context),
            env=_build_env(context),
            timeout=action.timeout,
            capture_output=True,
            text=True,
        )
        return ShellResult(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            timed_out=False,
        )
    except subprocess.TimeoutExpired:
        return ShellResult(exit_code=-1, stdout="", stderr="", timed_out=True)
    except Exception as exc:  # noqa: BLE001
        logger.debug("run_shell: caught exception (softened): %s", exc)
        return None


def inject_prompt(action: PromptAction, context: dict) -> str:
    """把 action.text 中的 {field} 占位符替换为 context 中的值。

    缺键时保留字面（SafeDict 语义）；内部异常 → 返回原始 text（软化）。
    """
    try:
        return action.text.format_map(SafeDict(context))
    except Exception as exc:  # noqa: BLE001
        logger.debug("inject_prompt: caught exception (softened): %s", exc)
        return action.text


def call_http(action: HttpAction, context: dict) -> int | None:
    """向 action.url 发送 HTTP 请求，body 为 json.dumps(context)。

    返回 HTTP 状态码；网络 / URL 异常 → 返回 None（软化）。
    """
    try:
        data = json.dumps(context).encode("utf-8")
        req = urllib.request.Request(
            action.url,
            data=data,
            method=action.method,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=action.timeout) as resp:
            return resp.status
    except Exception as exc:  # noqa: BLE001
        logger.debug("call_http: caught exception (softened): %s", exc)
        return None


def run_subagent_action(
    action: SubAgentAction,
    context: dict,  # noqa: ARG001
    *,
    manager: object | None = None,
) -> str:
    """子 Agent 动作执行器（v0.13 · T146 · F102）。

    - manager=None → 记「subagent 未启用（manager 未装配）」日志，返回占位结果（v0.12 向后兼容，不抛）。
    - manager 已装配 → 构造 AgentDef，调用 manager.submit(fire-and-forget)，返回含 id=<task_id> 的结果串。
    - 全程 try/except → 日志 + 返回「失败」结果串，绝不向调用方冒泡（N54）。
    """
    try:
        if manager is None:
            logger.info(
                "run_subagent_action: subagent 未启用（manager 未装配）; prompt=%r",
                action.prompt,
            )
            return "subagent_result:status=skipped,reason=manager_not_configured"

        agent_def = AgentDef(
            name="hook-subagent",
            description="hook 触发的子 Agent",
            body="",
        )
        task_id: str = manager.submit(  # type: ignore[union-attr]
            agent_def,
            action.prompt,
            background=True,
            agent_type=AgentType.DEFINITION,
        )
        logger.info(
            "run_subagent_action: 后台子 Agent 已提交; id=%s prompt=%r",
            task_id,
            action.prompt,
        )
        return f"subagent_result:status=submitted,id={task_id}"

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "run_subagent_action: 失败（软化）: %s; prompt=%r",
            exc,
            action.prompt,
        )
        return f"subagent_result:status=失败,error={exc!r}"
