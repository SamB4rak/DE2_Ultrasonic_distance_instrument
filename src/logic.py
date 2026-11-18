"""Pure application logic of the ultrasonic distance instrument.

Everything here is plain computation with no hardware access: echo time to
metres, clamping, choosing the nearer sensor, a median-of-3 filter, distance
zones with hysteresis, the indicator tick rate, display formatting, and the
event detector that turns the readings into a security-camera-style log
(SAMPLE, ZONE, ENTER, LEAVE, MODE).

The module never reads the clock itself: times are passed in as ``now_ms``
(a ``time.ticks_ms()`` value) and compared only with ``time.ticks_diff``.
It runs the same on CPython and MicroPython; on the Pico floats are single
precision, so results agree with the host only within that rounding.

Credits:
    New code written for this project; nothing reused.
"""

import time

import settings

ZONES = ("far", "medium", "close", "clear")
ZONE_CODES = {"clear": 0, "far": 1, "medium": 2, "close": 3}    # ThingSpeak field4
EVENT_NAMES = ("SAMPLE", "ZONE", "ENTER", "LEAVE", "MODE")    # index = event code, ThingSpeak field5

_EV_SAMPLE = 0
_EV_ZONE = 1
_EV_ENTER = 2
_EV_LEAVE = 3
_EV_MODE = 4


def echo_to_m(echo_us, c_mps):
    """Convert a round-trip echo time to a distance.

    Args:
        echo_us (int | float): echo pulse width (round trip), in microseconds.
        c_mps (float): speed of sound, in metres per second.

    Returns:
        float: the one-way distance in metres, ``c * echo_us / 2_000_000``.
    """
    return c_mps * echo_us / 2_000_000


def clamp_distance(d):
    """Limit a distance to the sensor's useful range.

    Args:
        d (float | None): distance in metres, or None for no reading.

    Returns:
        float | None: None if ``d`` is None or farther than ``D_MAX_M`` (no
        target); ``D_MIN_M`` if ``d`` is closer than that; otherwise ``d``
        (metres).
    """
    if d is None or d > settings.D_MAX_M:
        return None
    if d < settings.D_MIN_M:
        return settings.D_MIN_M
    return d


def nearest(d1, d2):
    """Pick the nearer of two distances.

    Args:
        d1 (float | None): left distance in metres, or None.
        d2 (float | None): right distance in metres, or None.

    Returns:
        float | None: the smaller of the values that are not None (metres),
        or None if both are None.
    """
    if d1 is None:
        return d2
    if d2 is None:
        return d1
    return d1 if d1 <= d2 else d2


class Median3:
    """Median-of-3 filter that tolerates one dropout (one per sensor)."""

    def __init__(self):
        """Create an empty filter."""
        self._hist = []
        self._nones = 0
        self._last = None

    def push(self, x):
        """Add one reading and return the filtered value.

        - A number resets the None counter, is stored (the last 3 are kept)
          and the median of the stored values is returned: 1 value gives that
          value, 2 values give their mean, 3 values give the middle one.
        - One None returns the previous output (one dropout is tolerated).
        - Two Nones in a row clear the history and return None.

        Args:
            x (float | None): a distance in metres, or None for a missing
                reading.

        Returns:
            float | None: the filtered distance in metres, or None.
        """
        if x is None:
            self._nones += 1
            if self._nones >= 2:
                self._hist = []
                self._last = None
            return self._last
        self._nones = 0
        h = self._hist
        h.append(x)
        if len(h) > 3:
            h.pop(0)
        n = len(h)
        if n == 1:
            out = h[0]
        elif n == 2:
            out = (h[0] + h[1]) / 2
        else:
            out = sorted(h)[1]
        self._last = out
        return out

    def reset(self):
        """Clear the history, the None counter and the last output."""
        self._hist = []
        self._nones = 0
        self._last = None


class ZoneTracker:
    """Distance zone with ``ZONE_HYST_M`` hysteresis.

    Transitions (H = ``ZONE_HYST_M``, FAR = ``ZONE_FAR_M``,
    CLOSE = ``ZONE_CLOSE_M``)::

        current  new                   when
        any      clear                 d is None
        clear    far / medium / close  d > FAR -> far; d < CLOSE -> close;
                                       else medium
        far      close                 d < CLOSE - H
        far      medium                d < FAR - H (and not close)
        medium   far                   d > FAR + H
        medium   close                 d < CLOSE - H
        close    far                   d > FAR + H
        close    medium                d > CLOSE + H (and not far)

    The farther jump is checked first. In every other case the zone stays.

    Attributes:
        zone (str): the current zone, one of ``ZONES``; starts as 'clear'.
    """

    def __init__(self):
        """Start in the 'clear' zone."""
        self.zone = "clear"

    def update(self, d):
        """Apply the transition table to a new distance.

        Args:
            d (float | None): the nearest distance in metres, or None.

        Returns:
            str: the new zone (also stored in ``.zone``).
        """
        far = settings.ZONE_FAR_M
        close = settings.ZONE_CLOSE_M
        h = settings.ZONE_HYST_M
        z = self.zone
        if d is None:
            z = "clear"
        elif z == "clear":
            if d > far:
                z = "far"
            elif d < close:
                z = "close"
            else:
                z = "medium"
        elif z == "far":
            if d < close - h:
                z = "close"
            elif d < far - h:
                z = "medium"
        elif z == "medium":
            if d > far + h:
                z = "far"
            elif d < close - h:
                z = "close"
        elif z == "close":
            if d > far + h:
                z = "far"
            elif d > close + h:
                z = "medium"
        self.zone = z
        return z

    def reset(self):
        """Go back to the 'clear' zone."""
        self.zone = "clear"


def tick_hz(d):
    """Indicator tick rate for a distance.

    Args:
        d (float | None): distance in metres, or None.

    Returns:
        float | None: None for None; ``TICK_HZ_MIN`` (Hz) at
        ``d >= TICK_D_FAR_M``; ``TICK_HZ_MAX`` at ``d <= TICK_D_NEAR_M``;
        linear in between.
    """
    if d is None:
        return None
    if d >= settings.TICK_D_FAR_M:
        return settings.TICK_HZ_MIN
    if d <= settings.TICK_D_NEAR_M:
        return settings.TICK_HZ_MAX
    span = settings.TICK_D_FAR_M - settings.TICK_D_NEAR_M
    frac = (settings.TICK_D_FAR_M - d) / span
    return settings.TICK_HZ_MIN + (settings.TICK_HZ_MAX - settings.TICK_HZ_MIN) * frac


def format_m(d):
    """Format a distance for the display.

    Args:
        d (float | None): distance in metres, or None.

    Returns:
        str: '--' for None; otherwise two decimals and ' m', e.g. '0.42 m'.
    """
    if d is None:
        return "--"
    return "{:.2f} m".format(d)


def bar_fraction(d):
    """Fill fraction of the display's distance bar.

    Args:
        d (float | None): distance in metres, or None.

    Returns:
        float: 0.0 for None; otherwise ``min(d, BAR_FULL_SCALE_M) /
        BAR_FULL_SCALE_M``, from 0.0 to 1.0.
    """
    if d is None:
        return 0.0
    full = settings.BAR_FULL_SCALE_M
    return min(d, full) / full


class EventDetector:
    """Turns the readings into log events, like a security-camera log.

    Call ``update`` once per ranging slot, also while OFF. Events:

    - MODE: the mode differs from the previous call (never on the first call).
    - ZONE (RUN only): the zone differs from the previous call (never on the
      first call).
    - ENTER (RUN only, no visit open): d < ``ALERT_M`` for ``ALERT_CONFIRM``
      calls in a row; stamped with the first of those calls. Opens a visit.
    - LEAVE (RUN only, visit open): d is None or >= ``ALERT_M`` continuously
      for ``LEAVE_HOLD_MS``; stamped with the first call outside, with the
      closest distance of the visit and its duration in whole seconds. If d
      comes back inside sooner, the visit continues.
    - SAMPLE (RUN only): ``SAMPLE_PERIOD_S`` after the previous SAMPLE; the
      first one ``SAMPLE_PERIOD_S`` after RUN starts.

    Switching RUN -> OFF during a visit gives a LEAVE and then the MODE. The
    LEAVE is stamped when the object left if it was already outside,
    otherwise now.

    Each event is a tuple ``(t_ms, kind, mode, d, d1, d2, zone, closest,
    duration_s)``: ``kind`` indexes ``EVENT_NAMES``; ``mode``, ``d``, ``d1``,
    ``d2`` and ``zone`` are those of the call at ``t_ms`` (for MODE, the new
    mode); ``closest`` (metres) and ``duration_s`` (int) are set only for
    LEAVE and are None otherwise.
    """

    def __init__(self):
        """Create a detector with no history."""
        self._first = True
        self._mode = None
        self._zone = None
        self._sample_ref = 0
        self._count = 0
        self._cand = None       # (t, mode, d, d1, d2, zone) of the first call inside
        self._cand_min = None
        self._visit = False
        self._t_enter = 0
        self._closest = None
        self._out = None        # (t, mode, d, d1, d2, zone) of the first call outside

    def update(self, now_ms, mode, d, d1, d2, zone):
        """Process one ranging slot and return the new events.

        Args:
            now_ms (int): the current ``time.ticks_ms()`` value (ms).
            mode (str): 'RUN' or 'OFF'.
            d (float | None): nearest distance in metres, or None.
            d1 (float | None): left distance in metres, or None.
            d2 (float | None): right distance in metres, or None.
            zone (str): one of ``ZONES`` ('clear' while OFF).

        Returns:
            list: the new event tuples (often empty), sorted by time; ties in
            the order of ``EVENT_NAMES``.
        """
        events = []
        snap = (now_ms, mode, d, d1, d2, zone)
        if self._first:
            self._first = False
            if mode == "RUN":
                self._sample_ref = now_ms
        elif mode != self._mode:
            if mode != "RUN" and self._visit:
                self._emit_leave(events, self._out if self._out else snap)
            self._count = 0
            self._cand = None
            events.append(self._event(snap, _EV_MODE))
            if mode == "RUN":
                self._sample_ref = now_ms
        if mode == "RUN":
            self._run_events(events, snap, zone)
        self._mode = mode
        self._zone = zone
        if len(events) > 1:
            events.sort(key=lambda e: (time.ticks_diff(e[0], now_ms), e[1]))
        return events

    def _run_events(self, events, snap, zone):
        """Add the ZONE, SAMPLE, ENTER and LEAVE events of one RUN call.

        Args:
            events (list): the list to append event tuples to.
            snap (tuple): (t_ms, mode, d, d1, d2, zone) of this call.
            zone (str): this call's zone.
        """
        now_ms = snap[0]
        d = snap[2]
        if self._zone is not None and zone != self._zone:
            events.append(self._event(snap, _EV_ZONE))
        if time.ticks_diff(now_ms, self._sample_ref) >= settings.SAMPLE_PERIOD_S * 1000:
            self._sample_ref = now_ms
            events.append(self._event(snap, _EV_SAMPLE))
        inside = d is not None and d < settings.ALERT_M
        if not self._visit:
            if not inside:
                self._count = 0
                self._cand = None
                return
            if self._count == 0:
                self._cand = snap
                self._cand_min = d
            elif d < self._cand_min:
                self._cand_min = d
            self._count += 1
            if self._count >= settings.ALERT_CONFIRM:
                events.append(self._event(self._cand, _EV_ENTER))
                self._visit = True
                self._t_enter = self._cand[0]
                self._closest = self._cand_min
                self._out = None
                self._count = 0
                self._cand = None
        elif inside:
            self._out = None
            if d < self._closest:
                self._closest = d
        else:
            if self._out is None:
                self._out = snap
            if time.ticks_diff(now_ms, self._out[0]) >= settings.LEAVE_HOLD_MS:
                self._emit_leave(events, self._out)

    def _emit_leave(self, events, snap):
        """Append a LEAVE stamped with ``snap`` and close the visit.

        Args:
            events (list): the list to append the event tuple to.
            snap (tuple): (t_ms, mode, d, d1, d2, zone) of the LEAVE's call.
        """
        duration_s = time.ticks_diff(snap[0], self._t_enter) // 1000
        events.append(snap[:1] + (_EV_LEAVE,) + snap[1:] + (self._closest, duration_s))
        self._visit = False
        self._out = None
        self._closest = None

    @staticmethod
    def _event(snap, kind):
        """Build a non-LEAVE event tuple.

        Args:
            snap (tuple): (t_ms, mode, d, d1, d2, zone) of the event's call.
            kind (int): index into ``EVENT_NAMES``.

        Returns:
            tuple: (t_ms, kind, mode, d, d1, d2, zone, None, None).
        """
        return snap[:1] + (kind,) + snap[1:] + (None, None)
