"""The five asyncio tasks of the instrument.

* ``mode_task``: polls the button and toggles RUN/OFF.
* ``ranging_task``: pings the two sensors in turn, one 60 ms slot each,
  filters the distances, picks the nearer one, tracks the zone and feeds the
  event detector after every slot.
* ``indicator_task``: the LED colour and the flash rate, plus one relay click
  per flash (never more than 5 per second).
* ``display_task``: draws the screen and sends it page by page.
* ``logger_task``: Wi-Fi, the NTP clock and the batched upload of the logged
  events to ThingSpeak.

All tasks share one ``state.State`` object; each attribute has one writer.
Every period and delay is a whole number of milliseconds, because the Pico's
``sleep_ms`` and ``ticks_add`` refuse floats. No task blocks the loop for more
than a few milliseconds, except the logger's DNS lookup (``ts.resolve()``) and
NTP sync (``clock.sync()``), which MicroPython can only do blocking; they run
rarely (after a (re)connect, every 6 h, or after failed uploads), and the
logger waits three ranging slots after each one so that the events that fell
due meanwhile reach the queue before a batch is built.

New code for this project.
"""

import asyncio
import time

import logic
import settings
import thingspeak
from hcsr04 import HCSR04

_PASS_MS = 1000          # the logger checks its timers this often
_FAULT_CODES = ("S1", "S2", "OLED")


# --- helpers ---------------------------------------------------------------

def _next_slot(t, period):
    """The next slot time on a fixed grid, skipping slots already missed.

    Args:
        t (int): the current slot's ``ticks_ms`` time.
        period (int): the slot length in ms.

    Returns:
        int: ``t + period``, or, after a stall, the first later grid time that
        is still in the future (no burst of catch-up slots).
    """
    t = time.ticks_add(t, period)
    late = time.ticks_diff(time.ticks_ms(), t)
    if late > 0:
        t = time.ticks_add(t, (late // period + 1) * period)
    return t


async def _sleep_until(t):
    """Wait without blocking until ``ticks_ms()`` reaches ``t``."""
    await asyncio.sleep_ms(max(0, time.ticks_diff(t, time.ticks_ms())))


async def _yield_slots():
    """Let the ranging task run three slots (after a blocking call)."""
    await asyncio.sleep_ms(3 * settings.RANGING_SLOT_MS)


def _set_fault(faults, code, on):
    """Add ``code`` to the fault set if ``on``, else remove it."""
    if on:
        faults.add(code)
    else:
        faults.discard(code)


def _period_ms(d):
    """Flash period in whole ms for a distance d in m (not None)."""
    return int(1000 / logic.tick_hz(d))


# --- mode ------------------------------------------------------------------

async def mode_task(state, button):
    """Toggle ``state.mode`` between RUN and OFF on each button press.

    Polls the button every ``settings.BUTTON_POLL_MS``. Writes only
    ``state.mode``.

    Args:
        state (State): the shared state.
        button (Button): the RUN/OFF button.
    """
    t = time.ticks_ms()
    while True:
        if button.poll():
            state.mode = "OFF" if state.mode == "RUN" else "RUN"
        t = _next_slot(t, settings.BUTTON_POLL_MS)
        await _sleep_until(t)


# --- ranging and events ----------------------------------------------------

async def _measure(sensor, filt, previous):
    """Ping one sensor and return its new filtered distance.

    Args:
        sensor (HCSR04): the sensor to ping.
        filt (logic.Median3): that sensor's filter.
        previous (float | None): its last distance in m.

    Returns:
        float | None: the filtered distance in m. OK readings go through
        ``echo_to_m``, ``clamp_distance`` and the filter; NO_TARGET and
        NO_RISE push None; BUSY (not pinged) keeps ``previous``.
    """
    status, echo_us = await sensor.ping()
    if status == HCSR04.OK:
        d = logic.clamp_distance(logic.echo_to_m(echo_us, settings.C_DEFAULT_MPS))
        return filt.push(d)
    if status == HCSR04.BUSY:
        return previous
    return filt.push(None)


def _ranging_off(state, filters, tracker):
    """The OFF transition: forget the readings, the zone and S1/S2."""
    for f in filters:
        f.reset()
    tracker.reset()
    state.d1 = None
    state.d2 = None
    state.d = None
    state.zone = "clear"
    state.faults.discard("S1")
    state.faults.discard("S2")


def _log_events(state, detector, mode):
    """Run the event detector once and queue what it returns."""
    events = detector.update(time.ticks_ms(), mode, state.d, state.d1,
                             state.d2, state.zone)
    for ev in events:
        state.events.add(ev)
        state.last_event = logic.EVENT_NAMES[ev[1]]


async def ranging_task(state, s1, s2, detector):
    """Ping the sensors in turn and keep the distances, zone and events.

    One slot every ``settings.RANGING_SLOT_MS``, alternating s1 and s2, so
    each sensor is read every two slots. Each slot reads ``state.mode`` once
    and uses that value for the whole slot (the button may toggle it while a
    ping is waiting). In RUN it pings, filters, updates S1/S2 from
    ``faulty``, the nearer distance and the zone. On the first OFF slot it
    clears the readings, the zone and S1/S2; while OFF it doesn't ping. In
    every slot, RUN or OFF, it calls the event detector and queues its events.

    Writes ``d1``, ``d2``, ``d``, ``zone``, ``last_event``, the S1/S2 faults,
    and adds to ``events``.

    Args:
        state (State): the shared state.
        s1 (HCSR04): the left sensor (fault code S1).
        s2 (HCSR04): the right sensor (fault code S2).
        detector (logic.EventDetector): the event detector.
    """
    sensors = (s1, s2)
    filters = (logic.Median3(), logic.Median3())
    codes = ("S1", "S2")
    tracker = logic.ZoneTracker()
    i = 0
    was_off = False
    slot = time.ticks_ms()
    while True:
        mode = state.mode
        if mode == "OFF":
            if not was_off:
                _ranging_off(state, filters, tracker)
                was_off = True
        else:
            was_off = False
            previous = state.d1 if i == 0 else state.d2
            value = await _measure(sensors[i], filters[i], previous)
            if i == 0:
                state.d1 = value
            else:
                state.d2 = value
            _set_fault(state.faults, codes[i], sensors[i].faulty)
            state.d = logic.nearest(state.d1, state.d2)
            state.zone = tracker.update(state.d)
            i = 1 - i
        _log_events(state, detector, mode)
        slot = _next_slot(slot, settings.RANGING_SLOT_MS)
        await _sleep_until(slot)


# --- indicator -------------------------------------------------------------

def _steady_color(state):
    """The colour of a state without ticks, or None while ticking."""
    if state.mode == "OFF":
        return settings.COLOR_BLUE
    for code in _FAULT_CODES:
        if code in state.faults:
            return settings.COLOR_MAGENTA
    if state.zone == "clear":
        return settings.COLOR_GREEN
    return None


def _zone_color(zone):
    """The flash colour of a zone ('far', 'medium' or 'close')."""
    if zone == "close":
        return settings.COLOR_RED
    if zone == "medium":
        return settings.COLOR_YELLOW
    return settings.COLOR_GREEN


async def indicator_task(state, led, relay):
    """Show the zone on the LED and click the relay once per flash.

    Every ``settings.INDICATOR_STEP_MS``, the first match wins:

    1. mode OFF: blue, relay off;
    2. any fault (S1, S2, OLED): magenta, relay off;
    3. zone clear: steady green, relay off;
    4. otherwise ticks at ``logic.tick_hz(d)``: each tick sets the zone
       colour and starts one ``relay.pulse()`` task; the LED goes off half a
       period later and the next tick starts a period after this one. A zone
       change starts a tick at once, but two pulses always start at least
       ``1000 / TICK_HZ_MAX`` ms (200 ms) apart, the relay's rating. A new d
       in the same zone changes the period from the next tick.

    Tick times fall on the 50 ms steps, so a period between them is rounded
    up to the next step.

    Args:
        state (State): the shared state (read only).
        led (RGBLed): the RGB LED.
        relay (Relay): the relay.
    """
    min_gap = int(1000 / settings.TICK_HZ_MAX)
    shown = None          # zone of the running tick; None: no tick running
    start = 0             # ticks_ms of the current tick's start
    period = 0
    lit = False
    last_pulse = None     # start of the last pulse, until min_gap has passed
    pulse = None          # the running pulse task, kept referenced until the next one
    step = time.ticks_ms()
    while True:
        now = time.ticks_ms()
        if last_pulse is not None and time.ticks_diff(now, last_pulse) >= min_gap:
            last_pulse = None
        steady = _steady_color(state)
        if steady is not None:
            led.color(steady)
            relay.off()
            shown = None
            lit = False
        else:
            due = shown != state.zone or time.ticks_diff(now, start) >= period
            if due and last_pulse is None:
                shown = state.zone
                period = _period_ms(state.d)
                led.color(_zone_color(shown))
                pulse = asyncio.create_task(relay.pulse())
                start = now
                last_pulse = now
                lit = True
            elif lit and time.ticks_diff(now, start) >= period // 2:
                led.off()
                lit = False
        step = _next_slot(step, settings.INDICATOR_STEP_MS)
        await _sleep_until(step)


# --- display ---------------------------------------------------------------

async def display_task(state, display):
    """Draw and send the screen every ``settings.DISPLAY_PERIOD_MS``.

    Adds the OLED fault while ``display.ok`` is False and removes it when the
    display answers again.

    Args:
        state (State): the shared state.
        display (Display): the OLED.
    """
    t = time.ticks_ms()
    while True:
        display.render(state)
        await display.flush()
        _set_fault(state.faults, "OLED", not display.ok)
        t = _next_slot(t, settings.DISPLAY_PERIOD_MS)
        await _sleep_until(t)


# --- logger ----------------------------------------------------------------

def _waited(now, t, wait_ms):
    """True if a stored time ``t`` (not None) is at least ``wait_ms`` old."""
    return t is not None and time.ticks_diff(now, t) >= wait_ms


class _Logger:
    """The logger task's state and one pass of its work.

    A batch, once built, is kept as ``(updates, n, last_s, d0)`` and re-sent
    unchanged until ThingSpeak accepts it. Every stored time is forgotten
    (set to None) as soon as its wait has passed, so none is compared after
    ``ticks_diff`` wraps (about 6.2 days).
    """

    def __init__(self, state, wifi, ts, clock):
        self.state = state
        self.wifi = wifi
        self.ts = ts
        self.clock = clock
        self.up = False
        self.pending = None       # (updates, n, last_s, d0) or None
        self.accepted_s = None    # newest second ThingSpeak accepted
        self.fails = 0            # None results in a row
        self.wifi_failed = None   # time of the last failed (re)connect
        self.sync_failed = None   # time of the last failed NTP sync
        self.last_try = None      # time of the last upload attempt
        self.last_req = None      # time of the last request sent

    def _forget(self, now):
        """Forget every stored time whose wait has passed."""
        if _waited(now, self.wifi_failed, settings.WIFI_RETRY_S * 1000):
            self.wifi_failed = None
        if _waited(now, self.sync_failed, settings.NTP_RETRY_S * 1000):
            self.sync_failed = None
        if _waited(now, self.last_try, settings.UPLOAD_PERIOD_S * 1000):
            self.last_try = None
        if _waited(now, self.last_req, settings.UPLOAD_MIN_GAP_S * 1000):
            self.last_req = None

    async def step(self):
        """One pass: the timers, then connect, re-sync or upload as due."""
        st = self.state
        self.clock.age_s()                # keeps the running age
        st.synced = self.clock.synced
        now = time.ticks_ms()
        st.events.expire(now)
        self._forget(now)
        if not self.up:
            if self.wifi_failed is None:
                await self._connect()
            return
        if self._sync_due():
            await self._sync()
        if self.last_try is None and self.last_req is None:
            await self._upload()

    def _down(self):
        """Mark the link down and start the Wi-Fi retry wait."""
        self.up = False
        self.state.wifi = "down"
        self.wifi_failed = time.ticks_ms()

    async def _connect(self):
        """Connect, resolve the host if needed, then sync the clock."""
        self.state.wifi = "connecting"
        if not await self.wifi.connect():
            self._down()
            return
        if not self.ts.resolved:
            ok = self.ts.resolve()        # blocking DNS
            await _yield_slots()
            if not ok:
                self.wifi.disconnect()
                self._down()
                return
        await self._sync()                # always right after a (re)connect
        self.up = True
        self.state.wifi = "up"

    def _sync_due(self):
        """True if the clock needs a sync and no retry wait is running."""
        if self.sync_failed is not None:
            return False
        if not self.clock.synced:
            return True
        return self.clock.age_s() >= settings.NTP_RESYNC_S

    async def _sync(self):
        """Sync the clock from NTP (blocking), then let ranging catch up."""
        ok = self.clock.sync()
        await _yield_slots()
        self.sync_failed = None if ok else time.ticks_ms()
        self.state.synced = self.clock.synced

    def _build(self):
        """A new batch from the queue, or None if there is nothing to send."""
        q = self.state.events
        if not self.clock.synced or len(q) == 0:
            return None
        d0 = q.dropped
        updates, n, last = thingspeak.make_updates(
            q.peek(), self.clock, time.ticks_ms(), after_s=self.accepted_s)
        if n <= 0:
            return None
        return (updates, n, last, d0)

    async def _upload(self):
        """Send the pending batch (or a new one) and handle the result."""
        st = self.state
        self.last_try = time.ticks_ms()
        if self.pending is None:
            self.pending = self._build()
            if self.pending is None:
                return
        updates, n, last, d0 = self.pending
        self.last_req = time.ticks_ms()
        result = await self.ts.bulk_post(updates)
        if result is True:
            # Events dropped meanwhile (overflow, expiry) were the batch's oldest.
            st.events.drop(max(0, n - (st.events.dropped - d0)))
            self.accepted_s = last
            self.pending = None
            st.cloud = "ok"
            self.fails = 0
        elif result is False:
            st.cloud = "rej"
            self.fails = 0
        else:
            st.cloud = "--"
            self.fails += 1
            if not self.wifi.is_connected():
                self.up = False
                st.wifi = "down"
            elif self.fails >= settings.HTTP_FAILS_BEFORE_RESOLVE:
                self.ts.resolve()         # blocking DNS
                self.fails = 0
                await _yield_slots()


async def logger_task(state, wifi, ts, clock):
    """Keep Wi-Fi and the clock up and upload the logged events in batches.

    Started only when the Wi-Fi settings exist. On every pass (about once a
    second), connected or not, it updates the clock's age and
    ``state.synced`` and expires events older than 4 days.

    1. Not connected: ``wifi`` 'connecting', ``await wifi.connect()``. On a
       failure ``wifi`` is 'down' and the next try waits ``WIFI_RETRY_S``.
       On success it resolves the host if no IP is cached (if that fails it
       disconnects and waits ``WIFI_RETRY_S``), syncs the clock and sets
       ``wifi`` 'up'.
    2. Connected: re-syncs the clock when it isn't synced or is
       ``NTP_RESYNC_S`` old (at least ``NTP_RETRY_S`` after a failed try).
       Every ``UPLOAD_PERIOD_S``, and at least ``UPLOAD_MIN_GAP_S`` after the
       previous request, it sends the pending batch, or builds a new one with
       ``thingspeak.make_updates()`` when the clock is synced and events are
       queued. True drops the uploaded events (``cloud`` 'ok'); False keeps
       the batch (``cloud`` 'rej'); None keeps it too (``cloud`` '--'), then
       reconnects if the link is down, or resolves the host again after
       ``HTTP_FAILS_BEFORE_RESOLVE`` failures in a row.

    Writes ``wifi``, ``synced`` and ``cloud``; uses ``events`` only through
    ``peek()``, ``drop()`` and ``expire()``.

    Args:
        state (State): the shared state.
        wifi (WiFi): the station interface.
        ts (ThingSpeak): the upload client.
        clock (TimeSync): the NTP clock.
    """
    lg = _Logger(state, wifi, ts, clock)
    while True:
        await lg.step()
        await asyncio.sleep_ms(_PASS_MS)
