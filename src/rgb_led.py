"""Driver for the RGB LED module.

The LED shows the colour of the current zone. Each channel (red, green, blue)
is set by PWM from 0 to 255, so mixed colours like yellow can be tuned. Our
module is common cathode; common-anode modules work too (the duty is inverted).

Based on our ``RGBLed`` class from lab 3 of BPC-DE2 (FEKT VUT), extended from
on/off to PWM colours.
"""

from machine import Pin, PWM

import board
import settings


def _clamp(v):
    """Clamp one channel value to an integer 0-255.

    Args:
        v (int | float): the requested channel value.

    Returns:
        int: the value limited to 0-255.
    """
    v = int(v)
    if v < 0:
        return 0
    if v > 255:
        return 255
    return v


class RGBLed:
    """An RGB LED on three PWM pins, one 0-255 value per channel.

    All three channels run at one PWM frequency: GP12 and GP13 share PWM
    slice 6 (GP14 is on slice 7), so they could not run at different
    frequencies anyway. The pins keep the default 4 mA drive strength, because
    the rp2 port's ``Pin`` has no drive keyword.

    Args:
        r_pin (int): GPIO number of the red channel.
        g_pin (int): GPIO number of the green channel.
        b_pin (int): GPIO number of the blue channel.
        freq (int | None): PWM frequency in Hz; ``None`` uses
            ``settings.PWM_FREQ_HZ``.
        common_anode (bool | None): ``True`` for a common-anode module (the
            duty is inverted); ``None`` uses ``board.RGB_COMMON_ANODE``.

    The LED starts off.
    """

    def __init__(self, r_pin, g_pin, b_pin, freq=None, common_anode=None):
        if freq is None:
            freq = settings.PWM_FREQ_HZ
        if common_anode is None:
            common_anode = board.RGB_COMMON_ANODE
        self._anode = bool(common_anode)
        self._pwms = (PWM(Pin(r_pin)), PWM(Pin(g_pin)), PWM(Pin(b_pin)))
        # Set the frequency on every channel before any duty: GP12 and GP13
        # share a slice, and a frequency change rescales the slice's counter.
        for pwm in self._pwms:
            pwm.freq(int(freq))
        self._current = settings.COLOR_OFF
        self.off()

    def color(self, rgb):
        """Show a colour.

        Each value is clamped to 0-255 and written as
        ``duty_u16 = value * 257`` (0-65535), inverted for a common-anode
        module.

        Args:
            rgb (tuple): ``(r, g, b)``, each 0-255.
        """
        r = _clamp(rgb[0])
        g = _clamp(rgb[1])
        b = _clamp(rgb[2])
        pwms = self._pwms
        if self._anode:
            pwms[0].duty_u16(65535 - r * 257)
            pwms[1].duty_u16(65535 - g * 257)
            pwms[2].duty_u16(65535 - b * 257)
        else:
            pwms[0].duty_u16(r * 257)
            pwms[1].duty_u16(g * 257)
            pwms[2].duty_u16(b * 257)
        self._current = (r, g, b)

    def off(self):
        """Turn all three channels off (the same as ``color(settings.COLOR_OFF)``)."""
        self.color(settings.COLOR_OFF)

    @property
    def current(self):
        """tuple: the last ``(r, g, b)`` set, after clamping, each 0-255."""
        return self._current

    def deinit(self):
        """Turn the LED off, then stop the three PWM outputs.

        After this the pins are no longer driven. On a common-anode module a
        pin left low would light its channel, so create a new ``RGBLed`` (or
        drive the pins high) if the LED must stay dark afterwards.
        """
        self.off()
        for pwm in self._pwms:
            pwm.deinit()
