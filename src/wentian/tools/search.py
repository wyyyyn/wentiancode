"""v0.3 · C9 · F20（任务 T35）

FindFilesTool and SearchTextTool — read-only filesystem search tools.

Both tools:
- Accept ``root: Path`` in their constructor.
- Prune directories named ``.git``, ``.venv``, ``node_modules``,
  ``__pycache__``, and any hidden directory (name starts with ``.``).
- Are read-only, so ``requires_confirmation = False`` (the default).
- Are stdlib-only.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Generator

from wentian.tools.base import Tool, ToolError

__all__ = ["FindFilesTool", "SearchTextTool"]

# Directories to skip when traversing the project tree.
_SKIP_DIRS: frozenset[str] = frozenset(
    {".git", ".venv", "node_modules", "__pycache__"}
)

# Maximum number of results before truncation.
_MAX_RESULTS = 200

# Maximum display length for a single matched line.
_MAX_LINE_DISPLAY = 200


def _should_skip_dir(name: str) -> bool:
    """Return True if a directory should be excluded from traversal."""
    return name in _SKIP_DIRS or name.startswith(".")


def _walk_files(root: Path) -> Generator[Path, None, None]:
    """Yield all files under *root*, pruning skip directories."""
    for path in root.iterdir():
        if path.is_dir():
            if not _should_skip_dir(path.name):
                yield from _walk_files(path)
        elif path.is_file():
            yield path


def _is_binary(path: Path, chunk_size: int = 8192) -> bool:
    """Return True if *path* appears to be a binary file (contains a null byte)."""
    try:
        chunk = path.read_bytes()[:chunk_size]
        return b"\x00" in chunk
    except OSError:
        return True


# ---------------------------------------------------------------------------
# FindFilesTool
# ---------------------------------------------------------------------------

class FindFilesTool(Tool):
    """Glob for files under the project root, excluding noisy directories."""

    name = "find_files"
    description = (
        "Search for files matching a glob pattern under the project root. "
        "Skips .git, .venv, node_modules, __pycache__, and hidden directories. "
        "Returns sorted relative paths, capped at 200 results."
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": (
                    "Glob pattern relative to the project root, e.g. '**/*.py'."
                ),
            },
        },
        "required": ["pattern"],
    }

    def __init__(self, root: Path) -> None:
        self._root = root

    def run(self, args: dict) -> str:
        pattern: str | None = args.get("pattern")
        if not pattern:
            raise ToolError("Missing required parameter: pattern")

        # Collect all glob matches, then filter out paths inside skipped dirs.
        try:
            raw_matches = list(self._root.glob(pattern))
        except (ValueError, OSError) as exc:
            raise ToolError(f"Invalid glob pattern: {exc}") from exc

        # Filter: keep only files (not dirs), and exclude skipped directories.
        matches: list[str] = []
        for path in raw_matches:
            if not path.is_file():
                continue
            # Check every parent component between root and path.
            try:
                rel = path.relative_to(self._root)
            except ValueError:
                continue
            parts = rel.parts
            # parts[-1] is the file name; parts[:-1] are parent dirs.
            if any(_should_skip_dir(part) for part in parts[:-1]):
                continue
            matches.append(str(rel))

        matches.sort()

        if not matches:
            return "no matches"

        truncated = len(matches) > _MAX_RESULTS
        matches = matches[:_MAX_RESULTS]

        output = "\n".join(matches)
        if truncated:
            output += f"\n(truncated to {_MAX_RESULTS} results; refine your pattern)"
        return output


# ---------------------------------------------------------------------------
# SearchTextTool
# ---------------------------------------------------------------------------

class SearchTextTool(Tool):
    """Search file contents with a regular expression under the project root."""

    name = "search_text"
    description = (
        "Search file contents for lines matching a regular expression. "
        "Outputs matches as 'relative_path:line_number:line_content'. "
        "Skips binary files, .git, .venv, node_modules, __pycache__, and hidden dirs. "
        "Optionally restrict to files matching a glob via the 'glob' parameter."
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regular expression to search for.",
            },
            "glob": {
                "type": "string",
                "description": (
                    "Optional glob pattern to restrict which files are searched, "
                    "e.g. '**/*.py'. Defaults to all files."
                ),
            },
        },
        "required": ["pattern"],
    }

    def __init__(self, root: Path) -> None:
        self._root = root

    def run(self, args: dict) -> str:
        pattern: str | None = args.get("pattern")
        if not pattern:
            raise ToolError("Missing required parameter: pattern")

        glob_filter: str | None = args.get("glob")

        # Compile regex early to surface errors before any I/O.
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise ToolError(f"Invalid regex pattern: {exc}") from exc

        # Determine candidate files.
        if glob_filter:
            try:
                candidate_paths = list(self._root.glob(glob_filter))
            except (ValueError, OSError) as exc:
                raise ToolError(f"Invalid glob filter: {exc}") from exc
            # Filter out skipped dirs from glob results too.
            files: list[Path] = []
            for path in candidate_paths:
                if not path.is_file():
                    continue
                try:
                    rel = path.relative_to(self._root)
                except ValueError:
                    continue
                if any(_should_skip_dir(part) for part in rel.parts[:-1]):
                    continue
                files.append(path)
        else:
            files = list(_walk_files(self._root))

        # Sort for deterministic output.
        files.sort()

        results: list[str] = []
        truncated = False

        for file_path in files:
            if _is_binary(file_path):
                continue

            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            try:
                rel_str = str(file_path.relative_to(self._root))
            except ValueError:
                rel_str = str(file_path)

            for lineno, raw_line in enumerate(text.splitlines(), start=1):
                line = raw_line.rstrip()
                if regex.search(line):
                    display = line if len(line) <= _MAX_LINE_DISPLAY else line[:_MAX_LINE_DISPLAY] + "..."
                    results.append(f"{rel_str}:{lineno}:{display}")
                    if len(results) >= _MAX_RESULTS:
                        truncated = True
                        break
            if truncated:
                break

        if not results:
            return ""

        output = "\n".join(results)
        if truncated:
            output += f"\n(truncated to {_MAX_RESULTS} matches; refine your pattern)"
        return output
