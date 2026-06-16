"""Tests for tools/files.py.

RED-GREEN-REFACTOR cycle for T32 (read_file, write_file) and T33 (edit_file).
"""

from __future__ import annotations

import pytest
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_read(tmp_path: Path):
    from wentian.tools.files import ReadFileTool

    return ReadFileTool(root=tmp_path)


def _make_write(tmp_path: Path):
    from wentian.tools.files import WriteFileTool

    return WriteFileTool(root=tmp_path)


def _make_edit(tmp_path: Path):
    from wentian.tools.files import EditFileTool

    return EditFileTool(root=tmp_path)


# ===========================================================================
# T32 — WriteFileTool
# ===========================================================================


class TestWriteFileTool:
    def test_write_new_file_returns_success_text(self, tmp_path):
        """write_file writes a new file and returns a success message containing the path."""
        tool = _make_write(tmp_path)
        target = tmp_path / "hello.txt"
        result = tool.run({"path": str(target), "content": "hello world"})
        assert "hello.txt" in result
        assert target.read_text() == "hello world"

    def test_write_creates_parent_dirs(self, tmp_path):
        """write_file creates missing parent directories automatically."""
        tool = _make_write(tmp_path)
        target = tmp_path / "a" / "b" / "c.txt"
        tool.run({"path": str(target), "content": "deep"})
        assert target.exists()
        assert target.read_text() == "deep"

    def test_write_overwrites_existing_file(self, tmp_path):
        """write_file overwrites an existing file."""
        tool = _make_write(tmp_path)
        target = tmp_path / "file.txt"
        target.write_text("old content")
        tool.run({"path": str(target), "content": "new content"})
        assert target.read_text() == "new content"

    def test_write_result_contains_byte_count(self, tmp_path):
        """write_file result includes byte count."""
        tool = _make_write(tmp_path)
        target = tmp_path / "bytes.txt"
        content = "12345"
        result = tool.run({"path": str(target), "content": content})
        assert "5" in result  # 5 bytes

    def test_write_requires_confirmation(self, tmp_path):
        """WriteFileTool.requires_confirmation is True."""
        tool = _make_write(tmp_path)
        assert tool.requires_confirmation is True

    def test_write_relative_path_resolves_against_root(self, tmp_path):
        """write_file with a relative path resolves against root."""
        tool = _make_write(tmp_path)
        tool.run({"path": "relative.txt", "content": "rel"})
        assert (tmp_path / "relative.txt").exists()

    def test_write_missing_path_raises_tool_error(self, tmp_path):
        """write_file with missing 'path' raises ToolError."""
        from wentian.tools.base import ToolError

        tool = _make_write(tmp_path)
        with pytest.raises(ToolError):
            tool.run({"content": "oops"})

    def test_write_missing_content_raises_tool_error(self, tmp_path):
        """write_file with missing 'content' raises ToolError."""
        from wentian.tools.base import ToolError

        tool = _make_write(tmp_path)
        with pytest.raises(ToolError):
            tool.run({"path": "file.txt"})

    def test_write_non_string_path_raises_tool_error(self, tmp_path):
        """write_file with non-string path raises ToolError."""
        from wentian.tools.base import ToolError

        tool = _make_write(tmp_path)
        with pytest.raises(ToolError):
            tool.run({"path": 42, "content": "x"})


# ===========================================================================
# T32 — ReadFileTool
# ===========================================================================


class TestReadFileTool:
    def test_read_returns_written_content(self, tmp_path):
        """read_file reads back what write_file wrote."""
        target = tmp_path / "test.txt"
        target.write_text("line1\nline2\nline3")
        tool = _make_read(tmp_path)
        result = tool.run({"path": str(target)})
        assert "line1" in result
        assert "line2" in result

    def test_read_relative_path(self, tmp_path):
        """read_file resolves relative paths against root."""
        (tmp_path / "rel.txt").write_text("relative content")
        tool = _make_read(tmp_path)
        result = tool.run({"path": "rel.txt"})
        assert "relative content" in result

    def test_read_absolute_path(self, tmp_path):
        """read_file handles absolute paths directly."""
        target = tmp_path / "abs.txt"
        target.write_text("absolute")
        tool = _make_read(tmp_path)
        result = tool.run({"path": str(target)})
        assert "absolute" in result

    def test_read_offset_and_limit(self, tmp_path):
        """read_file respects offset (1-based) and limit."""
        lines = [f"line{i}" for i in range(1, 11)]
        target = tmp_path / "lines.txt"
        target.write_text("\n".join(lines))
        tool = _make_read(tmp_path)
        # offset=3, limit=3 → lines 3,4,5
        result = tool.run({"path": str(target), "offset": 3, "limit": 3})
        assert "line3" in result
        assert "line4" in result
        assert "line5" in result
        assert "line1" not in result
        assert "line6" not in result

    def test_read_nonexistent_file_raises_tool_error(self, tmp_path):
        """read_file raises ToolError for a nonexistent file, message contains path."""
        from wentian.tools.base import ToolError

        tool = _make_read(tmp_path)
        with pytest.raises(ToolError, match="no_such_file.txt"):
            tool.run({"path": "no_such_file.txt"})

    def test_read_directory_raises_tool_error(self, tmp_path):
        """read_file raises ToolError when path points to a directory."""
        from wentian.tools.base import ToolError

        subdir = tmp_path / "subdir"
        subdir.mkdir()
        tool = _make_read(tmp_path)
        with pytest.raises(ToolError):
            tool.run({"path": str(subdir)})

    def test_read_truncates_at_2000_lines(self, tmp_path):
        """read_file truncates files over 2000 lines and appends a truncation notice."""
        lines = [f"line{i}" for i in range(1, 2101)]  # 2100 lines
        target = tmp_path / "big.txt"
        target.write_text("\n".join(lines))
        tool = _make_read(tmp_path)
        result = tool.run({"path": str(target)})
        # Should not include line 2001+
        assert "line2001" not in result
        # Should include truncation notice with total line count
        assert "2100" in result

    def test_read_missing_path_raises_tool_error(self, tmp_path):
        """read_file with missing 'path' parameter raises ToolError."""
        from wentian.tools.base import ToolError

        tool = _make_read(tmp_path)
        with pytest.raises(ToolError):
            tool.run({})

    def test_read_non_string_path_raises_tool_error(self, tmp_path):
        """read_file with non-string path raises ToolError."""
        from wentian.tools.base import ToolError

        tool = _make_read(tmp_path)
        with pytest.raises(ToolError):
            tool.run({"path": 123})

    def test_read_offset_only(self, tmp_path):
        """read_file with only offset reads from that line to end (or cap)."""
        lines = [f"L{i}" for i in range(1, 6)]
        target = tmp_path / "o.txt"
        target.write_text("\n".join(lines))
        tool = _make_read(tmp_path)
        result = tool.run({"path": str(target), "offset": 3})
        assert "L3" in result
        assert "L1" not in result

    def test_read_limit_only(self, tmp_path):
        """read_file with only limit reads the first N lines."""
        lines = [f"M{i}" for i in range(1, 6)]
        target = tmp_path / "l.txt"
        target.write_text("\n".join(lines))
        tool = _make_read(tmp_path)
        result = tool.run({"path": str(target), "limit": 2})
        assert "M1" in result
        assert "M2" in result
        assert "M3" not in result


# ===========================================================================
# T33 — EditFileTool
# ===========================================================================


class TestEditFileTool:
    def test_edit_unique_match_replaces_and_returns_success(self, tmp_path):
        """edit_file replaces a uniquely-matching old_string and returns success text."""
        target = tmp_path / "edit.txt"
        target.write_text("hello world\nfoo bar\n")
        tool = _make_edit(tmp_path)
        result = tool.run(
            {
                "path": str(target),
                "old_string": "foo bar",
                "new_string": "baz qux",
            }
        )
        assert "edit.txt" in result
        assert target.read_text() == "hello world\nbaz qux\n"

    def test_edit_zero_matches_raises_tool_error(self, tmp_path):
        """edit_file raises ToolError (mentioning '0') when old_string not found."""
        from wentian.tools.base import ToolError

        target = tmp_path / "zero.txt"
        target.write_text("some content here")
        original = target.read_text()
        tool = _make_edit(tmp_path)
        with pytest.raises(ToolError, match="0"):
            tool.run(
                {
                    "path": str(target),
                    "old_string": "not present at all",
                    "new_string": "replacement",
                }
            )
        # File must be unchanged
        assert target.read_text() == original

    def test_edit_multiple_matches_raises_tool_error(self, tmp_path):
        """edit_file raises ToolError (mentioning '3') when old_string matches 3 times."""
        from wentian.tools.base import ToolError

        target = tmp_path / "multi.txt"
        target.write_text("abc\nabc\nabc\n")
        original = target.read_text()
        tool = _make_edit(tmp_path)
        with pytest.raises(ToolError, match="3"):
            tool.run(
                {
                    "path": str(target),
                    "old_string": "abc",
                    "new_string": "xyz",
                }
            )
        assert target.read_text() == original

    def test_edit_same_old_new_raises_tool_error(self, tmp_path):
        """edit_file raises ToolError when old_string == new_string."""
        from wentian.tools.base import ToolError

        target = tmp_path / "same.txt"
        target.write_text("unchanged content")
        tool = _make_edit(tmp_path)
        with pytest.raises(ToolError):
            tool.run(
                {
                    "path": str(target),
                    "old_string": "unchanged content",
                    "new_string": "unchanged content",
                }
            )

    def test_edit_nonexistent_file_raises_tool_error(self, tmp_path):
        """edit_file raises ToolError when file does not exist."""
        from wentian.tools.base import ToolError

        tool = _make_edit(tmp_path)
        with pytest.raises(ToolError):
            tool.run(
                {
                    "path": "ghost.txt",
                    "old_string": "x",
                    "new_string": "y",
                }
            )

    def test_edit_requires_confirmation(self, tmp_path):
        """EditFileTool.requires_confirmation is True."""
        tool = _make_edit(tmp_path)
        assert tool.requires_confirmation is True

    def test_edit_relative_path(self, tmp_path):
        """edit_file resolves relative paths against root."""
        target = tmp_path / "rel_edit.txt"
        target.write_text("old text")
        tool = _make_edit(tmp_path)
        tool.run(
            {
                "path": "rel_edit.txt",
                "old_string": "old text",
                "new_string": "new text",
            }
        )
        assert target.read_text() == "new text"

    def test_edit_two_matches_raises_tool_error_with_count(self, tmp_path):
        """edit_file raises ToolError mentioning '2' for two matches."""
        from wentian.tools.base import ToolError

        target = tmp_path / "two.txt"
        target.write_text("dup\ndup\n")
        tool = _make_edit(tmp_path)
        with pytest.raises(ToolError, match="2"):
            tool.run(
                {
                    "path": str(target),
                    "old_string": "dup",
                    "new_string": "unique",
                }
            )

    def test_edit_missing_path_raises_tool_error(self, tmp_path):
        """edit_file with missing 'path' raises ToolError."""
        from wentian.tools.base import ToolError

        tool = _make_edit(tmp_path)
        with pytest.raises(ToolError):
            tool.run({"old_string": "x", "new_string": "y"})

    def test_edit_missing_old_string_raises_tool_error(self, tmp_path):
        """edit_file with missing 'old_string' raises ToolError."""
        from wentian.tools.base import ToolError

        target = tmp_path / "f.txt"
        target.write_text("content")
        tool = _make_edit(tmp_path)
        with pytest.raises(ToolError):
            tool.run({"path": str(target), "new_string": "y"})

    def test_edit_missing_new_string_raises_tool_error(self, tmp_path):
        """edit_file with missing 'new_string' raises ToolError."""
        from wentian.tools.base import ToolError

        target = tmp_path / "f2.txt"
        target.write_text("content")
        tool = _make_edit(tmp_path)
        with pytest.raises(ToolError):
            tool.run({"path": str(target), "old_string": "content"})

    def test_edit_zero_match_error_contains_path(self, tmp_path):
        """edit_file zero-match ToolError message contains the file path."""
        from wentian.tools.base import ToolError

        target = tmp_path / "pathinmsg.txt"
        target.write_text("some text here")
        tool = _make_edit(tmp_path)
        with pytest.raises(ToolError) as exc_info:
            tool.run(
                {
                    "path": str(target),
                    "old_string": "absent string",
                    "new_string": "x",
                }
            )
        assert "pathinmsg.txt" in str(exc_info.value)

    def test_edit_directory_raises_tool_error(self, tmp_path):
        """edit_file raises ToolError when path is a directory."""
        from wentian.tools.base import ToolError

        subdir = tmp_path / "adir"
        subdir.mkdir()
        tool = _make_edit(tmp_path)
        with pytest.raises(ToolError):
            tool.run(
                {
                    "path": str(subdir),
                    "old_string": "x",
                    "new_string": "y",
                }
            )


# ===========================================================================
# v0.6 · C35 · F43/F45（任务 T75）— tool metadata
# ===========================================================================


class TestFileToolsMetadata:
    def test_read_is_read_only(self, tmp_path):
        from wentian.permissions.decision import Category

        tool = _make_read(tmp_path)
        assert tool.category is Category.READ_ONLY
        assert tool.friendly_name == "Read"
        assert tool.requires_confirmation is False
        assert tool.command_arg is None
        assert tool.path_args == ("path",)

    def test_write_is_file_write(self, tmp_path):
        from wentian.permissions.decision import Category

        tool = _make_write(tmp_path)
        assert tool.category is Category.FILE_WRITE
        assert tool.friendly_name == "Write"
        assert tool.requires_confirmation is True
        assert tool.command_arg is None
        assert tool.path_args == ("path",)

    def test_edit_is_file_write(self, tmp_path):
        from wentian.permissions.decision import Category

        tool = _make_edit(tmp_path)
        assert tool.category is Category.FILE_WRITE
        assert tool.friendly_name == "Edit"
        assert tool.requires_confirmation is True
        assert tool.command_arg is None
        assert tool.path_args == ("path",)
