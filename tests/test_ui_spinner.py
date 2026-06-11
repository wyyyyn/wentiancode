"""Tests for WaitingSpinner (v0.2 · C5 · F17 / T20)."""

import pytest
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
# Test 2 – frame rotation
# ---------------------------------------------------------------------------

def test_frame_rotation():
    clock, advance = make_clock(0.0)
    console = Console(record=True)
    spinner = WaitingSpinner(console, clock=clock)
    spinner.start()

    # First frame at elapsed=0
    text_a = spinner.render_line()
    frame_a = text_a.plain[0]

    # Advance enough so int(elapsed*4) changes by at least 1 (0.25 s)
    advance(0.3)
    text_b = spinner.render_line()
    frame_b = text_b.plain[0]

    # Frames should differ
    assert frame_a != frame_b
    # Both must come from FRAMES
    assert frame_a in WaitingSpinner.FRAMES
    assert frame_b in WaitingSpinner.FRAMES


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
