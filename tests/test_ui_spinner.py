"""Tests for WaitingSpinner (v0.2 · C5 · F17 / T20)."""

import io

from rich.console import Console

from wentian.ui.spinner import WaitingSpinner


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def make_clock(start: float = 0.0):
    """Return (get_time, advance) pair backed by a mutable container."""
    state = [start]

    def get_time() -> float:
        return state[0]

    def advance(delta: float) -> None:
        state[0] += delta

    return get_time, advance


# ---------------------------------------------------------------------------
# Test 1 – seconds display in render_line
# ---------------------------------------------------------------------------


def test_render_line_elapsed_seconds():
    clock, advance = make_clock(100.0)
    console = Console(record=True)  # is_terminal is False in this context
    spinner = WaitingSpinner(console, clock=clock)

    spinner.start()

    # At t0 elapsed == 0 → "(0s)"
    text = spinner.render_line()
    assert "(0s)" in text.plain

    # Advance 5.3 s → "(5s)"
    advance(5.3)
    text = spinner.render_line()
    assert "(5s)" in text.plain


# ---------------------------------------------------------------------------
# Test 2 – cat face blink rotation (v0.2 · C5 · F17 / T28 改版)
# ---------------------------------------------------------------------------


def test_render_line_cat_face_blink():
    """流式期单行：以 =^_^= 文本猫脸开头，按时间出现眨眼帧 =-_-=。"""
    from wentian.ui.mascot import TEXT_FACES  # noqa: PLC0415

    clock, advance = make_clock(0.0)
    console = Console(record=True)
    spinner = WaitingSpinner(console, clock=clock)
    spinner.start()

    # elapsed=0 → 睁眼帧
    face_open = spinner.render_line().plain[:5]
    assert face_open == TEXT_FACES[0] == "=^_^="

    # elapsed=1.5 → int(1.5*2)%4==3 → 眨眼帧
    advance(1.5)
    face_blink = spinner.render_line().plain[:5]
    assert face_blink == TEXT_FACES[1] == "=-_-="


def test_render_block_is_text_face_line():
    """T29：等待期与流式期一致——单行 =^_^= 文本帧 + 秒数，无像素字符。"""
    clock, advance = make_clock(0.0)
    console = Console(record=True)
    spinner = WaitingSpinner(console, clock=clock)
    spinner.start()

    advance(2.2)  # elapsed=2.2 → 睁眼
    block_open = spinner.render_block()
    assert block_open.plain.startswith("=^_^="), "open-eye text face expected"
    assert "(2s)" in block_open.plain, "timer must appear"
    assert "▀" not in block_open.plain and "▄" not in block_open.plain

    advance(-0.7)  # elapsed=1.5 → 眨眼
    block_blink = spinner.render_block()
    assert block_blink.plain.startswith("=-_-="), "blink text face expected"


# ---------------------------------------------------------------------------
# Test 3 – non-TTY: no output, no crashes
# ---------------------------------------------------------------------------


def test_non_tty_no_output_and_no_crash():
    console = Console(record=True)
    # Console(record=True) has is_terminal == False
    spinner = WaitingSpinner(console)

    spinner.start()
    spinner.stop()

    assert console.export_text() == ""


def test_non_tty_double_stop_no_exception():
    console = Console(record=True)
    spinner = WaitingSpinner(console)
    spinner.start()
    spinner.stop()
    spinner.stop()  # second stop must not raise


def test_non_tty_stop_before_start_no_exception():
    console = Console(record=True)
    spinner = WaitingSpinner(console)
    spinner.stop()  # must not raise


# ---------------------------------------------------------------------------
# Test 3b – TTY: double start() must not leak an orphaned Live
# ---------------------------------------------------------------------------


def test_tty_double_start_no_live_leak():
    """Regression: start() twice without stop() must not orphan a Live.

    On rich 15, a leaked Live keeps its refresh thread alive and hijacks
    stdout/stderr (redirect_io) — output is swallowed and stop() cannot
    recover. After start(); start(); stop() the console must be fully
    released: no Live on the console's live stack, spinner._live cleared,
    and a subsequent console.print must reach the buffer.
    """
    buf = io.StringIO()
    console = Console(force_terminal=True, file=buf)
    spinner = WaitingSpinner(console)

    spinner.start()
    spinner.start()  # second start without intervening stop
    spinner.stop()

    assert spinner._live is None
    # No Live must remain registered on the console
    assert not console._live_stack
    # Output path must be usable again
    console.print("hello-after-stop")
    assert "hello-after-stop" in buf.getvalue()


# ---------------------------------------------------------------------------
# Test 4 – elapsed property
# ---------------------------------------------------------------------------


def test_elapsed_before_start_is_zero():
    clock, _ = make_clock(50.0)
    console = Console(record=True)
    spinner = WaitingSpinner(console, clock=clock)
    assert spinner.elapsed == 0.0


def test_elapsed_after_start():
    clock, advance = make_clock(10.0)
    console = Console(record=True)
    spinner = WaitingSpinner(console, clock=clock)
    spinner.start()
    advance(3.2)
    assert abs(spinner.elapsed - 3.2) < 1e-9
