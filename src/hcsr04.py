"""Driver for the HC-SR04 ultrasonic sensor (one instance per sensor).

The driver measures time only: how long the Echo pin stays HIGH after a
10 µs Trig pulse. Converting that time into metres is done elsewhere.

How a measurement works:

1. ``start()`` checks that Echo is LOW (a sensor still sending the end of an
   earlier echo is not triggered: BUSY), arms the capture and sends the
   10 µs Trig pulse. That pulse is the only blocking call (``sleep_us(10)``).
2. A hard interrupt on both Echo edges stores ``time.ticks_us()`` of the
   first rising edge and of the first falling edge after it, into a
   preallocated array. The handler allocates nothing, so it runs at once even
   during garbage collection or an I2C transfer.
3. After a fixed wait (``settings.ECHO_WAIT_MS``, awaited, so other tasks keep
   running) ``result()`` disarms the capture and classifies the edges:
   OK, NO_TARGET (rise but no fall in time) or NO_RISE (no edge at all).

A run of ``settings.FAULT_AFTER`` NO_RISE or BUSY outcomes marks the sensor
faulty: a disconnected Echo gives NO_RISE, and an Echo stuck HIGH gives BUSY
every time, so both kinds of failure show.
"""

import asyncio
import time
from array import array

from machine import Pin

import settings


class HCSR04:
    """One HC-SR04 sensor: Trig output, Echo input with an edge interrupt.

    Args:
        trig_pin (int): GPIO number of the Trig line (driven LOW at start).
        echo_pin (int): GPIO number of the Echo line (behind its voltage
            divider; input without pull).
        name (str): a short label, e.g. ``"L"`` or ``"R"``.

    Attributes:
        OK (int): rise and fall within ``settings.ECHO_TIMEOUT_US``; the echo
            time is valid.
        NO_TARGET (int): Echo rose but did not fall in time (nothing within
            about 5 m).
        NO_RISE (int): Echo never went HIGH (wiring or sensor fault).
        BUSY (int): Echo was still HIGH before Trig; the sensor was not pinged.
    """

    OK = 0
    NO_TARGET = 1
    NO_RISE = 2
    BUSY = 3

    def __init__(self, trig_pin, echo_pin, name=""):
        self._name = name
        self._trig = Pin(trig_pin, Pin.OUT, value=0)
        self._echo = Pin(echo_pin, Pin.IN)
        # Storage the interrupt writes into, created before the IRQ is
        # enabled: [t_rise, t_fall] in ticks_us, the number of edges stored
        # (0, 1 or 2) and the armed flag.
        self._t = array("i", [0, 0])
        self._edges = 0
        self._armed = 0
        self._bad = 0          # consecutive NO_RISE or BUSY outcomes
        self._echo.irq(handler=self._irq,
                       trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING, hard=True)

    def _irq(self, pin):
        """Store the time of an Echo edge (hard interrupt, allocation-free).

        Only while armed: the first rising edge becomes ``t_rise``, the first
        falling edge after it ``t_fall``. Every other edge is ignored.

        Args:
            pin (Pin): the Echo pin.
        """
        if not self._armed:
            return
        t = time.ticks_us()
        if pin.value():
            if self._edges == 0:
                self._t[0] = t
                self._edges = 1
        elif self._edges == 1:
            self._t[1] = t
            self._edges = 2

    def start(self):
        """Arm the capture and send the 10 µs Trig pulse.

        If Echo is already HIGH, the sensor is not triggered: the BUSY
        outcome counts towards ``faulty`` and the call returns False.
        Blocks for about 15 µs at most (the Trig pulse).

        Returns:
            bool: True if the sensor was triggered, False if it was BUSY.
        """
        if self._echo.value():
            self._armed = 0
            self._edges = 0      # a result() after this finds nothing: NO_RISE
            self._bad += 1
            return False
        self._armed = 0
        self._edges = 0
        self._t[0] = 0
        self._t[1] = 0
        self._armed = 1
        self._trig.value(1)
        time.sleep_us(10)
        self._trig.value(0)
        return True

    def result(self):
        """Disarm the capture and classify the edges seen since ``start()``.

        Edges that arrive after this call are ignored. Updates the fault
        counter: NO_RISE counts, OK and NO_TARGET reset it. Called without a
        ``start()`` it finds no edges and returns NO_RISE.

        Returns:
            tuple: ``(status, echo_us)``: ``(OK, t_fall - t_rise)`` in µs if
            ``0 < echo_us <= settings.ECHO_TIMEOUT_US``, otherwise
            ``(NO_TARGET, 0)`` or ``(NO_RISE, 0)``.
        """
        self._armed = 0
        edges = self._edges
        if edges == 0:
            self._bad += 1
            return (self.NO_RISE, 0)
        self._bad = 0
        if edges == 2:
            echo_us = time.ticks_diff(self._t[1], self._t[0])
            if 0 < echo_us <= settings.ECHO_TIMEOUT_US:
                return (self.OK, echo_us)
        return (self.NO_TARGET, 0)

    async def ping(self, wait_ms=None):
        """Trigger, wait without blocking, then classify.

        Args:
            wait_ms (int | None): how long to wait after Trig, in ms;
                ``None`` (or 0) uses ``settings.ECHO_WAIT_MS``.

        Returns:
            tuple: ``(BUSY, 0)`` at once if Echo was HIGH, otherwise the
            ``result()`` after the wait.
        """
        if not self.start():
            return (self.BUSY, 0)
        await asyncio.sleep_ms(int(wait_ms or settings.ECHO_WAIT_MS))
        return self.result()

    @property
    def faulty(self):
        """bool: True after ``settings.FAULT_AFTER`` NO_RISE or BUSY outcomes
        in a row (in any mix); any OK or NO_TARGET clears it."""
        return self._bad >= settings.FAULT_AFTER

    @property
    def name(self):
        """str: the label given to the constructor."""
        return self._name

    def deinit(self):
        """Disable the Echo interrupt and leave Trig LOW."""
        self._armed = 0
        self._echo.irq(handler=None)
        self._trig.value(0)
