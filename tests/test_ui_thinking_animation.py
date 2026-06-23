"""Tests for ThinkingAnimation (v0.14 · C130 · F103/F104)."""

import io

from rich.console import Console

from wentian.ui.thinking_animation import (
    BRAILLE_FRAMES,
    THINK_STYLE,
    ThinkingAnimation,
    pick_braille_frame,
)


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
# Test 1 – pick_braille_frame: valid index, advances, wraps
# ---------------------------------------------------------------------------


def test_pick_braille_frame_valid_index():
    """Always returns a valid index into BRAILLE_FRAMES."""
    for elapsed in (0.0, 0.05, 0.1, 0.55, 1.0, 12.7, 999.9):
        idx = pick_braille_frame(elapsed)
        assert 0 <= idx < len(BRAILLE_FRAMES)


def test_pick_braille_frame_advances_and_wraps():
    """int(elapsed*10) % len → advances ~every 0.1s, wraps around."""
    n = len(BRAILLE_FRAMES)
    assert n == 8
    assert pick_braille_frame(0.0) == 0
    assert pick_braille_frame(0.1) == 1
    assert pick_braille_frame(0.2) == 2
    assert pick_braille_frame(0.7) == 7
    # wraps back to 0 after a full cycle (0.8s → index 8 % 8 == 0)
    assert pick_braille_frame(0.8) == 0
    assert pick_braille_frame(0.9) == 1


# ---------------------------------------------------------------------------
# Test 2 – render_line: braille frame + 🧠 思考中 + (Ns) + THINK_STYLE
# ---------------------------------------------------------------------------


def test_render_line_contents_and_seconds():
    clock, advance = make_clock(100.0)
    console = Console(record=True)  # is_terminal is False in this context
    anim = ThinkingAnimation(console, clock=clock)
    anim.start()

    # At t0 elapsed == 0 → "(0s)", braille frame index 0
    text = anim.render_line()
    plain = text.plain
    assert BRAILLE_FRAMES[0] in plain
    assert "🧠" in plain
    assert "思考中" in plain
    assert "(0s)" in plain

    # Advance 5.3 s → "(5s)" and the matching braille frame.
    # Compute the expected frame from the live elapsed value (not the bare
    # literal) so float arithmetic stays consistent with render_line.
    advance(5.3)
    text = anim.render_line()
    plain = text.plain
    assert "(5s)" in plain
    assert BRAILLE_FRAMES[pick_braille_frame(anim.elapsed)] in plain


def test_render_line_style_is_think_style():
    clock, _ = make_clock(0.0)
    console = Console(record=True)
    anim = ThinkingAnimation(console, clock=clock)
    anim.start()
    assert anim.render_line().style == THINK_STYLE


# ---------------------------------------------------------------------------
# Test 3 – render_breadcrumb: 💭 思考 Ns, dim, uses elapsed duration
# ---------------------------------------------------------------------------


def test_render_breadcrumb_contents_and_dim():
    clock, advance = make_clock(0.0)
    console = Console(record=True)
    anim = ThinkingAnimation(console, clock=clock)
    anim.start()

    advance(7.8)  # thinking duration 7.8s → "思考 7s"
    crumb = anim.render_breadcrumb()
    plain = crumb.plain
    assert "💭 思考" in plain
    assert "7s" in plain
    assert crumb.style == "dim"


# ---------------------------------------------------------------------------
# Test 4 – elapsed property
# ---------------------------------------------------------------------------


def test_elapsed_before_start_is_zero():
    clock, _ = make_clock(50.0)
    console = Console(record=True)
    anim = ThinkingAnimation(console, clock=clock)
    assert anim.elapsed == 0.0


def test_elapsed_after_start():
    clock, advance = make_clock(10.0)
    console = Console(record=True)
    anim = ThinkingAnimation(console, clock=clock)
    anim.start()
    advance(3.2)
    assert abs(anim.elapsed - 3.2) < 1e-9


# ---------------------------------------------------------------------------
# Test 5 – non-TTY: no output, no crashes; idempotency
# ---------------------------------------------------------------------------


def test_non_tty_no_output_and_no_crash():
    console = Console(record=True)  # is_terminal == False
    anim = ThinkingAnimation(console)
    anim.start()
    anim.stop()
    assert console.export_text() == ""


def test_non_tty_double_stop_no_exception():
    console = Console(record=True)
    anim = ThinkingAnimation(console)
    anim.start()
    anim.stop()
    anim.stop()  # second stop must not raise


def test_non_tty_stop_before_start_no_exception():
    console = Console(record=True)
    anim = ThinkingAnimation(console)
    anim.stop()  # must not raise


def test_tty_double_start_no_live_leak():
    """Regression: start() twice without stop() must not orphan a Live.

    Mirrors WaitingSpinner: a leaked Live keeps its refresh thread alive
    and hijacks stdout. After start(); start(); stop() the console must be
    fully released and usable again.
    """
    buf = io.StringIO()
    console = Console(force_terminal=True, file=buf)
    anim = ThinkingAnimation(console)

    anim.start()
    anim.start()  # second start without intervening stop
    anim.stop()

    assert anim._live is None
    assert not console._live_stack
    console.print("hello-after-stop")
    assert "hello-after-stop" in buf.getvalue()


# ---------------------------------------------------------------------------
# Test 6 – THINK_STYLE distinct from mascot 朱砂 (F103 visual distinction)
# ---------------------------------------------------------------------------


def test_think_style_distinct_from_mascot():
    from wentian.ui.mascot import FACE_STYLE  # noqa: PLC0415

    assert FACE_STYLE == "bold #C84B31"
    assert THINK_STYLE != FACE_STYLE
    assert "#C84B31" not in THINK_STYLE
