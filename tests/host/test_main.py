"""Host tests for src/main.py: safe boot, start order and the safe exit path."""

import ast
import asyncio
import os
import sys

import pytest

import board
import machine
import main
import network
import settings
import tasks
from button import Button

SRC_MAIN = os.path.join(os.path.dirname(__file__), "..", "..", "src", "main.py")


@pytest.fixture(autouse=True)
def no_secret_in_output(capsys):
    """After every test: captured output never holds a (fake) secret."""
    yield
    out, err = capsys.readouterr()
    for secret in ("test-ssid", "test-password", "TEST-WRITE-KEY"):
        assert secret not in out
        assert secret not in err


@pytest.fixture
def oled():
    """A fake OLED on the I2C bus, so the display works."""
    dev = machine.I2CDevice()
    machine.I2C(board.I2C_ID).attach(board.ADDR_OLED, dev)
    return dev


@pytest.fixture
def no_exit(monkeypatch):
    def fail(*args):
        raise AssertionError("sys.exit() must never be called")
    monkeypatch.setattr(sys, "exit", fail)


def relay_off_level():
    return 1 if board.RELAY_ACTIVE_LOW else 0


def led_duties():
    return [machine.PWM.get(p).duty_u16() for p in (board.PIN_LED_R, board.PIN_LED_G, board.PIN_LED_B)]


def fail_after(monkeypatch, n, exc, outputs_on=False):
    """Make Button.poll raise exc on its n-th call (inside mode_task).

    With outputs_on, the relay coil and the LED are switched on just before,
    so the test sees run()'s finally switch them off.
    """
    calls = {"n": 0}
    real = Button.poll

    def poll(self, now_ms=None):
        calls["n"] += 1
        if calls["n"] >= n:
            if outputs_on:
                machine.Pin.get(board.PIN_RELAY).value(1 - relay_off_level())
                machine.PWM.get(board.PIN_LED_R).duty_u16(65535)
            raise exc
        return real(self, now_ms)

    monkeypatch.setattr(Button, "poll", poll)


def record_messages(monkeypatch):
    """Record every Display.message() call and every show_now()."""
    from display import Display
    log = []
    real_message, real_show = Display.message, Display.show_now

    def message(self, line1, line2=""):
        log.append(("message", line1, line2))
        real_message(self, line1, line2)

    def show_now(self):
        log.append(("show_now", self.ok))
        real_show(self)

    monkeypatch.setattr(Display, "message", message)
    monkeypatch.setattr(Display, "show_now", show_now)
    return log


# --- safe boot -------------------------------------------------------------

def test_safe_boot_returns_without_starting(monkeypatch, capsys, no_exit):
    machine.Pin.preset(board.PIN_BUTTON, 0)          # held during reset
    started = []
    monkeypatch.setattr(asyncio, "run", lambda coro: started.append(coro))
    assert main.run() is None
    assert started == []
    assert "safe boot: app not started" in capsys.readouterr().out
    with pytest.raises(KeyError):
        machine.Pin.get(board.PIN_RELAY)             # no output was even built


def test_emergency_buffer_is_allocated_first(monkeypatch, no_exit):
    import micropython
    sizes = []
    monkeypatch.setattr(micropython, "alloc_emergency_exception_buf", lambda n: sizes.append(n))
    machine.Pin.preset(board.PIN_BUTTON, 0)
    main.run()
    assert sizes == [100]


# --- start order and boot screen -------------------------------------------

def test_relay_is_built_first_then_led(monkeypatch, oled, no_exit):
    order = []
    real_relay, real_led = main.Relay, main.RGBLed

    def relay(pin):
        order.append("relay")
        return real_relay(pin)

    def led(*pins):
        order.append("led")
        return real_led(*pins)

    monkeypatch.setattr(main, "Relay", relay)
    monkeypatch.setattr(main, "RGBLed", led)
    monkeypatch.setattr(asyncio, "run", lambda coro: (order.append("run"), coro.close()))
    main.run()
    assert order == ["relay", "led", "run"]
    assert machine.Pin.get(board.PIN_RELAY).history[0] == relay_off_level()
    assert led_duties() == [0, 0, 0]


def test_boot_screen_is_shown_before_the_loop(monkeypatch, oled, no_exit):
    log = record_messages(monkeypatch)
    seen = []
    monkeypatch.setattr(asyncio, "run", lambda coro: (seen.append(list(log)), coro.close()))
    main.run()
    assert seen[0][0][0] == "message"
    assert seen[0][1] == ("show_now", True)
    assert len(oled.received) > 8                    # init commands + 8 pages


def test_i2c_uses_the_board_pins_and_speed(monkeypatch, oled, no_exit):
    monkeypatch.setattr(asyncio, "run", lambda coro: coro.close())
    main.run()
    bus = machine.I2C.get(board.I2C_ID)
    assert bus.freq == settings.I2C_FREQ_HZ
    assert bus.sda.id == board.PIN_SDA and bus.scl.id == board.PIN_SCL


# --- the exception path ----------------------------------------------------

def test_exception_in_a_task_leaves_outputs_safe_and_shows_fatal(monkeypatch, capsys, oled, no_exit, no_config):
    log = record_messages(monkeypatch)
    fail_after(monkeypatch, 30, RuntimeError("boom"), outputs_on=True)   # about 600 ms in
    main.run()
    out = capsys.readouterr().out
    assert "RuntimeError" in out and "boom" in out
    assert ("message", "FATAL ERROR", "RuntimeError") in log
    assert log[-1] == ("show_now", True)
    relay_pin = machine.Pin.get(board.PIN_RELAY)
    assert relay_pin.history[-2:] == [1 - relay_off_level(), relay_off_level()]
    assert led_duties() == [0, 0, 0]


def test_fatal_screen_skipped_when_display_missing(monkeypatch, capsys, no_exit, no_config):
    log = record_messages(monkeypatch)
    fail_after(monkeypatch, 5, ValueError("bad"))
    main.run()                                        # no OLED attached
    assert "ValueError" in capsys.readouterr().out
    assert ("message", "FATAL ERROR", "ValueError") not in log
    assert machine.Pin.get(board.PIN_RELAY).value() == relay_off_level()
    assert led_duties() == [0, 0, 0]


def test_exception_while_building_is_handled(monkeypatch, capsys, oled, no_exit):
    def broken(*args, **kwargs):
        raise OSError(19, "no device")
    monkeypatch.setattr(main, "HCSR04", broken)
    main.run()
    assert "OSError" in capsys.readouterr().out
    assert machine.Pin.get(board.PIN_RELAY).value() == relay_off_level()


def test_keyboard_interrupt_still_leaves_outputs_safe(monkeypatch, oled, no_exit, no_config):
    log = record_messages(monkeypatch)
    machine.Pin.preset(board.PIN_ECHO_1, 0)

    def interrupt(coro):
        coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(asyncio, "run", interrupt)
    with pytest.raises(KeyboardInterrupt):
        main.run()
    assert not any(e[0] == "message" and e[1] == "FATAL ERROR" for e in log)
    assert machine.Pin.get(board.PIN_RELAY).value() == relay_off_level()
    assert led_duties() == [0, 0, 0]


# --- main(): which tasks start ------------------------------------------------

def count_tasks(monkeypatch):
    names = []
    for name in ("mode_task", "ranging_task", "indicator_task", "display_task", "logger_task"):
        def make(n):
            async def fake(*args):
                names.append(n)
            return fake
        monkeypatch.setattr(tasks, name, make(name))
    return names


def test_main_starts_five_tasks_with_config(monkeypatch, oled, no_exit):
    names = count_tasks(monkeypatch)
    network.WLAN.script_next([network.STAT_CONNECTING])
    main.run()                                        # every fake task ends at once
    assert sorted(names) == sorted(["mode_task", "ranging_task", "indicator_task",
                                    "display_task", "logger_task"])
    wlan = network.WLAN.last()
    assert ("active", True) in wlan.calls             # radio started before the loop


def test_logger_not_started_without_config(monkeypatch, oled, no_exit, no_config):
    names = count_tasks(monkeypatch)
    main.run()
    assert "logger_task" not in names and len(names) == 4
    assert network._instances == []                   # no WLAN created


def test_no_config_system_runs_and_wifi_stays_down(vrun, oled, no_config):
    import logic
    from display import Display
    from hcsr04 import HCSR04
    from relay import Relay
    from rgb_led import RGBLed
    from state import State
    state = State()
    relay = Relay(board.PIN_RELAY)
    led = RGBLed(board.PIN_LED_R, board.PIN_LED_G, board.PIN_LED_B)
    disp = Display(machine.I2C(board.I2C_ID))
    s1 = HCSR04(board.PIN_TRIG_1, board.PIN_ECHO_1, "L")
    s2 = HCSR04(board.PIN_TRIG_2, board.PIN_ECHO_2, "R")
    vrun.for_ms(main.main(state, relay, led, disp, s1, s2, Button(board.PIN_BUTTON),
                          logic.EventDetector()), 3000)
    assert state.wifi == "down" and state.cloud == "--" and not state.synced
    assert state.faults == {"S1", "S2"}               # no echoes from the fake pins


def test_file_ends_with_the_entry_guard():
    with open(SRC_MAIN, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    last = tree.body[-1]
    assert isinstance(last, ast.If)
    assert ast.unparse(last.test) == "__name__ == '__main__'"
    assert ast.unparse(last.body[0]) == "run()"


def test_main_never_calls_sys_exit():
    with open(SRC_MAIN, encoding="utf-8") as f:
        assert "sys.exit" not in f.read().replace("``sys.exit()``", "")
