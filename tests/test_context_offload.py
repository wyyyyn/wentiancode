"""Tests for the first-layer oversized tool-result offload pass.

v0.8 · C48 · F57/N27（任务 T92）

All offline; ``tmp_path`` serves as the artifacts directory. Covers the
task.md T92 RED list: single-message offload (disk write + content
replacement + ``offloaded`` marker + ``is_error`` preserved + returned
``OffloadAction``); idempotence; per-round sum gate picking the largest until
the round falls back under threshold; user/assistant messages never rewritten
(N27); and preview truncation.
"""

from __future__ import annotations

from wentian.config import ContextConfig
from wentian.context.offload import OffloadAction, offload_oversized


# A char_per_token of 1.0 makes "tokens == characters", so thresholds map
# directly onto content length for predictable tests.
def _cfg(*, single: int, round_sum: int) -> ContextConfig:
    return ContextConfig(
        offload_single_tokens=single,
        offload_round_sum_tokens=round_sum,
        char_per_token=1.0,
    )


def _tool(call_id: str, content: str, *, is_error: bool = False) -> dict:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": content,
        "is_error": is_error,
    }


# ---------------------------------------------------------------------------
# RED 1: single-message offload
# ---------------------------------------------------------------------------


def test_single_oversized_offloaded_to_disk(tmp_path):
    big = "X" * 5000
    messages = [_tool("call-1", big)]
    cfg = _cfg(single=2000, round_sum=8000)

    actions = offload_oversized(messages, artifacts_dir=tmp_path, cfg=cfg)

    # Original content written verbatim to artifacts_dir/tool-<id>.txt.
    artifact = tmp_path / "tool-call-1.txt"
    assert artifact.exists()
    assert artifact.read_text(encoding="utf-8") == big

    # The conversation message no longer holds the full content.
    msg = messages[0]
    assert msg["content"] != big
    assert str(artifact) in msg["content"]  # absolute path mentioned
    assert "完整输出已存盘" in msg["content"]
    assert msg["offloaded"] is True

    # A single OffloadAction is returned with id/path/tokens.
    assert len(actions) == 1
    action = actions[0]
    assert isinstance(action, OffloadAction)
    assert action.tool_call_id == "call-1"
    assert action.path == str(artifact)
    assert action.original_tokens == 5000


def test_is_error_preserved_on_offload(tmp_path):
    messages = [_tool("err-1", "Y" * 5000, is_error=True)]
    cfg = _cfg(single=2000, round_sum=8000)

    offload_oversized(messages, artifacts_dir=tmp_path, cfg=cfg)

    assert messages[0]["is_error"] is True
    assert messages[0]["offloaded"] is True


def test_under_threshold_not_offloaded(tmp_path):
    small = "z" * 100
    messages = [_tool("ok-1", small)]
    cfg = _cfg(single=2000, round_sum=8000)

    actions = offload_oversized(messages, artifacts_dir=tmp_path, cfg=cfg)

    assert actions == []
    assert messages[0]["content"] == small
    assert not messages[0].get("offloaded")
    assert not (tmp_path / "tool-ok-1.txt").exists()


# ---------------------------------------------------------------------------
# RED 2: idempotence
# ---------------------------------------------------------------------------


def test_idempotent_already_offloaded_skipped(tmp_path):
    big = "X" * 5000
    messages = [_tool("call-1", big)]
    cfg = _cfg(single=2000, round_sum=8000)

    first = offload_oversized(messages, artifacts_dir=tmp_path, cfg=cfg)
    assert len(first) == 1
    replaced = messages[0]["content"]

    # Second scan: already offloaded → no action, no rewrite, no re-write to disk.
    artifact = tmp_path / "tool-call-1.txt"
    mtime_before = artifact.stat().st_mtime_ns

    second = offload_oversized(messages, artifacts_dir=tmp_path, cfg=cfg)

    assert second == []
    assert messages[0]["content"] == replaced
    assert artifact.stat().st_mtime_ns == mtime_before


# ---------------------------------------------------------------------------
# RED 3: per-round sum gate — pick the largest until the round falls back
# ---------------------------------------------------------------------------


def test_round_sum_offloads_largest_until_under_threshold(tmp_path):
    # Three tool results in one round, each under the single gate (4000),
    # but together (3000 + 2500 + 1000 = 6500) over the round gate (5000).
    a = _tool("a", "a" * 3000)
    b = _tool("b", "b" * 2500)
    c = _tool("c", "c" * 1000)
    messages = [a, b, c]
    cfg = _cfg(single=4000, round_sum=5000)

    actions = offload_oversized(messages, artifacts_dir=tmp_path, cfg=cfg)

    offloaded_ids = {act.tool_call_id for act in actions}
    # Offload "a" (3000) → remaining 3500 still > 5000? no, 2500+1000=3500 < 5000.
    # So only the single largest "a" is offloaded; b and c left intact.
    assert offloaded_ids == {"a"}
    assert messages[0]["offloaded"] is True
    assert not messages[1].get("offloaded")
    assert not messages[2].get("offloaded")
    assert messages[1]["content"] == "b" * 2500
    assert messages[2]["content"] == "c" * 1000


def test_round_sum_offloads_multiple_largest_first(tmp_path):
    # 3000 + 2500 + 2400 = 7900 over round gate 5000.
    # Offload 3000 → 4900 still under 5000? 2500+2400=4900 < 5000 → stop after one.
    # Use a tighter gate so two must go.
    a = _tool("a", "a" * 3000)
    b = _tool("b", "b" * 2500)
    c = _tool("c", "c" * 2400)
    messages = [a, b, c]
    cfg = _cfg(single=4000, round_sum=4000)

    actions = offload_oversized(messages, artifacts_dir=tmp_path, cfg=cfg)

    # 7900 > 4000 → drop a (3000): 4900 > 4000 → drop b (2500): 2400 < 4000 → stop.
    offloaded_ids = {act.tool_call_id for act in actions}
    assert offloaded_ids == {"a", "b"}
    assert not messages[2].get("offloaded")
    assert messages[2]["content"] == "c" * 2400


def test_separate_rounds_isolated_by_non_tool_message(tmp_path):
    # Two tool groups split by an assistant message; each group small enough
    # to stay under its round gate, so nothing is offloaded.
    cfg = _cfg(single=5000, round_sum=5000)
    messages = [
        _tool("a", "a" * 2000),
        _tool("b", "b" * 2000),
        {"role": "assistant", "content": "thinking"},
        _tool("c", "c" * 2000),
        _tool("d", "d" * 2000),
    ]

    actions = offload_oversized(messages, artifacts_dir=tmp_path, cfg=cfg)

    # Each group sums to 4000 < 5000 → no offload (would offload if treated as
    # one 8000-char group).
    assert actions == []
    assert all(not m.get("offloaded") for m in messages if m["role"] == "tool")


# ---------------------------------------------------------------------------
# RED 4: user / assistant messages never rewritten (N27)
# ---------------------------------------------------------------------------


def test_user_and_assistant_never_rewritten(tmp_path):
    big_user = "U" * 9000
    big_assistant = "A" * 9000
    messages = [
        {"role": "user", "content": big_user},
        {"role": "assistant", "content": big_assistant},
        _tool("t", "T" * 5000),
    ]
    cfg = _cfg(single=2000, round_sum=8000)

    offload_oversized(messages, artifacts_dir=tmp_path, cfg=cfg)

    # User / assistant content untouched, no offloaded marker, nothing on disk.
    assert messages[0]["content"] == big_user
    assert messages[1]["content"] == big_assistant
    assert not messages[0].get("offloaded")
    assert not messages[1].get("offloaded")
    # Only the tool result was offloaded.
    assert messages[2]["offloaded"] is True


# ---------------------------------------------------------------------------
# RED 5: preview truncation (first ~20 lines or ~800 chars, whichever first)
# ---------------------------------------------------------------------------


def test_preview_truncated_by_char_limit(tmp_path):
    # Single long line well over 800 chars → preview capped at ~800 chars.
    line = "Q" * 5000
    messages = [_tool("p1", line)]
    cfg = _cfg(single=2000, round_sum=8000)

    offload_oversized(messages, artifacts_dir=tmp_path, cfg=cfg)

    content = messages[0]["content"]
    preview = content.split("\n\n[完整输出已存盘")[0]
    assert len(preview) <= 800
    # full original is on disk, not inline.
    assert line not in content


def test_preview_truncated_by_line_limit(tmp_path):
    # 100 lines, each padded to ~21 chars → total ~2.1K chars (over the single
    # gate so it offloads), yet 20 lines (~420 chars) fit inside the 800-char
    # cap, so the line gate (20) hits before the char gate.
    body = "\n".join(f"line-{i:03d}-{'.' * 12}" for i in range(100))
    messages = [_tool("p2", body)]
    cfg = _cfg(single=2000, round_sum=8000)

    offload_oversized(messages, artifacts_dir=tmp_path, cfg=cfg)

    content = messages[0]["content"]
    preview = content.split("\n\n[完整输出已存盘")[0]
    assert preview.count("\n") <= 20
    assert "line-099" not in preview
    # Full content preserved on disk.
    assert (tmp_path / "tool-p2.txt").read_text(encoding="utf-8") == body
