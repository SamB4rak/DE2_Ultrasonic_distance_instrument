"""Host tests for src/hcsr04.py (the HC-SR04 driver with hard-IRQ edge capture)."""

import asyncio
import os
import time

import pytest

import board
import machine
import settings

from hcsr04 import HCSR04

T1, E1 = board.PIN_TRIG_1, board.PIN_ECHO_1
T2, E2 = board.PIN_TRIG_2, board.PIN_ECHO_2
TICKS_PERIOD = 1 << 30


def make(trig=T1, echo=E1, name="L"):
    """Create a sensor on the given pins."""
    return HCSR04(trig, echo, name)


def echo(pin_id=E1):
    """The fake Echo pin a sensor created."""
    return machine.Pin.get(pin_id)


def pulse(fake_clock, width_us, pin_id=E1, delay_us=500):
    """Raise Echo after ``delay_us``, then lower it ``width_us`` later."""
    fake_clock.advance_us(delay_us)
    echo(pin_id).set_input(1)
    fake_clock.advance_us(width_us)
    echo(pin_id).set_input(0)


def no_rise(s):
    """One NO_RISE outcome: start, no edges, result."""
    assert s.start()
    assert s.result() == (HCSR04.NO_RISE, 0)


def busy(s, pin_id=E1):
    """One BUSY outcome: Echo HIGH before start."""
    echo(pin_id).set_input(1)
    assert s.start() is False
    echo(pin_id).set_input(0)


def ok(s, fake_clock, pin_id=E1):
    """One OK outcome."""
    assert s.start()
    pulse(fake_clock, 1000, pin_id)
    assert s.result() == (HCSR04.OK, 1000)


# --- construction -----------------------------------------------------------

def test_constants():
    assert (HCSR04.OK, HCSR04.NO_TARGET, HCSR04.NO_RISE, HCSR04.BUSY) == (0, 1, 2, 3)


def test_construction_pins_and_irq():
    s = make()
    trig = machine.Pin.get(T1)
    e = echo()
    assert trig.mode == machine.Pin.OUT
    assert trig.history == [0]
    assert e.mode == machine.Pin.IN
    assert e.pull == -1
    assert e.irq_hard is True
    assert e.irq_trigger == machine.Pin.IRQ_RISING | machine.Pin.IRQ_FALLING
    assert s.name == "L"
    assert s.faulty is False


def test_default_name_is_empty():
    assert HCSR04(T1, E1).name == ""


def test_capture_storage_is_a_preallocated_int_array():
    s = make()
    assert s._t.typecode in ("i", "I")
    assert len(s._t) == 2


# --- start() ----------------------------------------------------------------

def test_start_sends_a_10us_trig_pulse(fake_clock):
    s = make()
    t0 = fake_clock.us
    assert s.start() is True
    assert machine.Pin.get(T1).history == [0, 1, 0]
    assert fake_clock.us - t0 == 10


# --- result() ---------------------------------------------------------------

def test_ok_path(fake_clock):
    s = make()
    assert s.start()
    pulse(fake_clock, 5831)
    assert s.result() == (HCSR04.OK, 5831)


def test_width_equal_to_timeout_is_ok(fake_clock):
    s = make()
    s.start()
    pulse(fake_clock, settings.ECHO_TIMEOUT_US)
    assert s.result() == (HCSR04.OK, settings.ECHO_TIMEOUT_US)


def test_width_above_timeout_is_no_target(fake_clock):
    s = make()
    s.start()
    pulse(fake_clock, settings.ECHO_TIMEOUT_US + 1)
    assert s.result() == (HCSR04.NO_TARGET, 0)


def test_one_us_echo_is_ok(fake_clock):
    s = make()
    s.start()
    pulse(fake_clock, 1)
    assert s.result() == (HCSR04.OK, 1)


def test_zero_width_is_no_target(fake_clock):
    s = make()
    s.start()
    pulse(fake_clock, 0)
    assert s.result() == (HCSR04.NO_TARGET, 0)


def test_rise_without_fall_is_no_target(fake_clock):
    s = make()
    s.start()
    fake_clock.advance_us(500)
    echo().set_input(1)
    fake_clock.advance_ms(36)
    assert s.result() == (HCSR04.NO_TARGET, 0)


def test_no_edge_is_no_rise():
    s = make()
    s.start()
    assert s.result() == (HCSR04.NO_RISE, 0)


def test_result_without_start_is_no_rise(fake_clock):
    s = make()
    pulse(fake_clock, 1000)          # not armed: ignored
    assert s.result() == (HCSR04.NO_RISE, 0)


def test_settings_timeout_is_read_at_call_time(fake_clock, monkeypatch):
    monkeypatch.setattr(settings, "ECHO_TIMEOUT_US", 2000)
    s = make()
    s.start()
    pulse(fake_clock, 2500)
    assert s.result() == (HCSR04.NO_TARGET, 0)


# --- BUSY -------------------------------------------------------------------

def test_echo_high_before_start_is_busy_and_not_triggered():
    machine.Pin.preset(E1, 1)
    s = make()
    assert s.start() is False
    assert machine.Pin.get(T1).history == [0]


def test_ping_busy_returns_at_once(vrun, fake_clock):
    machine.Pin.preset(E1, 1)
    s = make()
    t0 = fake_clock.us
    assert vrun(s.ping()) == (HCSR04.BUSY, 0)
    assert fake_clock.us == t0
    assert machine.Pin.get(T1).history == [0]


def test_three_busy_pings_make_faulty(vrun):
    machine.Pin.preset(E1, 1)
    s = make()
    for _ in range(3):
        assert vrun(s.ping()) == (HCSR04.BUSY, 0)
    assert s.faulty is True


def test_result_after_busy_start_does_not_reuse_an_old_capture(fake_clock):
    s = make()
    ok(s, fake_clock)
    echo().set_input(1)
    assert s.start() is False
    assert s.result() == (HCSR04.NO_RISE, 0)


# --- late and extra edges -----------------------------------------------------

def test_late_fall_after_result_is_ignored(fake_clock):
    s = make()
    s.start()
    fake_clock.advance_us(500)
    echo().set_input(1)
    fake_clock.advance_ms(36)
    assert s.result() == (HCSR04.NO_TARGET, 0)
    fake_clock.advance_ms(2)
    echo().set_input(0)              # late fall
    s.start()
    assert s.result() == (HCSR04.NO_RISE, 0)


def test_late_full_pulse_after_result_is_ignored(fake_clock):
    s = make()
    s.start()
    assert s.result() == (HCSR04.NO_RISE, 0)
    pulse(fake_clock, 1000)          # after disarm
    assert s._t[0] == 0 and s._t[1] == 0
    s.start()
    assert s.result() == (HCSR04.NO_RISE, 0)


def test_edges_before_start_are_ignored(fake_clock):
    s = make()
    pulse(fake_clock, 3000)
    s.start()
    pulse(fake_clock, 1234)
    assert s.result() == (HCSR04.OK, 1234)


def test_second_pulse_does_not_overwrite_the_first(fake_clock):
    s = make()
    s.start()
    pulse(fake_clock, 1500)
    pulse(fake_clock, 4000)
    assert s.result() == (HCSR04.OK, 1500)


def test_fall_without_rise_is_ignored(fake_clock):
    # A falling edge while armed but before any rise (e.g. a glitch) must not
    # be stored: put the fake Echo HIGH without an edge, arm, then lower it.
    s = make()
    e = echo()
    e._level = 1                     # HIGH without an edge (test-only)
    s._armed = 1
    s._edges = 0
    fake_clock.advance_us(100)
    e.set_input(0)                   # a falling edge with no stored rise
    assert s._edges == 0
    fake_clock.advance_us(100)
    pulse(fake_clock, 777)
    assert s.result() == (HCSR04.OK, 777)


def test_start_clears_the_previous_capture(fake_clock):
    s = make()
    s.start()
    pulse(fake_clock, 2000)
    assert s.result() == (HCSR04.OK, 2000)
    s.start()
    assert s.result() == (HCSR04.NO_RISE, 0)


# --- ticks wraparound -------------------------------------------------------

def test_ticks_wraparound(fake_clock):
    fake_clock.reset(TICKS_PERIOD - 100)
    s = make()
    s.start()
    echo().set_input(1)
    rise = s._t[0]
    fake_clock.advance_us(5831)
    echo().set_input(0)
    assert s._t[1] < rise            # the fall time stamp wrapped
    assert s.result() == (HCSR04.OK, 5831)


# --- fault counter ------------------------------------------------------------

def test_two_no_rise_not_faulty():
    s = make()
    no_rise(s)
    no_rise(s)
    assert s.faulty is False


def test_three_no_rise_faulty():
    s = make()
    for _ in range(3):
        no_rise(s)
    assert s.faulty is True


def test_three_busy_faulty():
    s = make()
    for _ in range(2):
        busy(s)
    assert s.faulty is False
    busy(s)
    assert s.faulty is True


def test_mixed_no_rise_busy_faulty():
    s = make()
    no_rise(s)
    busy(s)
    no_rise(s)
    assert s.faulty is True


def test_ok_clears_fault_and_counting_restarts(fake_clock):
    s = make()
    for _ in range(4):
        no_rise(s)
    assert s.faulty
    ok(s, fake_clock)
    assert s.faulty is False
    no_rise(s)
    no_rise(s)
    assert s.faulty is False
    no_rise(s)
    assert s.faulty is True


def test_no_target_clears_fault(fake_clock):
    s = make()
    for _ in range(3):
        busy(s)
    assert s.faulty
    s.start()
    echo().set_input(1)              # rise, no fall
    assert s.result() == (HCSR04.NO_TARGET, 0)
    assert s.faulty is False


def test_ok_between_breaks_the_run(fake_clock):
    s = make()
    no_rise(s)
    no_rise(s)
    ok(s, fake_clock)
    busy(s)
    no_rise(s)
    assert s.faulty is False


def test_fault_after_is_read_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "FAULT_AFTER", 5)
    s = make()
    for _ in range(4):
        no_rise(s)
    assert s.faulty is False
    no_rise(s)
    assert s.faulty is True


# --- ping() -------------------------------------------------------------------

def test_ping_takes_echo_wait_ms(vrun, fake_clock):
    s = make()
    t0 = time.ticks_us()
    assert vrun(s.ping()) == (HCSR04.NO_RISE, 0)
    # the wait, plus the 10 µs Trig pulse before it
    assert time.ticks_diff(time.ticks_us(), t0) == settings.ECHO_WAIT_MS * 1000 + 10


def test_ping_with_wait_ms(vrun, fake_clock):
    s = make()
    t0 = time.ticks_us()
    vrun(s.ping(wait_ms=50))
    assert time.ticks_diff(time.ticks_us(), t0) == 50 * 1000 + 10


def test_ping_wait_ms_zero_uses_default(vrun, fake_clock):
    s = make()
    t0 = time.ticks_us()
    vrun(s.ping(wait_ms=0))
    assert time.ticks_diff(time.ticks_us(), t0) == settings.ECHO_WAIT_MS * 1000 + 10


def test_ping_float_wait_ms_is_converted(vrun, fake_clock):
    s = make()
    t0 = time.ticks_us()
    vrun(s.ping(wait_ms=50.0))       # the shim rejects floats, like the Pico
    assert time.ticks_diff(time.ticks_us(), t0) == 50 * 1000 + 10


def test_cancelled_ping_then_next_ping_is_clean(vrun, fake_clock):
    s = make()

    async def main():
        task = asyncio.create_task(s.ping())
        await asyncio.sleep_ms(5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        echo().set_input(1)          # the cancelled ping's echo
        await asyncio.sleep_ms(1)
        echo().set_input(0)

        async def sensor():
            await asyncio.sleep_ms(1)
            echo().set_input(1)
            await asyncio.sleep_ms(2)
            echo().set_input(0)

        t = asyncio.create_task(sensor())
        res = await s.ping()
        await t
        return res

    assert vrun(main()) == (HCSR04.OK, 2000)
    assert s.faulty is False


def test_ping_captures_edges_during_the_wait(vrun, fake_clock):
    s = make()

    async def sensor():
        await asyncio.sleep_ms(1)
        echo().set_input(1)
        await asyncio.sleep_ms(6)
        echo().set_input(0)

    async def main():
        task = asyncio.create_task(sensor())
        res = await s.ping()
        await task
        return res

    assert vrun(main()) == (HCSR04.OK, 6000)


def test_ping_ignores_a_fall_after_the_wait(vrun, fake_clock):
    s = make()

    async def sensor():
        await asyncio.sleep_ms(1)
        echo().set_input(1)
        await asyncio.sleep_ms(settings.ECHO_WAIT_MS + 5)
        echo().set_input(0)

    async def main():
        task = asyncio.create_task(sensor())
        res = await s.ping()
        await task
        return res, s._edges

    res, edges = vrun(main())
    assert res == (HCSR04.NO_TARGET, 0)
    assert edges == 1


# --- two instances ------------------------------------------------------------

def test_two_instances_are_independent(fake_clock):
    s1 = make(T1, E1, "L")
    s2 = make(T2, E2, "R")
    s1.start()
    assert machine.Pin.get(T1).history == [0, 1, 0]
    assert machine.Pin.get(T2).history == [0]
    s2.start()
    pulse(fake_clock, 1111, E1)
    pulse(fake_clock, 2222, E2)
    assert s1.result() == (HCSR04.OK, 1111)
    assert s2.result() == (HCSR04.OK, 2222)
    assert (s1.name, s2.name) == ("L", "R")


def test_edges_on_one_sensor_do_not_reach_the_other(fake_clock):
    s1 = make(T1, E1)
    s2 = make(T2, E2)
    s1.start()
    s2.start()
    pulse(fake_clock, 1500, E1)
    assert s2.result() == (HCSR04.NO_RISE, 0)
    assert s1.result() == (HCSR04.OK, 1500)


def test_overlapping_pulses_on_two_sensors(fake_clock):
    s1 = make(T1, E1)
    s2 = make(T2, E2)
    s1.start()
    s2.start()
    echo(E1).set_input(1)
    fake_clock.advance_us(300)
    echo(E2).set_input(1)
    fake_clock.advance_us(1000)
    echo(E1).set_input(0)
    fake_clock.advance_us(1500)
    echo(E2).set_input(0)
    assert s1.result() == (HCSR04.OK, 1300)
    assert s2.result() == (HCSR04.OK, 2500)


def test_fault_counters_are_independent(fake_clock):
    s1 = make(T1, E1)
    s2 = make(T2, E2)
    for _ in range(3):
        no_rise(s2)
        ok(s1, fake_clock, E1)
    assert s2.faulty is True
    assert s1.faulty is False


def test_deinit_of_one_leaves_the_other_working(fake_clock):
    s1 = make(T1, E1)
    s2 = make(T2, E2)
    s1.deinit()
    s2.start()
    pulse(fake_clock, 900, E2)
    assert s2.result() == (HCSR04.OK, 900)


# --- deinit() -----------------------------------------------------------------

def test_deinit_disables_irq_and_leaves_trig_low(fake_clock):
    s = make()
    s.start()
    machine.Pin.get(T1).value(1)
    s.deinit()
    assert echo()._handler is None
    assert machine.Pin.get(T1).value() == 0
    pulse(fake_clock, 1000)
    assert s._edges == 0


# --- implementation constraints -------------------------------------------------

def test_irq_handler_adds_no_attributes(fake_clock):
    s = make()
    before = set(vars(s))
    s.start()
    pulse(fake_clock, 1000)
    pulse(fake_clock, 1000)
    assert set(vars(s)) == before


def test_source_has_no_busy_wait_pulse_measurement():
    path = os.path.join(os.path.dirname(__file__), "..", "..", "src", "hcsr04.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    assert "time_pulse_us" not in src
    assert "import logic" not in src
