"""Driver for the push button.

The button switches the instrument between RUN and OFF. It connects its pin
to GND when pressed and uses the Pico's internal pull-up, so the pin reads 1
when released and 0 when pressed.

The button is polled (no interrupt): the program calls :meth:`Button.poll`
every ``settings.BUTTON_POLL_MS``. Contacts bounce for a few milliseconds, so
a new level counts only after it has stayed the same for
``settings.BUTTON_DEBOUNCE_MS``. Each press is reported once.

Holding the button during reset skips the automatic start
(:meth:`Button.held_at_boot`), which keeps the REPL free for development.

Based on our ``Button`` class from lab 3 of BPC-DE2 (FEKT VUT), method
``was_pressed``; the time of each poll can now be passed in for testing.
"""

import time

from machine import Pin

import settings


class Button:
    """A debounced, active-low push button on one GPIO.

    Args:
        pin (int): GPIO number; the other side of the button goes to GND.
        debounce_ms (int | None): how long (ms) a new level must stay stable
            before it counts; ``None`` uses ``settings.BUTTON_DEBOUNCE_MS``.

    The debounced state starts at the level read now, so a button already
    held at start-up gives no press until it is released and pressed again.
    """

    def __init__(self, pin, debounce_ms=None):
        if debounce_ms is None:
            debounce_ms = settings.BUTTON_DEBOUNCE_MS
        self._debounce_ms = int(debounce_ms)
        self._pin = Pin(pin, Pin.IN, Pin.PULL_UP)
        self._raw = self._pin.value()      # last level read
        self._stable = self._raw           # debounced level (0 = pressed)
        # Poll time of the last raw change; only read after one, since
        # _raw and _stable start equal.
        self._changed_ms = 0

    def poll(self, now_ms=None):
        """Read the pin and update the debounced state.

        Call it every ``settings.BUTTON_POLL_MS``. The debounced state changes
        only after the raw level has stayed the same for ``debounce_ms``
        (with 20 ms polls and 40 ms debounce: on the 3rd poll that sees the
        new level).

        Args:
            now_ms (int | None): the current ``time.ticks_ms()`` value;
                ``None`` reads the clock.

        Returns:
            bool: ``True`` exactly once per press (the released-to-pressed
            change of the debounced state), otherwise ``False``.
        """
        now = time.ticks_ms() if now_ms is None else now_ms
        raw = self._pin.value()
        if raw != self._raw:
            self._raw = raw
            self._changed_ms = now
        if raw != self._stable and \
                time.ticks_diff(now, self._changed_ms) >= self._debounce_ms:
            self._stable = raw
            return raw == 0
        return False

    def is_down(self):
        """Tell whether the button is pressed, after debouncing.

        Returns:
            bool: ``True`` while the debounced state is pressed.
        """
        return self._stable == 0

    @staticmethod
    def held_at_boot(pin):
        """Tell whether the button is held right now (used at start-up).

        Sets the pin up as an input with the pull-up itself, so it works
        before any ``Button`` exists.

        Args:
            pin (int): GPIO number of the button.

        Returns:
            bool: ``True`` if the pin reads LOW (pressed).
        """
        return Pin(pin, Pin.IN, Pin.PULL_UP).value() == 0
