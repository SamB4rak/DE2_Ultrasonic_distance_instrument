"""Bench test of the whole instrument (run on the Pico).

Wiring: the complete instrument, as in ``docs/img/ultrasonic_wiring_diagram.svg``:

    * both HC-SR04 sensors on the 5 V rail (VBUS, Pico pin 40, from the
      centre header; never a red 3V3 pin of a Gravity row), with a 100 nF
      capacitor across each one's VCC and GND; Trig #1 to GP20 and Trig #2 to
      GP18, each Trig line also 10 kOhm to GND; each Echo through its own
      divider (1 kOhm in series to the GPIO node, 2 kOhm from the node to
      GND) to GP21 (#1, left) and GP19 (#2, right), each divider checked with
      the meter before its node touched a GPIO: the node reads 0.64-0.69 of
      the measured 5 V rail (4.75-5.25 V);
    * the relay module with single jumpers (never a 3-way cable): VCC to the
      5 V rail, GND to GND, the VCC-JD_VCC jumper fitted and the H/L jumper on
      L; pre-checked with IN on 3V3 (dark, silent) and on GND (click) before
      IN went to GP16, with 4.7 kOhm from IN to 3V3; 100 nF plus a 10-47 uF
      electrolytic (rated >= 10 V, stripe/minus leg on GND) across its VCC
      and GND; its contacts stay unwired;
    * the OLED on 3V3 and GND with single jumpers by its own pin labels (the
      VCC/GND order differs between modules; never plug a 4-way connector
      straight onto the I2C0 header), SDA to GP4 and SCL to GP5, plus
      4.7 kOhm from SDA and from SCL to 3V3;
    * the RGB module's R, G, B to GP12, GP13, GP14 through its series
      resistors, minus to GND;
    * the button, by two diagonally opposite legs, between the GP17 row's D
      pin and its GND pin;
    * all grounds common (the breadboard GND rail beeps to a Gravity GND
      pin), USB power only (a computer's port or a charger of at least 1 A,
      not an unpowered hub).

Wire with USB unplugged, check the 5 V rail with a continuity beeper (no
steady beep to GND, 3V3 or any GPIO row), and run nothing until the tester
confirms the setup with "wired OK".

For the cloud log, ``config.py`` on the Pico (copied by
``python tools/deploy.py``) holds the hotspot and the ThingSpeak channel. This
script imports it but never prints any of its values. Without it the
instrument runs without the upload.

Run:
    mpremote connect auto run tests/bench/bench_system.py
    mpremote connect auto exec "DURATION_S=120" + run tests/bench/bench_system.py

It runs the real application tasks for ``DURATION_S`` seconds (default 300),
plus a watchdog task that wakes every 10 ms and records how late it is: that
is the event loop's lateness, including the uploads. While it runs, the
tester walks in and out of range, presses the button, and pulls only the
wires the test asks for (a sensor's Echo jumper at the sensor, the OLED's SDA
jumper), never a supply or ground wire. Park the free end of a pulled jumper
in an empty breadboard row, away from the 5 V rail and any VCC pin. The
measured lateness includes this script's own printing.

Output: one CSV header ``t_s,row,what,v1,v2,v3``, then rows, then a
``# summary:`` line. Rows:
    event   name, UTC time (empty before the clock is set), d in m, and for
            LEAVE the closest distance and the duration in s
    zone    new zone, ms from the zone change to the LED colour change, and
            ms from the first raw ping in the new zone (the hand appearing)
            to that colour
    upload  result (True/False/None), queue length, s since the last request
    stats   every 10 s: max lateness in ms, pings left/right, display flushes
    status  every 10 s: queue length, Wi-Fi state, upload state, faults
The summary gives the maximum and 99th-percentile lateness, the pings per
second of each sensor, the display updates per second, the mean upload
interval, the worst zone-to-LED delay and the worst response time (an upper
bound: it counts from the first raw ping in the new zone, even a stray one).
If an application task fails, the run stops at the next 10 s step; its error
may be printed twice.
"""

import asyncio
import time
from array import array

from machine import I2C, Pin

import board
import logic
import main
import settings
from button import Button
from display import Display
from hcsr04 import HCSR04
from relay import Relay
from rgb_led import RGBLed
from state import EventQueue, State
from timesync import TimeSync

DURATION_S = int(globals().get("DURATION_S", 300))
STATS_S = 10
WATCH_MS = 10

_late_hist = array("I", [0] * 101)      # 1 ms bins; the last one is >= 100 ms
_window_late = [0]
_t0 = time.ticks_ms()


def t_s():
    return "{:.2f}".format(time.ticks_diff(time.ticks_ms(), _t0) / 1000)


def row(kind, what="", v1="", v2="", v3=""):
    print("{},{},{},{},{},{}".format(t_s(), kind, what, v1, v2, v3))


def fmt(d):
    return "" if d is None else "{:.2f}".format(d)


def raw_zone(d):
    """Zone of one raw reading, without the filter or the hysteresis."""
    if d is None:
        return "clear"
    if d > settings.ZONE_FAR_M:
        return "far"
    if d < settings.ZONE_CLOSE_M:
        return "close"
    return "medium"


class CountingSensor:
    """An HCSR04 that counts its pings and notes raw zone changes."""

    raw = {}          # zone -> ticks_ms of the first raw ping in it (both sensors)

    def __init__(self, sensor):
        self.sensor = sensor
        self.pings = 0
        self.zone = "clear"

    async def ping(self, wait_ms=None):
        self.pings += 1
        t = time.ticks_ms()
        status, echo_us = await self.sensor.ping(wait_ms)
        if status != HCSR04.BUSY:
            d = None
            if status == HCSR04.OK:
                d = logic.clamp_distance(logic.echo_to_m(echo_us, settings.C_DEFAULT_MPS))
            zone = raw_zone(d)
            if zone != self.zone:
                self.zone = zone
                if zone not in CountingSensor.raw:
                    CountingSensor.raw[zone] = t
        return status, echo_us

    @property
    def faulty(self):
        return self.sensor.faulty


class CountingDisplay:
    """A Display that counts its flushes."""

    def __init__(self, display):
        self.display = display
        self.flushes = 0

    @property
    def ok(self):
        return self.display.ok

    def render(self, view):
        self.display.render(view)

    async def flush(self):
        await self.display.flush()
        self.flushes += 1


class ZoneWatch:
    """Times each zone change until the LED shows the new zone's colour."""

    COLORS = {"far": settings.COLOR_GREEN, "medium": settings.COLOR_YELLOW,
              "close": settings.COLOR_RED, "clear": settings.COLOR_GREEN}

    def __init__(self, state):
        self.state = state
        self.zone = state.zone
        self.since = None
        self.worst_ms = 0
        self.worst_response_ms = 0

    def check(self, rgb):
        st = self.state
        zone = st.zone
        if st.mode != "RUN" or st.faults:
            # No zone colour is shown now: this time is not a delay.
            self.zone = zone
            self.since = None
            CountingSensor.raw.clear()
            return
        if zone != self.zone:
            self.zone = zone
            self.since = time.ticks_ms()
        if self.since is not None and tuple(rgb) == self.COLORS[zone]:
            now = time.ticks_ms()
            ms = time.ticks_diff(now, self.since)
            self.worst_ms = max(self.worst_ms, ms)
            first = CountingSensor.raw.get(zone)
            resp = ""
            if first is not None:
                resp = time.ticks_diff(now, first)
                self.worst_response_ms = max(self.worst_response_ms, resp)
            row("zone", zone, ms, resp)
            self.since = None
            CountingSensor.raw.clear()


class WatchedLed:
    """An RGBLed that reports each colour to a ZoneWatch."""

    def __init__(self, led, watch):
        self.led = led
        self.watch = watch

    def color(self, rgb):
        self.led.color(rgb)
        self.watch.check(rgb)

    def off(self):
        self.led.off()

    @property
    def current(self):
        return self.led.current


class LoggedQueue(EventQueue):
    """The event queue, printing each event as it is added."""

    clock = None

    def add(self, ev):
        EventQueue.add(self, ev)
        stamp = ""
        if self.clock is not None and self.clock.synced:
            stamp = TimeSync.stamp(self.clock.unix(ev[0]))
        extra = ""
        if ev[1] == 3:                       # LEAVE
            extra = "{} m {} s".format(fmt(ev[7]), ev[8])
        row("event", logic.EVENT_NAMES[ev[1]], stamp, fmt(ev[3]), extra)


class LoggedThingSpeak:
    """A ThingSpeak client that prints each request's result."""

    def __init__(self, ts, state):
        self.ts = ts
        self.state = state
        self.last = None
        self.intervals = []

    @property
    def resolved(self):
        return self.ts.resolved

    def resolve(self):
        return self.ts.resolve()

    async def bulk_post(self, updates):
        now = time.ticks_ms()
        gap = ""
        if self.last is not None:
            gap_s = time.ticks_diff(now, self.last) / 1000
            self.intervals.append(gap_s)
            gap = "{:.1f}".format(gap_s)
        self.last = now
        result = await self.ts.bulk_post(updates)
        row("upload", result, len(self.state.events), gap)
        return result


async def watchdog():
    """Wake every 10 ms and record how late each wake-up is."""
    due = time.ticks_add(time.ticks_ms(), WATCH_MS)
    while True:
        await asyncio.sleep_ms(max(0, time.ticks_diff(due, time.ticks_ms())))
        late = time.ticks_diff(time.ticks_ms(), due)
        if late < 0:
            late = 0
        _late_hist[min(late, 100)] += 1
        if late > _window_late[0]:
            _window_late[0] = late
        due = time.ticks_add(time.ticks_ms(), WATCH_MS)


def percentile(p):
    total = sum(_late_hist)
    need = total * p / 100
    seen = 0
    for ms in range(len(_late_hist)):
        seen += _late_hist[ms]
        if seen >= need:
            return ms
    return 100


def max_late():
    for ms in range(len(_late_hist) - 1, -1, -1):
        if _late_hist[ms]:
            return ms
    return 0


def network():
    try:
        import config
    except ImportError:
        print("# no config.py: running without the cloud log")
        return None, None, None
    from thingspeak import ThingSpeak
    from wifi import WiFi
    wifi = WiFi(config.WIFI_SSID, config.WIFI_PASSWORD)
    ts = ThingSpeak(config.THINGSPEAK_WRITE_KEY, config.THINGSPEAK_CHANNEL_ID)
    return wifi, ts, TimeSync()


async def bench(state, parts, s1, s2, disp, lts):
    app = asyncio.create_task(main.main(state, *parts))
    asyncio.create_task(watchdog())
    for _ in range(DURATION_S // STATS_S):
        if app.done():
            print("# an application task stopped")
            await app          # re-raises its error, which ends the run
        p1, p2, fl = s1.pings, s2.pings, disp.flushes
        _window_late[0] = 0
        await asyncio.sleep_ms(STATS_S * 1000)
        row("stats", _window_late[0], s1.pings - p1, s2.pings - p2, disp.flushes - fl)
        row("status", len(state.events), state.wifi, state.cloud,
            " ".join(sorted(state.faults)))


def run():
    if Button.held_at_boot(board.PIN_BUTTON):
        print("# button held: bench not started")
        return
    relay = Relay(board.PIN_RELAY)
    led = RGBLed(board.PIN_LED_R, board.PIN_LED_G, board.PIN_LED_B)
    try:
        i2c = I2C(board.I2C_ID, sda=Pin(board.PIN_SDA), scl=Pin(board.PIN_SCL),
                  freq=settings.I2C_FREQ_HZ)
        disp = CountingDisplay(Display(i2c))
        s1 = CountingSensor(HCSR04(board.PIN_TRIG_1, board.PIN_ECHO_1, "L"))
        s2 = CountingSensor(HCSR04(board.PIN_TRIG_2, board.PIN_ECHO_2, "R"))
        state = State()
        state.events = LoggedQueue(settings.EVENT_QUEUE_LEN)
        watch = ZoneWatch(state)
        wifi, ts, clock = network()
        LoggedQueue.clock = clock
        lts = LoggedThingSpeak(ts, state) if ts is not None else None
        parts = (relay, WatchedLed(led, watch), disp, s1, s2,
                 Button(board.PIN_BUTTON), logic.EventDetector(), wifi, lts, clock)
        print("t_s,row,what,v1,v2,v3")
        asyncio.run(bench(state, parts, s1, s2, disp, lts))
        secs = DURATION_S // STATS_S * STATS_S
        mean_up = ""
        if lts is not None and lts.intervals:
            mean_up = "{:.1f}".format(sum(lts.intervals) / len(lts.intervals))
        print("# summary: duration_s={} late_max_ms={} late_p99_ms={} pings_per_s_L={:.2f} "
              "pings_per_s_R={:.2f} display_per_s={:.2f} upload_interval_s={} "
              "zone_to_led_max_ms={} response_max_ms={} events_dropped={}".format(
                  secs, max_late(), percentile(99), s1.pings / secs, s2.pings / secs,
                  disp.flushes / secs, mean_up, watch.worst_ms,
                  watch.worst_response_ms, state.events.dropped))
    finally:
        relay.off()
        led.off()


run()
