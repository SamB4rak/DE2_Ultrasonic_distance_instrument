"""Host tests for src/button.py (the debounced push button)."""

import time

import pytest

import board
import machine
import settings

import button

PIN = board.PIN_BUTTON
POLL = settings.BUTTON_POLL_MS
UP, DOWN = 1, 0


def pin():
    """Return the fake Pin the driver created for the button."""
    return machine.Pin.get(PIN)


def run(b, steps):
    """Set each level, then poll at its time.

    Args:
        b (button.Button): the button under test.
        steps (list): ``(t_ms, level)`` pairs; ``level`` is applied to the
            pin just before the poll at ``t_ms``.

    Returns:
        list: the times at which ``poll()`` returned True.
    """
    hits = []
    for t, level in steps:
        pin().set_input(level)
        if b.poll(now_ms=t):
            hits.append(t)
    return hits


def grid(t0, t1, level, step=POLL):
    """Polls every ``step`` ms from ``t0`` up to (not including) ``t1``."""
    return [(t, level) for t in range(t0, t1, step)]


# --- construction ------------------------------------------------------------

def test_pin_is_input_with_pull_up_and_no_irq():
    b = button.Button(PIN)
    assert pin().mode == machine.Pin.IN
    assert pin().pull == machine.Pin.PULL_UP
    assert pin().irq_hard is None          # polled, never an interrupt
    assert b.is_down() is False


def test_default_debounce_comes_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "BUTTON_DEBOUNCE_MS", 100)
    b = button.Button(PIN)
    hits = run(b, grid(0, 20, UP) + grid(20, 200, DOWN))
    assert hits == [120]                   # first LOW at 20, stable 100 ms


def test_settings_defaults_give_three_polls():
    assert settings.BUTTON_POLL_MS == 20
    assert settings.BUTTON_DEBOUNCE_MS == 40


def test_explicit_debounce_ms():
    b = button.Button(PIN, debounce_ms=60)
    assert run(b, grid(0, 20, UP) + grid(20, 200, DOWN)) == [80]


def test_float_debounce_is_made_an_integer():
    b = button.Button(PIN, debounce_ms=40.0)
    assert run(b, grid(0, 20, UP) + grid(20, 200, DOWN)) == [60]


# --- presses -------------------------------------------------------------------

def test_clean_press_gives_one_true_on_the_third_low_poll():
    b = button.Button(PIN)
    assert run(b, grid(0, 20, UP)) == []
    pin().set_input(DOWN)
    assert b.poll(now_ms=20) is False      # 1st LOW poll: just changed
    assert b.poll(now_ms=40) is False      # 2nd: stable 20 ms
    assert b.is_down() is False
    assert b.poll(now_ms=60) is True       # 3rd: stable 40 ms
    assert b.is_down() is True
    assert run(b, grid(80, 400, DOWN)) == []


def test_39_ms_is_not_enough_and_40_ms_is():
    b = button.Button(PIN)
    pin().set_input(DOWN)
    assert b.poll(now_ms=100) is False
    assert b.poll(now_ms=139) is False
    assert b.poll(now_ms=140) is True


def bouncy_press(t):
    """Raw level at ``t`` ms: 5 toggles at 10..18 ms, then held LOW."""
    if t < 10:
        return UP
    if t >= 18:
        return DOWN
    return DOWN if (t - 10) // 2 % 2 == 0 else UP


@pytest.mark.parametrize("period, expected", [(1, 58), (2, 58), (20, 60)])
def test_bounce_gives_one_true(period, expected):
    b = button.Button(PIN)
    steps = [(t, bouncy_press(t)) for t in range(0, 300, period)]
    assert run(b, steps) == [expected]     # 40 ms after the last change seen
    assert b.is_down() is True


def test_bounce_model_has_five_toggles_in_8_ms():
    levels = [bouncy_press(t) for t in range(0, 30)]
    toggles = [t for t in range(1, 30) if levels[t] != levels[t - 1]]
    assert toggles == [10, 12, 14, 16, 18]


def test_release_bounce_gives_no_true():
    b = button.Button(PIN)
    assert run(b, grid(0, 100, DOWN)) == [40]
    release = [(t, DOWN if bouncy_press(t - 100) == UP else UP)
               for t in range(100, 300)]
    assert run(b, release) == []
    assert b.is_down() is False


def test_holding_gives_no_repeats_then_a_new_press_counts():
    b = button.Button(PIN)
    hits = run(b, grid(0, 20, UP) + grid(20, 2020, DOWN))
    assert hits == [60]                    # one press in a 2 s hold
    assert run(b, grid(2020, 2200, UP)) == []
    assert b.is_down() is False
    assert run(b, grid(2200, 2400, DOWN)) == [2240]


def test_short_release_blip_while_held_gives_no_second_press():
    b = button.Button(PIN)
    steps = grid(0, 20, UP) + grid(20, 200, DOWN) + [(200, UP)] + grid(220, 400, DOWN)
    assert run(b, steps) == [60]
    assert b.is_down() is True


def test_late_poll_still_reports_the_press():
    b = button.Button(PIN)
    pin().set_input(DOWN)
    assert b.poll(now_ms=20) is False
    assert b.poll(now_ms=120) is True      # a poll delayed by a busy loop


def test_glitch_of_one_poll_gives_no_true():
    b = button.Button(PIN)
    steps = grid(0, 20, UP) + [(20, DOWN)] + grid(40, 300, UP)
    assert run(b, steps) == []
    assert b.is_down() is False


def test_glitch_of_two_polls_gives_no_true():
    b = button.Button(PIN)
    steps = grid(0, 20, UP) + grid(20, 60, DOWN) + grid(60, 300, UP)
    assert run(b, steps) == []


def test_is_down_follows_the_debounced_state_not_the_raw_pin():
    b = button.Button(PIN)
    pin().set_input(DOWN)
    b.poll(now_ms=0)
    b.poll(now_ms=20)
    assert pin().value() == 0 and b.is_down() is False


def test_held_at_construction_needs_release_then_press():
    machine.Pin.preset(PIN, DOWN)
    b = button.Button(PIN)
    assert b.is_down() is True
    assert run(b, grid(0, 500, DOWN)) == []        # no spurious press
    assert run(b, grid(500, 600, UP)) == []
    assert run(b, grid(600, 700, DOWN)) == [640]


def test_ticks_wraparound():
    b = button.Button(PIN)
    start = time.ticks_add(0, -30)          # 30 ms before the wrap
    assert start > 2 ** 29                  # a large raw value
    steps = [(time.ticks_add(start, k * POLL), UP if k == 0 else DOWN)
             for k in range(10)]
    assert run(b, steps) == [time.ticks_add(start, 3 * POLL)]
    assert time.ticks_add(start, 3 * POLL) < start   # it wrapped


def test_poll_without_now_reads_the_clock(fake_clock):
    b = button.Button(PIN)
    fake_clock.advance_ms(1000)
    pin().set_input(DOWN)
    assert b.poll() is False
    fake_clock.advance_ms(39)
    assert b.poll() is False
    fake_clock.advance_ms(1)
    assert b.poll() is True


# --- safe boot -------------------------------------------------------------------

def test_held_at_boot_true_when_low():
    machine.Pin.preset(PIN, DOWN)
    assert button.Button.held_at_boot(PIN) is True
    assert pin().mode == machine.Pin.IN
    assert pin().pull == machine.Pin.PULL_UP


def test_held_at_boot_false_when_high():
    machine.Pin.preset(PIN, UP)
    assert button.Button.held_at_boot(PIN) is False


def test_held_at_boot_false_with_only_the_pull_up():
    assert button.Button.held_at_boot(PIN) is False
    assert pin().pull == machine.Pin.PULL_UP
