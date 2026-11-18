"""Host tests for src/rgb_led.py (the PWM RGB LED driver)."""

import pytest

import board
import machine
import settings

import rgb_led

R, G, B = board.PIN_LED_R, board.PIN_LED_G, board.PIN_LED_B


def duties():
    """Return the (r, g, b) duty_u16 values of the three fake PWMs."""
    return tuple(machine.PWM.get(p).duty_u16() for p in (R, G, B))


def make(**kwargs):
    """Create an RGBLed on the board's LED pins."""
    return rgb_led.RGBLed(R, G, B, **kwargs)


def test_construction_all_off_one_shared_frequency():
    led = make()
    assert duties() == (0, 0, 0)
    freqs = {machine.PWM.get(p).freq() for p in (R, G, B)}
    assert freqs == {settings.PWM_FREQ_HZ}
    assert led.current == (0, 0, 0)


def test_frequency_is_set_on_all_channels_before_any_duty(monkeypatch):
    # GP12 and GP13 share one PWM slice: a frequency write after a duty write
    # would rescale it, so every freq() must come before the first duty.
    created = []
    freq_calls = []
    orig_init = machine.PWM.__init__
    orig_freq = machine.PWM.freq

    def init(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        created.append(self)

    def freq(self, value=None):
        if value is not None:
            assert all(not pwm.duty_history for pwm in created)
            freq_calls.append(value)
        return orig_freq(self, value)

    monkeypatch.setattr(machine.PWM, "__init__", init)
    monkeypatch.setattr(machine.PWM, "freq", freq)
    make()
    assert freq_calls == [settings.PWM_FREQ_HZ] * 3
    for p in (R, G, B):
        assert machine.PWM.get(p).duty_history == [0]


def test_float_freq_is_passed_as_int():
    make(freq=500.0)
    assert {machine.PWM.get(p).freq() for p in (R, G, B)} == {500}


def test_no_drive_strength_register_writes():
    make()
    assert machine.mem32.writes == []


def test_freq_argument_overrides_setting():
    make(freq=500)
    assert {machine.PWM.get(p).freq() for p in (R, G, B)} == {500}


def test_pins_are_the_given_gpios():
    make()
    for p in (R, G, B):
        assert machine.PWM.get(p).id == p


def test_red_full():
    led = make()
    led.color((255, 0, 0))
    assert duties() == (65535, 0, 0)


def test_green_and_blue_map_to_their_channels():
    led = make()
    led.color((0, 255, 0))
    assert duties() == (0, 65535, 0)
    led.color((0, 0, 255))
    assert duties() == (0, 0, 65535)


def test_mid_value_scales_by_257():
    led = make()
    led.color((128, 1, 254))
    assert duties() == (128 * 257, 257, 254 * 257)


def test_common_anode_inverts():
    led = make(common_anode=True)
    assert duties() == (65535, 65535, 65535)  # starts dark
    led.color((255, 0, 0))
    assert duties() == (0, 65535, 65535)
    led.color((128, 0, 0))
    assert duties()[0] == 65535 - 128 * 257
    assert led.current == (128, 0, 0)


def test_common_anode_default_comes_from_board(monkeypatch):
    monkeypatch.setattr(board, "RGB_COMMON_ANODE", True)
    led = make()
    assert duties() == (65535, 65535, 65535)
    led.color((0, 255, 0))
    assert duties() == (65535, 0, 65535)


def test_explicit_common_cathode_overrides_board(monkeypatch):
    monkeypatch.setattr(board, "RGB_COMMON_ANODE", True)
    led = make(common_anode=False)
    led.color((255, 0, 0))
    assert duties() == (65535, 0, 0)


def test_values_are_clamped():
    led = make()
    led.color((300, -5, 256))
    assert duties() == (65535, 0, 65535)
    assert led.current == (255, 0, 255)


def test_float_values_become_ints():
    led = make()
    led.color((127.9, 0.0, 255.0))
    assert duties() == (127 * 257, 0, 65535)
    assert led.current == (127, 0, 255)


def test_clamped_common_anode():
    led = make(common_anode=True)
    led.color((999, -1, 0))
    assert duties() == (0, 65535, 65535)


def test_off_gives_all_zero():
    led = make()
    led.color((255, 255, 255))
    led.off()
    assert duties() == (0, 0, 0)
    assert led.current == settings.COLOR_OFF


def test_current_tracks_last_colour():
    led = make()
    for c in (settings.COLOR_GREEN, settings.COLOR_YELLOW, settings.COLOR_RED):
        led.color(c)
        assert led.current == tuple(c)
    assert isinstance(led.current, tuple)


def test_deinit_turns_off_then_stops_pwms():
    led = make()
    led.color((255, 255, 255))
    led.deinit()
    for p in (R, G, B):
        pwm = machine.PWM.get(p)
        assert pwm.duty_u16() == 0
        assert pwm.deinited


@pytest.mark.parametrize("anode, dark", [(False, 0), (True, 65535)])
def test_deinit_writes_off_before_stopping(monkeypatch, anode, dark):
    seen = []
    orig_deinit = machine.PWM.deinit

    def deinit(self):
        seen.append(self.duty_u16())
        orig_deinit(self)

    monkeypatch.setattr(machine.PWM, "deinit", deinit)
    led = make(common_anode=anode)
    led.color((255, 128, 1))
    led.deinit()
    assert seen == [dark, dark, dark]


@pytest.mark.parametrize("name", [n for n in dir(settings) if n.startswith("COLOR_")])
def test_every_setting_colour_is_valid(name):
    c = getattr(settings, name)
    assert isinstance(c, tuple) and len(c) == 3
    assert all(isinstance(v, int) and 0 <= v <= 255 for v in c)
    led = make()
    led.color(c)
    assert duties() == tuple(v * 257 for v in c)

