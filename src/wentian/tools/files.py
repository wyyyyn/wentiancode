"""v0.3 · C9 · F20/F25（任务 T32/T33）
v0.6 · C35 · F43/F45（任务 T75）— each tool declares category / friendly_name /
path_args; requires_confirmation is now derived from category in the base class.

File-manipulation tools: ReadFileTool, WriteFileTool, EditFileTool.

Stdlib-only: no third-party imports (spec N6/N7).
Tools take a ``root: Path`` constructor argument; relative paths in tool
arguments resolve against it, absolute paths pass through unchanged.
"""

from __future__ import annotations

from pathlib import Path

from wentian.permissions.decision import Category
from wentian.tools.base import Tool, ToolError

__all__ = ["ReadFileTool", "WriteFileTool", "EditFileTool"]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_LINES = 2000
_MAX_BYTES = 50 * 1024  # 50 KB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve(root: Path, raw: object) -> Path:
    """Validate *raw* is a non-empty string, return resolved Path."""
    if not isinstance(raw, str):
        raise ToolError(f"'path' must be a string, got {type(raw).__name__!r}.")
    p = Path(raw)
    return p if p.is_absolute() else root / p


def _require_str(args: dict, key: str) -> str:
    """Return args[key] as str or raise ToolError."""
    if key not in args:
        raise ToolError(f"Missing required parameter: '{key}'.")
    val = args[key]
    if not isinstance(val, str):
        raise ToolError(
            f"Parameter '{key}' must be a string, got {type(val).__name__!r}."
        )
    return val


# ===========================================================================
# ReadFileTool
# ===========================================================================


class ReadFileTool(Tool):
    """Read the contents of a file, with optional line-range slicing.

    Returns the file contents as a string.  Files longer than 2000 lines (or
    larger than 50 KB) are truncated; a notice is appended indicating total
    line count so the model can request further slices with offset/limit.
    """

    name = "read_file"
    description = (
        "Read the contents of a file at the given path. "
        "Use offset (1-based) and limit to read a slice of a large file. "
        "Files are capped at 2000 lines / 50 KB; a truncation notice is "
        "appended when the cap is hit."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to read. Relative paths are resolved against the working root.",
            },
            "offset": {
                "type": "integer",
                "description": "1-based line number to start reading from (inclusive). Defaults to 1.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of lines to return. Defaults to 2000.",
            },
        },
        "required": ["path"],
    }
    # v0.6 · C35 · F43/F45（任务 T75）
    category = Category.READ_ONLY
    friendly_name = "Read"
    path_args = ("path",)

    def __init__(self, root: Path) -> None:
        self._root = root

    def run(self, args: dict) -> str:
        # --- parameter validation ---
        if "path" not in args:
            raise ToolError("Missing required parameter: 'path'.")
        raw_path = args["path"]
        if not isinstance(raw_path, str):
            raise ToolError(
                f"'path' must be a string, got {type(raw_path).__name__!r}."
            )

        path = _resolve(self._root, raw_path)

        # --- existence / type checks ---
        if not path.exists():
            raise ToolError(f"File not found: {path}")
        if path.is_dir():
            raise ToolError(f"Path is a directory, not a file: {path}")

        # --- offset / limit ---
        offset: int = args.get("offset", 1)
        limit: int = args.get("limit", _MAX_LINES)

        # Read raw bytes first to check size cap
        raw_bytes = path.read_bytes()
        text = raw_bytes.decode("utf-8", errors="replace")
        all_lines = text.splitlines(keepends=True)
        total_lines = len(all_lines)

        # Clamp offset to valid range (1-based → 0-based index)
        start_idx = max(0, offset - 1)
        end_idx = start_idx + limit  # exclusive

        selected = all_lines[start_idx:end_idx]

        # Apply hard caps
        truncated_by_lines = False
        if len(selected) > _MAX_LINES:
            selected = selected[:_MAX_LINES]
            truncated_by_lines = True
        else:
            # Even if we didn't need to cut selected[], the file may have more
            # lines beyond our window (total available > what we returned).
            available = total_lines - start_idx
            truncated_by_lines = available > len(selected)

        result = "".join(selected)

        truncated_by_bytes = False
        if len(result.encode("utf-8", errors="replace")) > _MAX_BYTES:
            # Truncate to 50 KB
            encoded = result.encode("utf-8", errors="replace")[:_MAX_BYTES]
            result = encoded.decode("utf-8", errors="replace")
            truncated_by_bytes = True

        if truncated_by_lines or truncated_by_bytes:
            result += (
                f"\n[Truncated: showing up to {_MAX_LINES} lines. "
                f"File has {total_lines} lines total. "
                "Use offset/limit to read further sections.]"
            )

        return result


# ===========================================================================
# WriteFileTool
# ===========================================================================


class WriteFileTool(Tool):
    """Write content to a file, creating parent directories as needed.

    Overwrites the file if it already exists.  Returns a success message
    with the file path and byte count written.
    """

    name = "write_file"
    description = (
        "Write content to a file at the given path. "
        "Creates parent directories automatically. "
        "Overwrites the file if it already exists."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Destination file path. Relative paths resolve against the working root.",
            },
            "content": {
                "type": "string",
                "description": "Text content to write to the file.",
            },
        },
        "required": ["path", "content"],
    }
    # v0.6 · C35 · F43/F45（任务 T75）
    category = Category.FILE_WRITE
    friendly_name = "Write"
    path_args = ("path",)

    def __init__(self, root: Path) -> None:
        self._root = root

    def run(self, args: dict) -> str:
        # --- parameter validation ---
        if "path" not in args:
            raise ToolError("Missing required parameter: 'path'.")
        if "content" not in args:
            raise ToolError("Missing required parameter: 'content'.")

        raw_path = args["path"]
        if not isinstance(raw_path, str):
            raise ToolError(
                f"'path' must be a string, got {type(raw_path).__name__!r}."
            )
        content = args["content"]
        if not isinstance(content, str):
            raise ToolError(
                f"'content' must be a string, got {type(content).__name__!r}."
            )

        path = _resolve(self._root, raw_path)

        # --- write ---
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = content.encode("utf-8")
        path.write_bytes(encoded)

        byte_count = len(encoded)
        return f"Wrote {byte_count} bytes to {path}"


# ===========================================================================
# EditFileTool
# ===========================================================================


class EditFileTool(Tool):
    """Replace a unique occurrence of old_string with new_string in a file.

    Raises ToolError if old_string appears zero times (not found) or more
    than once (ambiguous match).  The actual match count is included in the
    error message so the model can self-correct.

    Also raises ToolError when old_string == new_string (no-op edit) or
    when the target file does not exist.
    """

    name = "edit_file"
    description = (
        "Replace a unique occurrence of old_string with new_string in a file. "
        "Fails with a descriptive error if old_string is not found or appears "
        "multiple times — in the latter case the actual count is reported so "
        "you can refine the match."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to edit. Relative paths resolve against the working root.",
            },
            "old_string": {
                "type": "string",
                "description": "Exact string to find (must occur exactly once in the file).",
            },
            "new_string": {
                "type": "string",
                "description": "Replacement string.",
            },
        },
        "required": ["path", "old_string", "new_string"],
    }
    # v0.6 · C35 · F43/F45（任务 T75）
    category = Category.FILE_WRITE
    friendly_name = "Edit"
    path_args = ("path",)

    def __init__(self, root: Path) -> None:
        self._root = root

    def run(self, args: dict) -> str:
        # --- parameter validation ---
        path_str = _require_str(args, "path")
        old_string = _require_str(args, "old_string")
        new_string = _require_str(args, "new_string")

        # No-op guard
        if old_string == new_string:
            raise ToolError(
                "old_string and new_string are identical — no edit would be made."
            )

        path = _resolve(self._root, path_str)

        # --- existence check ---
        if not path.exists():
            raise ToolError(f"File not found: {path}")
        if path.is_dir():
            raise ToolError(f"Path is a directory, not a file: {path}")

        content = path.read_text(encoding="utf-8", errors="replace")

        count = content.count(old_string)
        if count != 1:
            raise ToolError(
                f"Expected exactly 1 occurrence of old_string in {path}, "
                f"but found {count}. "
                "Refine old_string to match exactly one location."
            )

        new_content = content.replace(old_string, new_string, 1)
        path.write_text(new_content, encoding="utf-8")

        return f"Edited {path}: replaced 1 occurrence."
