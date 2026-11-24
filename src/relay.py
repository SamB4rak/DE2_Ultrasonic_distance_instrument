"""Driver for the relay module.

The relay clicks once per flash of the RGB LED. Our module (SRD-05VDC-SL-C on
a small board) is switched on by a LOW level on its IN pin; modules that
switch on HIGH work too (``active_low=False``). Nothing is connected to the
relay contacts.

The pin is created already at the OFF level, in the same call that makes it
an output, so the relay never clicks while the program starts.

The relay is rated for 5 operations per second, so callers start pulses at
least 200 ms apart.
"""

import asyncio

from machine import Pin

import board
import settings


class Relay:
    """A relay module on one GPIO, with polarity-independent on/off.

    Args:
        pin (int): GPIO number of the module's IN pin.
        active_low (bool | None): ``True`` if a LOW level switches the relay
            on; ``None`` uses ``board.RELAY_ACTIVE_LOW``.

    The relay starts off.
    """

    def __init__(self, pin, active_low=None):
        if active_low is None:
            active_low = board.RELAY_ACTIVE_LOW
        self._on_level = 0 if active_low else 1
        self._off_level = 1 - self._on_level
        # One call: the pin becomes an output already at the OFF level.
        self._pin = Pin(pin, Pin.OUT, value=self._off_level)

    def on(self):
        """Energise the coil (the relay clicks on)."""
        self._pin.value(self._on_level)

    def off(self):
        """Release the coil (the relay clicks off)."""
        self._pin.value(self._off_level)

    def is_on(self):
        """Tell whether the coil is energised, whatever the polarity.

        Returns:
            bool: ``True`` if the pin is at the ON level.
        """
        return self._pin.value() == self._on_level

    async def pulse(self, ms=None):
        """Switch the relay on for a short time, then off again.

        The relay is switched off in a ``finally`` block, so it ends up off
        even if the task is cancelled or fails during the pulse.

        Args:
            ms (int | None): pulse length in ms; ``None`` (or 0) uses
                ``settings.RELAY_PULSE_MS``.
        """
        self.on()
        try:
            await asyncio.sleep_ms(int(ms or settings.RELAY_PULSE_MS))
        finally:
            self.off()
