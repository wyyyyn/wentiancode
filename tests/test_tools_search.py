"""v0.3 · C9 · F20（任务 T35）

Tests for FindFilesTool and SearchTextTool.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wentian.tools.search import FindFilesTool, SearchTextTool
from wentian.tools.base import ToolError


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def make_tree(root: Path) -> None:
    """Build a deterministic directory tree for testing.

    Structure:
      root/
        a.py            – contains "hello world"
        b.py            – contains "def foo(): pass"
        sub/
          c.py          – contains "import os\nhello again"
          d.txt         – contains "not python"
        .git/
          HEAD          – should be excluded
        .venv/
          site.py       – should be excluded
        node_modules/
          pkg.py        – should be excluded
        __pycache__/
          cached.py     – should be excluded
        .hidden/
          secret.py     – should be excluded
        binary_file     – contains a null byte (binary)
    """
    (root / "a.py").write_text("hello world\n")
    (root / "b.py").write_text("def foo(): pass\n")
    sub = root / "sub"
    sub.mkdir()
    (sub / "c.py").write_text("import os\nhello again\n")
    (sub / "d.txt").write_text("not python\n")

    # Directories that should be skipped
    for skip_dir in [".git", ".venv", "node_modules", "__pycache__", ".hidden"]:
        d = root / skip_dir
        d.mkdir()
        (d / "file.py").write_text("should not appear\n")

    # Binary file
    (root / "binary_file").write_bytes(b"some data\x00more data\n")


# ---------------------------------------------------------------------------
# FindFilesTool
# ---------------------------------------------------------------------------


class TestFindFilesTool:
    def test_find_py_files_returns_sorted_relative_paths(self, tmp_path):
        make_tree(tmp_path)
        tool = FindFilesTool(tmp_path)
        result = tool.run({"pattern": "**/*.py"})
        lines = [ln for ln in result.splitlines() if ln]
        # Should find a.py, b.py, sub/c.py — not files in skipped dirs
        assert "a.py" in lines
        assert "b.py" in lines
        assert "sub/c.py" in lines
        # Sorted
        assert lines == sorted(lines)

    def test_skipped_dirs_not_in_results(self, tmp_path):
        make_tree(tmp_path)
        tool = FindFilesTool(tmp_path)
        result = tool.run({"pattern": "**/*.py"})
        for skip in [".git", ".venv", "node_modules", "__pycache__", ".hidden"]:
            assert skip not in result

    def test_zero_matches_returns_no_matches_text(self, tmp_path):
        make_tree(tmp_path)
        tool = FindFilesTool(tmp_path)
        result = tool.run({"pattern": "**/*.nonexistent"})
        assert "no matches" in result.lower()

    def test_truncation_at_200(self, tmp_path):
        # Create 250 .py files
        for i in range(250):
            (tmp_path / f"file_{i:03d}.py").write_text(f"# file {i}\n")
        tool = FindFilesTool(tmp_path)
        result = tool.run({"pattern": "**/*.py"})
        lines = [ln for ln in result.splitlines() if ln and not ln.startswith("(")]
        assert len(lines) == 200
        # Should contain a truncation notice
        assert "200" in result or "truncated" in result.lower()

    def test_missing_pattern_raises_tool_error(self, tmp_path):
        tool = FindFilesTool(tmp_path)
        with pytest.raises(ToolError):
            tool.run({})

    def test_tool_attributes(self, tmp_path):
        tool = FindFilesTool(tmp_path)
        assert tool.name == "find_files"
        assert tool.requires_confirmation is False
        assert isinstance(tool.parameters, dict)
        assert "pattern" in tool.parameters.get("properties", {})

    def test_nested_dirs_found(self, tmp_path):
        # Create deeply nested structure
        deep = tmp_path / "level1" / "level2" / "level3"
        deep.mkdir(parents=True)
        (deep / "deep.py").write_text("deep\n")
        tool = FindFilesTool(tmp_path)
        result = tool.run({"pattern": "**/*.py"})
        assert "level1/level2/level3/deep.py" in result


# ---------------------------------------------------------------------------
# SearchTextTool
# ---------------------------------------------------------------------------


class TestSearchTextTool:
    def test_basic_match_format(self, tmp_path):
        make_tree(tmp_path)
        tool = SearchTextTool(tmp_path)
        result = tool.run({"pattern": "hello"})
        # Expect lines like "path:lineno:content"
        lines = [ln for ln in result.splitlines() if ln and not ln.startswith("(")]
        assert len(lines) >= 2  # a.py:1:hello world, sub/c.py:2:hello again
        for line in lines:
            parts = line.split(":", 2)
            assert len(parts) == 3, f"Bad format: {line!r}"
            assert parts[1].isdigit(), f"Line number not digit: {line!r}"

    def test_relative_paths_in_output(self, tmp_path):
        make_tree(tmp_path)
        tool = SearchTextTool(tmp_path)
        result = tool.run({"pattern": "hello"})
        assert "a.py:1:" in result
        assert "sub/c.py:2:" in result

    def test_binary_files_skipped(self, tmp_path):
        make_tree(tmp_path)
        tool = SearchTextTool(tmp_path)
        result = tool.run({"pattern": "data"})
        assert "binary_file" not in result

    def test_skipped_dirs_not_searched(self, tmp_path):
        make_tree(tmp_path)
        tool = SearchTextTool(tmp_path)
        result = tool.run({"pattern": "should not appear"})
        assert result == "" or "no matches" in result.lower()

    def test_invalid_regex_raises_tool_error(self, tmp_path):
        make_tree(tmp_path)
        tool = SearchTextTool(tmp_path)
        with pytest.raises(ToolError) as exc_info:
            tool.run({"pattern": "["})  # invalid regex
        assert (
            "regex" in str(exc_info.value).lower()
            or "pattern" in str(exc_info.value).lower()
            or "invalid" in str(exc_info.value).lower()
        )

    def test_missing_pattern_raises_tool_error(self, tmp_path):
        tool = SearchTextTool(tmp_path)
        with pytest.raises(ToolError):
            tool.run({})

    def test_glob_filter_limits_search(self, tmp_path):
        make_tree(tmp_path)
        tool = SearchTextTool(tmp_path)
        # "hello" appears in a.py and sub/c.py; d.txt has no "hello"
        # With glob *.txt, only d.txt is searched
        result = tool.run({"pattern": "hello", "glob": "**/*.txt"})
        assert "a.py" not in result
        assert "c.py" not in result

    def test_glob_filter_matches_files(self, tmp_path):
        make_tree(tmp_path)
        tool = SearchTextTool(tmp_path)
        # "not python" is in d.txt
        result = tool.run({"pattern": "not python", "glob": "**/*.txt"})
        assert "d.txt" in result

    def test_truncation_at_200_matches(self, tmp_path):
        # Create a file with 250 matching lines
        lines = "\n".join(f"match line {i}" for i in range(250))
        (tmp_path / "big.py").write_text(lines + "\n")
        tool = SearchTextTool(tmp_path)
        result = tool.run({"pattern": "match line"})
        match_lines = [ln for ln in result.splitlines() if "big.py" in ln]
        assert len(match_lines) == 200
        assert "200" in result or "truncated" in result.lower()

    def test_line_content_rstripped(self, tmp_path):
        (tmp_path / "whitespace.py").write_text("hello world   \n")
        tool = SearchTextTool(tmp_path)
        result = tool.run({"pattern": "hello"})
        assert result.rstrip().endswith("hello world")

    def test_long_line_capped(self, tmp_path):
        long_line = "x" * 300
        (tmp_path / "long.py").write_text(long_line + "\n")
        tool = SearchTextTool(tmp_path)
        result = tool.run({"pattern": "x+"})
        # Display should be capped at ~200 chars for the content portion
        match_line = [ln for ln in result.splitlines() if "long.py" in ln][0]
        content_part = match_line.split(":", 2)[2]
        assert len(content_part) <= 210  # allow a small buffer for "..."

    def test_tool_attributes(self, tmp_path):
        tool = SearchTextTool(tmp_path)
        assert tool.name == "search_text"
        assert tool.requires_confirmation is False
        assert isinstance(tool.parameters, dict)
        assert "pattern" in tool.parameters.get("properties", {})

    def test_no_matches_returns_empty_or_notice(self, tmp_path):
        make_tree(tmp_path)
        tool = SearchTextTool(tmp_path)
        result = tool.run({"pattern": "zzz_no_match_zzz"})
        # Either empty string or explicit "no matches" message
        assert result == "" or "no matches" in result.lower()

    def test_unicode_decode_error_file_skipped(self, tmp_path):
        # Write a file with invalid UTF-8 that might cause decode issues
        (tmp_path / "latin.py").write_bytes(b"caf\xe9 hello\n")
        tool = SearchTextTool(tmp_path)
        # Should not raise; either finds or skips gracefully
        result = tool.run({"pattern": "hello"})
        # As long as it doesn't raise, we're good
        assert isinstance(result, str)


# ===========================================================================
# v0.6 · C35 · F43/F45（任务 T75）— tool metadata
# ===========================================================================


class TestSearchToolsMetadata:
    def test_find_files_is_read_only(self, tmp_path):
        from wentian.permissions.decision import Category

        tool = FindFilesTool(tmp_path)
        assert tool.category is Category.READ_ONLY
        assert tool.friendly_name == "Glob"
        assert tool.requires_confirmation is False
        assert tool.command_arg is None
        assert tool.path_args == ()

    def test_search_text_is_read_only(self, tmp_path):
        from wentian.permissions.decision import Category

        tool = SearchTextTool(tmp_path)
        assert tool.category is Category.READ_ONLY
        assert tool.friendly_name == "Grep"
        assert tool.requires_confirmation is False
        assert tool.command_arg is None
        assert tool.path_args == ()
