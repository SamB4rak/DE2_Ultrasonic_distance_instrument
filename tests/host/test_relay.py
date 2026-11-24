"""Host tests for src/relay.py (the relay module driver)."""

import asyncio
import inspect
import time

import pytest

import board
import machine
import settings

import relay

PIN = board.PIN_RELAY


def pin():
    """Return the fake Pin the driver created for the relay."""
    return machine.Pin.get(PIN)


# --- construction ------------------------------------------------------------

def test_active_low_starts_off_in_one_call():
    r = relay.Relay(PIN, active_low=True)
    assert pin().mode == machine.Pin.OUT
    assert pin().history == [1]          # the first level ever written is OFF
    assert r.is_on() is False


def test_active_high_starts_off_in_one_call():
    r = relay.Relay(PIN, active_low=False)
    assert pin().mode == machine.Pin.OUT
    assert pin().history == [0]
    assert r.is_on() is False


def test_default_polarity_is_the_board_setting(monkeypatch):
    relay.Relay(PIN)
    assert pin().history == [1 if board.RELAY_ACTIVE_LOW else 0]
    monkeypatch.setattr(board, "RELAY_ACTIVE_LOW", False)
    relay.Relay(PIN)
    assert pin().history == [0]


def test_board_default_is_active_low():
    assert board.RELAY_ACTIVE_LOW is True
    relay.Relay(PIN)
    assert pin().history == [1]


@pytest.mark.parametrize("active_low, off", [(True, 1), (False, 0)])
def test_pin_is_built_at_off_level_in_the_constructor(monkeypatch, active_low, off):
    # A separate Pin(...) then .value(off) would leave the pin briefly at the
    # wrong level; the OFF level must be part of the constructor call.
    calls = []
    real_pin = relay.Pin

    def recorder(*args, **kwargs):
        calls.append((args, kwargs))
        return real_pin(*args, **kwargs)

    recorder.OUT = real_pin.OUT
    monkeypatch.setattr(relay, "Pin", recorder)
    relay.Relay(PIN, active_low=active_low)
    assert calls == [((PIN, machine.Pin.OUT), {"value": off})]
    assert pin().history == [off]


def test_uses_the_given_gpio():
    relay.Relay(7, active_low=True)
    assert machine.Pin.get(7).history == [1]


# --- polarity ----------------------------------------------------------------

def test_active_low_on_is_0_off_is_1():
    r = relay.Relay(PIN, active_low=True)
    r.on()
    assert pin().value() == 0
    assert r.is_on() is True
    r.off()
    assert pin().value() == 1
    assert r.is_on() is False
    assert pin().history == [1, 0, 1]


def test_active_high_on_is_1_off_is_0():
    r = relay.Relay(PIN, active_low=False)
    r.on()
    assert pin().value() == 1
    assert r.is_on() is True
    r.off()
    assert pin().value() == 0
    assert r.is_on() is False
    assert pin().history == [0, 1, 0]


@pytest.mark.parametrize("active_low", [True, False])
def test_repeated_on_off_are_idempotent(active_low):
    r = relay.Relay(PIN, active_low=active_low)
    r.on()
    r.on()
    assert r.is_on() is True
    r.off()
    r.off()
    assert r.is_on() is False
    off = 1 if active_low else 0
    assert pin().value() == off


# --- pulse -------------------------------------------------------------------

def test_pulse_is_a_coroutine_with_default_args():
    assert inspect.iscoroutinefunction(relay.Relay.pulse)
    assert inspect.signature(relay.Relay.pulse).parameters["ms"].default is None
    assert inspect.signature(relay.Relay.__init__).parameters["active_low"].default is None


@pytest.mark.parametrize("active_low", [True, False])
def test_pulse_default_length_on_virtual_clock(vrun, active_low):
    r = relay.Relay(PIN, active_low=active_low)
    seen = []

    async def watch():
        task = asyncio.create_task(r.pulse())
        await asyncio.sleep_ms(0)
        seen.append((time.ticks_ms(), r.is_on()))
        await asyncio.sleep_ms(settings.RELAY_PULSE_MS - 1)
        seen.append((time.ticks_ms(), r.is_on()))
        await task
        seen.append((time.ticks_ms(), r.is_on()))

    vrun(watch())
    assert seen == [
        (0, True),
        (settings.RELAY_PULSE_MS - 1, True),
        (settings.RELAY_PULSE_MS, False),
    ]
    off = 1 if active_low else 0
    assert pin().history == [off, 1 - off, off]


def test_pulse_explicit_length(vrun):
    r = relay.Relay(PIN, active_low=True)

    async def go():
        await r.pulse(120)
        return time.ticks_ms()

    assert vrun(go()) == 120
    assert r.is_on() is False
    assert pin().history == [1, 0, 1]


def test_pulse_zero_uses_the_default_length(vrun):
    r = relay.Relay(PIN, active_low=True)

    async def go():
        await r.pulse(0)
        return time.ticks_ms()

    assert vrun(go()) == settings.RELAY_PULSE_MS
    assert pin().history == [1, 0, 1]


def test_pulse_across_ticks_wraparound(vrun, fake_clock):
    # Start 20 ms before ticks_ms() wraps: the pulse still lasts 50 ms.
    fake_clock.reset(((1 << 30) - 20) * 1000)
    r = relay.Relay(PIN, active_low=True)

    async def go():
        t0 = time.ticks_ms()
        await r.pulse(50)
        return t0, time.ticks_ms()

    t0, t1 = vrun(go())
    assert t1 < t0                      # the counter wrapped
    assert time.ticks_diff(t1, t0) == 50
    assert r.is_on() is False


def test_pulse_float_length_is_converted_to_int(vrun):
    r = relay.Relay(PIN, active_low=True)

    async def go():
        await r.pulse(50.0)        # the shim raises TypeError on a float
        return time.ticks_ms()

    assert vrun(go()) == 50
    assert r.is_on() is False


@pytest.mark.parametrize("active_low", [True, False])
def test_pulse_cancelled_mid_pulse_leaves_relay_off(vrun, active_low):
    r = relay.Relay(PIN, active_low=active_low)
    out = {}

    async def go():
        task = asyncio.create_task(r.pulse(50))
        await asyncio.sleep_ms(20)
        out["on_at_20"] = r.is_on()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            out["cancelled"] = True
        out["t_ms"] = time.ticks_ms()
        out["on_after"] = r.is_on()

    vrun(go())
    assert out == {"on_at_20": True, "cancelled": True, "t_ms": 20, "on_after": False}
    off = 1 if active_low else 0
    assert pin().value() == off
    assert pin().history == [off, 1 - off, off]


def test_pulse_left_running_when_loop_ends_is_switched_off(vrun):
    # vrun cancels leftover tasks like asyncio.run, so the finally still runs.
    r = relay.Relay(PIN, active_low=True)

    async def go():
        asyncio.create_task(r.pulse(1000))
        await asyncio.sleep_ms(10)
        return r.is_on()

    assert vrun(go()) is True
    assert r.is_on() is False


def test_pulse_error_during_sleep_leaves_relay_off(vrun, monkeypatch):
    r = relay.Relay(PIN, active_low=True)

    async def broken_sleep(ms):
        raise RuntimeError("boom")

    monkeypatch.setattr(asyncio, "sleep_ms", broken_sleep)
    with pytest.raises(RuntimeError):
        vrun(r.pulse())
    assert r.is_on() is False
    assert pin().history == [1, 0, 1]


def test_two_pulses_200_ms_apart_give_two_clicks(vrun):
    r = relay.Relay(PIN, active_low=True)
    starts = []

    async def go():
        for _ in range(2):
            start = time.ticks_ms()
            starts.append(start)
            asyncio.create_task(r.pulse())
            await asyncio.sleep_ms(200)

    vrun(go())
    assert starts == [0, 200]
    assert pin().history == [1, 0, 1, 0, 1]
    assert r.is_on() is False
