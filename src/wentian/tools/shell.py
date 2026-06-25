"""v0.3 · C9 · F20 (task T34)
v0.6 · C35 · F43/F45 (task T75) — declares category / friendly_name / command_arg.

RunCommandTool — execute shell commands with timeout and output cap.

Stdlib-only; no third-party imports.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from wentian.permissions.decision import Category
from wentian.tools.base import Tool, ToolError

__all__ = ["RunCommandTool"]

_OUTPUT_CAP = 10_000  # characters
_HALF_CAP = _OUTPUT_CAP // 2

# Minimum subprocess timeout in seconds regardless of timeout_s setting.
_MIN_PROC_TIMEOUT = 1.0


def _truncate(text: str) -> str:
    """Truncate *text* to ``_OUTPUT_CAP`` chars keeping head and tail halves.

    A plain-language marker is inserted between the two halves so the model
    knows content was omitted.
    """
    if len(text) <= _OUTPUT_CAP:
        return text
    head = text[:_HALF_CAP]
    tail = text[-_HALF_CAP:]
    omitted = len(text) - _OUTPUT_CAP
    marker = f"\n... [{omitted} chars truncated] ...\n"
    return head + marker + tail


class RunCommandTool(Tool):
    """Run an arbitrary shell command in the project root directory.

    The command is executed via ``/bin/sh -c`` so shell builtins, pipes, and
    redirects work as expected.  Both stdout and stderr are captured and
    returned in the result text so the model can reason about all output.

    Non-zero exit codes are *not* treated as errors — the exit code is
    included in the result text so the model can decide what to do next.
    """

    name = "run_command"
    description = (
        "Run a shell command in the project working directory. "
        "Returns stdout, stderr (when non-empty), and the exit code. "
        "Non-zero exit codes are reported rather than raised as errors."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command to execute (passed to /bin/sh -c).",
            }
        },
        "required": ["command"],
    }
    # v0.6 · C35 · F43/F45 (task T75)
    category = Category.COMMAND_EXEC
    friendly_name = "Bash"
    command_arg = "command"

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, args: dict) -> str:  # noqa: D102
        command = self._validate(args)
        proc_timeout = max(self.timeout_s - 5.0, _MIN_PROC_TIMEOUT)

        try:
            result = subprocess.run(
                ["/bin/sh", "-c", command],
                capture_output=True,
                text=True,
                timeout=proc_timeout,
                cwd=self._root,
            )
        except subprocess.TimeoutExpired:
            raise ToolError(
                f"Command timed out after {proc_timeout:.1f}s (timeout_s={self.timeout_s}). "
                "Timed out — the process was terminated."
            ) from None

        return self._format(result)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _validate(self, args: dict) -> str:
        if "command" not in args:
            raise ToolError("Missing required argument: 'command'.")
        command = args["command"]
        if not isinstance(command, str):
            raise ToolError(
                f"'command' must be a string, got {type(command).__name__!r}."
            )
        return command

    @staticmethod
    def _format(result: subprocess.CompletedProcess) -> str:  # type: ignore[type-arg]
        parts: list[str] = []

        stdout = _truncate(result.stdout) if result.stdout else ""
        stderr = _truncate(result.stderr) if result.stderr else ""

        if stdout:
            parts.append(f"[stdout]\n{stdout}")
        if stderr:
            parts.append(f"[stderr]\n{stderr}")

        parts.append(f"[exit code]\n{result.returncode}")

        return "\n\n".join(parts)
