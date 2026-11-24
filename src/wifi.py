"""Wi-Fi station connection for the Pico W, without blocking the event loop.

``WiFi`` switches the radio on in its constructor. ``connect()`` then polls
the connection status with ``await asyncio.sleep_ms(250)``, so the other tasks
keep running while it waits, and it gives up at once when the access point
reports a failure (e.g. a wrong password).

The SSID and password are passed in by ``main.py``. This module never prints
or logs them.

New code; it replaces the blocking connect loop of the course's lab 7
(BPC-DE2, VUT FEKT) with an asynchronous one.
"""

import asyncio
import time

import network

import settings

_POLL_MS = 250


class WiFi:
    """The Pico W's station interface (``network.WLAN(network.STA_IF)``).

    Args:
        ssid (str): the network name.
        password (str): the network password. Kept only privately; never
            printed.

    The constructor calls ``active(True)``, which blocks while the radio
    starts (a few hundred ms). Construct ``WiFi`` before the event loop
    starts, never inside a task.
    """

    def __init__(self, ssid, password):
        self._ssid = ssid
        self._password = password
        self._connecting = False
        self._wlan = network.WLAN(network.STA_IF)
        self._wlan.active(True)

    async def connect(self, timeout_s=None):
        """Join the network, waiting without blocking.

        Calls ``wlan.connect(ssid, password)``, then reads ``wlan.status()``
        every 250 ms (``await asyncio.sleep_ms(250)`` in between).

        Args:
            timeout_s (int | None): how long to wait in seconds; None uses
                ``settings.WIFI_CONNECT_TIMEOUT_S``.

        Returns:
            bool: True once connected (status ``STAT_GOT_IP``). False at once
            if the status turns negative (wrong password, no access point,
            other failure) or the radio raises ``OSError``, or after
            ``timeout_s``.
        """
        if timeout_s is None:
            timeout_s = settings.WIFI_CONNECT_TIMEOUT_S
        self._connecting = True
        try:
            try:
                self._wlan.connect(self._ssid, self._password)
            except OSError:   # a radio or driver error
                return False
            deadline = time.ticks_add(time.ticks_ms(), int(timeout_s * 1000))
            while True:
                status = self._wlan.status()
                if status < 0:
                    return False
                if status == network.STAT_GOT_IP:
                    return True
                if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                    return False
                await asyncio.sleep_ms(_POLL_MS)
        finally:
            self._connecting = False

    def is_connected(self):
        """Whether the station has a link and an IP address.

        Returns:
            bool: ``wlan.isconnected()``.
        """
        return self._wlan.isconnected()

    @property
    def state(self):
        """The connection state, for the display and the logger.

        Returns:
            str: ``'connecting'`` while ``connect()`` runs, otherwise ``'up'``
            if connected and ``'down'`` if not.
        """
        if self._connecting:
            return "connecting"
        return "up" if self._wlan.isconnected() else "down"

    def disconnect(self):
        """Leave the network (the radio stays on)."""
        self._wlan.disconnect()
