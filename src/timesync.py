"""Wall-clock time (UTC) for the event time stamps, from NTP.

``TimeSync`` asks an NTP server for the time once and remembers one anchor:
the Unix second it got and the ``time.ticks_ms()`` value right after the
reply. Any later ``ticks_ms`` value is turned into a Unix second from that
anchor, so the events carry their own time even while Wi-Fi is down. The RTC
is never set or read.

``sync()`` blocks: ``ntptime.time()`` does its own DNS lookup (up to several
seconds when a DNS server doesn't answer), then one UDP exchange with a 1 s
timeout. MicroPython has no asynchronous NTP, so this is one of the few
accepted blocking calls. The logger calls it only right after a (re)connect,
every ``settings.NTP_RESYNC_S`` (6 h), and while not synced at most every
``settings.NTP_RETRY_S``.

Uses MicroPython's ``ntptime`` module (frozen into the Pico W firmware; MIT
licence, micropython-lib), as in lab 7 of the course (BPC-DE2, VUT FEKT).
``stamp()`` converts days to a date with Howard Hinnant's public-domain
``civil_from_days`` algorithm (http://howardhinnant.github.io/date_algorithms.html).
"""

import time

import ntptime

import settings

_EPOCH_2000_S = 946_684_800   # Unix seconds of 2000-01-01 00:00:00 UTC


def _civil_from_days(days):
    """Turn days since 1970-01-01 into a date (proleptic Gregorian).

    Args:
        days (int): days since 1970-01-01.

    Returns:
        tuple: ``(year, month, day)``.
    """
    z = days + 719_468
    era = z // 146_097
    doe = z - era * 146_097
    yoe = (doe - doe // 1460 + doe // 36_524 - doe // 146_096) // 365
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    day = doy - (153 * mp + 2) // 5 + 1
    month = mp + 3 if mp < 10 else mp - 9
    year = yoe + era * 400 + (1 if month <= 2 else 0)
    return year, month, day


class TimeSync:
    """Wall-clock time (UTC) for the event stamps, from NTP.

    Keeps only an anchor ``(unix_s, ticks_ms)`` and a running age. The age is
    a total of ``ticks_diff`` steps, so it can't wrap; ``synced`` turns False
    once the anchor is ``4 * settings.NTP_RESYNC_S`` (24 h) old, long before
    ``ticks_diff`` itself would wrap (about 6 days).
    """

    def __init__(self):
        # ntptime.time() counts from the port's epoch: 1970 or 2000.
        self._epoch_offset_s = _EPOCH_2000_S if time.gmtime(0)[0] == 2000 else 0
        self._anchor_s = None
        self._anchor_ticks = 0
        self._age_ms = 0
        self._age_ticks = 0

    def sync(self):
        """Get UTC from NTP and store a new anchor. Blocking.

        Blocks for ``ntptime``'s own DNS lookup (up to several seconds), then
        up to 1 s for the UDP reply. Call it only outside time-critical
        moments: after a (re)connect, every ``settings.NTP_RESYNC_S``, and
        while not synced at most every ``settings.NTP_RETRY_S``.

        Returns:
            bool: True on success (anchor stored, age reset to 0); False on
            any error, and the old anchor stays.
        """
        try:
            port_s = ntptime.time()
        except Exception:     # OSError (timeout, no DNS), or a bad reply
            return False
        now = time.ticks_ms()
        self._anchor_s = int(port_s) + self._epoch_offset_s
        self._anchor_ticks = now
        self._age_ms = 0
        self._age_ticks = now
        return True

    @property
    def synced(self):
        """Whether the clock can stamp events.

        Returns:
            bool: True after a successful sync while ``age_s()`` is below
            ``4 * settings.NTP_RESYNC_S`` (24 h). An older anchor has drifted
            too far.
        """
        age = self.age_s()
        return age is not None and age < 4 * settings.NTP_RESYNC_S

    def age_s(self):
        """Seconds since the last successful sync.

        Each call adds the ``ticks_diff`` since the previous call to a running
        total, so the age stays right for any length of time as long as it is
        called at least every few days (the logger calls it on every pass).

        Returns:
            int | None: whole seconds, or None if never synced.
        """
        if self._anchor_s is None:
            return None
        now = time.ticks_ms()
        self._age_ms += time.ticks_diff(now, self._age_ticks)
        self._age_ticks = now
        return self._age_ms // 1000

    def unix(self, t_ms):
        """Unix time of a ``time.ticks_ms()`` value.

        ``anchor_unix + (ticks_diff(t_ms, anchor_ticks) + 500) // 1000``.
        ``ntptime`` drops the fraction of a second, so the anchor is on
        average 0.5 s early; the added 500 ms centres the error at about
        +-0.5 s (plus the network delay).

        Args:
            t_ms (int): a ``time.ticks_ms()`` value, within about 6 days of
                the anchor.

        Returns:
            int | None: Unix seconds (UTC), or None if never synced.
        """
        if self._anchor_s is None:
            return None
        return self._anchor_s + (time.ticks_diff(t_ms, self._anchor_ticks) + 500) // 1000

    @staticmethod
    def stamp(unix_s):
        """Format a Unix second as ThingSpeak's ``created_at``.

        Integer arithmetic only (no ``time.gmtime``), so the result is the
        same on CPython and on the Pico.

        Args:
            unix_s (int): Unix seconds (UTC).

        Returns:
            str: ``'YYYY-MM-DD HH:MM:SS +0000'``.
        """
        days, rest = divmod(int(unix_s), 86_400)
        year, month, day = _civil_from_days(days)
        hour, rest = divmod(rest, 3600)
        minute, second = divmod(rest, 60)
        return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d} +0000".format(
            year, month, day, hour, minute, second)
