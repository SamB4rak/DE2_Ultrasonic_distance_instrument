"""Bench test for Wi-Fi, the NTP clock and the ThingSpeak upload (run on the Pico).

Wiring: none beyond the board itself. Power it over USB only, from a
computer's port or a charger of at least 1 A (not an unpowered hub): Wi-Fi
bursts draw up to about 0.4 A. All grounds stay common. Run nothing until the
tester confirms the setup with "wired OK".

Before the first run:
    * ``config.py`` on the Pico (copied by ``python tools/deploy.py``) holds a
      2.4 GHz hotspot, the write key and the id of a private ThingSpeak
      channel used only for this project. This script imports it but never
      prints any of its values.
    * The channel has fields 1-7: Distance, Left, Right, Zone, Event,
      Closest, Visit.

Run one mode at a time (global ``MODE``, default ``"clock"``):
    mpremote connect auto run tests/bench/bench_network.py
    mpremote connect auto exec "MODE='upload'" + run tests/bench/bench_network.py

Modes:
    clock:          time ``WiFi()`` (the radio start, outside the event loop),
                    then connect, resolve and NTP sync, and print the UTC
                    time five times, 2 s apart. The tester compares it with a
                    phone clock: within 1 s.
    upload:         after connect/resolve/sync, 5 synthetic events in 4
                    different seconds (two share a second) become 4 updates,
                    sent in one bulk request. 16 s later the same request is
                    sent again. Both must return True; the tester checks on
                    ThingSpeak: 4 entries with the right times, fields and
                    status, and no extra entries after the re-send.
    fail_password:  a deliberately wrong password (a literal in this script)
                    with the real network name: connect must give False
                    quickly.
    fail_key:       a deliberately wrong write key (a literal in this
                    script): ThingSpeak answers 4xx and bulk_post gives False.
    hotspot_off:    one accepted upload, then the tester switches the hotspot
                    off when told: bulk_post gives None; then the hotspot on
                    again: the script reconnects and uploads again.

Every step also reports the worst stall of the event loop: a side task waits
``sleep_ms(10)`` in a loop and records how late it wakes up. A blocking call
(DNS in ``resolve()``, NTP in ``sync()``) shows up as a large stall.

Output: one CSV header ``mode,step,result,status,resp_bytes,ms,stall_ms``,
the rows, then a ``# summary:`` line. ``status`` is the HTTP status code,
``resp_bytes`` the response size, ``ms`` how long the step took.
"""

import asyncio
import time

import config
import settings
from thingspeak import ThingSpeak, make_updates
from timesync import TimeSync
from wifi import WiFi

MODE = globals().get("MODE", "clock")

WRONG_PASSWORD = "wrong-password-for-bench"   # deliberately wrong, not a real one
WRONG_KEY = "not-a-real-write-key"            # deliberately wrong, not a real one

_rows = []


class StallMeter:
    """Records how late a 10 ms ticker task wakes up (the loop's stall)."""

    def __init__(self):
        self.worst_ms = 0

    async def run(self):
        last = time.ticks_ms()
        while True:
            await asyncio.sleep_ms(10)
            now = time.ticks_ms()
            late = time.ticks_diff(now, last) - 10
            if late > self.worst_ms:
                self.worst_ms = late
            last = now

    def take(self):
        """Return the worst lateness since the last take() and reset it."""
        worst = self.worst_ms
        self.worst_ms = 0
        return worst


def row(step, result, status="", resp_bytes="", ms="", stall_ms=""):
    """Print one CSV row and keep it for the summary."""
    _rows.append((step, result))
    print("{},{},{},{},{},{},{}".format(MODE, step, result, status, resp_bytes, ms, stall_ms))


async def measure(meter, step, call, ts=None):
    """Run one step (a coroutine or a plain function) and print its row.

    Args:
        meter (StallMeter): the running stall meter.
        step (str): the step's name in the CSV.
        call: a no-argument callable returning a result or a coroutine.
        ts (ThingSpeak | None): given for bulk_post steps, to report the
            status code and response size.

    Returns:
        the step's result.
    """
    meter.take()
    t0 = time.ticks_ms()
    result = call()
    if hasattr(result, "send"):        # a coroutine: await it
        result = await result
    ms = time.ticks_diff(time.ticks_ms(), t0)
    await asyncio.sleep_ms(30)         # let the meter see a blocking call's stall
    status = ts._last_status if ts is not None else ""
    size = ts._last_len if ts is not None else ""
    row(step, result, "" if status is None else status, size, ms, meter.take())
    return result


async def countdown(text, seconds):
    """Print a countdown for an action of the tester."""
    print("# " + text)
    for left in range(seconds, 0, -5):
        print("#   {} s".format(left))
        await asyncio.sleep_ms(min(5, left) * 1000)


async def setup(meter, wifi, clock, ts):
    """Connect, resolve and sync; True if all three worked."""
    if not await measure(meter, "connect", wifi.connect):
        return False
    if not await measure(meter, "resolve", ts.resolve):
        return False
    return await measure(meter, "sync", clock.sync)


def synthetic_events(clock):
    """5 events in 4 different seconds (two share one), ending 13 s ago.

    The events start in the middle of a clock second, so the two events
    100 ms apart always fall into the same second.
    """
    t0 = time.ticks_add(time.ticks_ms(), -20_000)
    while clock.unix(time.ticks_add(t0, -400)) != clock.unix(time.ticks_add(t0, 400)):
        t0 = time.ticks_add(t0, 100)
    t1 = time.ticks_add(t0, 2000)
    t2 = time.ticks_add(t1, 100)
    return [
        (t0, 0, "RUN", 1.52, 1.52, 2.10, "far", None, None),                     # SAMPLE
        (t1, 1, "RUN", 0.85, 0.85, 1.90, "medium", None, None),                  # ZONE
        (t2, 2, "RUN", 0.84, 0.84, 1.90, "medium", None, None),                  # ENTER
        (time.ticks_add(t0, 5000), 1, "RUN", None, None, 1.95, "clear", None, None),  # ZONE
        (time.ticks_add(t0, 7000), 3, "RUN", None, None, 1.95, "clear", 0.84, 3),     # LEAVE
    ]


def one_update(clock):
    """One SAMPLE update stamped 10 s ago."""
    t = time.ticks_add(time.ticks_ms(), -10_000)
    event = (t, 0, "RUN", 1.0, 1.0, None, "far", None, None)
    updates, _, _ = make_updates([event], clock, time.ticks_ms())
    return updates


async def run_clock(meter, wifi, clock, ts):
    if not await setup(meter, wifi, clock, ts):
        return
    for _ in range(5):
        row("utc", TimeSync.stamp(clock.unix(time.ticks_ms())))
        await asyncio.sleep_ms(2000)


async def run_upload(meter, wifi, clock, ts):
    if not await setup(meter, wifi, clock, ts):
        return
    events = synthetic_events(clock)
    updates, n, last = make_updates(events, clock, time.ticks_ms())
    row("make_updates", "{} updates from {} events".format(len(updates), n))
    for u in updates:
        print("# " + u["created_at"] + " field5=" + str(u["field5"]) + " status=" + u["status"])
    first = await measure(meter, "bulk_post", lambda: ts.bulk_post(updates), ts)
    if first is None:
        print("# stop: no answer over HTTP on port 80; tell the tester")
        return
    await asyncio.sleep_ms((settings.UPLOAD_MIN_GAP_S + 1) * 1000)
    resend = await measure(meter, "bulk_post_resend", lambda: ts.bulk_post(updates), ts)
    ok = first is True and resend is True
    row("resend_ok", ok)
    if not ok:
        print("# stop: the first send and the re-send must both be True; tell the tester")


async def run_fail_password(meter, wifi):
    wifi.disconnect()
    await asyncio.sleep_ms(1000)
    await measure(meter, "connect_wrong_password", wifi.connect)


async def run_fail_key(meter, wifi, clock):
    ts = ThingSpeak(WRONG_KEY, config.THINGSPEAK_CHANNEL_ID)
    if not await setup(meter, wifi, clock, ts):
        return
    updates = one_update(clock)
    await measure(meter, "bulk_post_wrong_key", lambda: ts.bulk_post(updates), ts)


async def run_hotspot_off(meter, wifi, clock, ts):
    if not await setup(meter, wifi, clock, ts):
        return
    await measure(meter, "bulk_post", lambda: ts.bulk_post(one_update(clock)), ts)
    await countdown("switch the hotspot OFF now", 20)
    result = True
    for i in range(4):
        result = await measure(meter, "bulk_post_off_" + str(i),
                               lambda: ts.bulk_post(one_update(clock)), ts)
        if result is None:
            break
        await asyncio.sleep_ms((settings.UPLOAD_MIN_GAP_S + 1) * 1000)
    row("is_connected_after_off", wifi.is_connected())
    await countdown("switch the hotspot ON now", 20)
    for i in range(6):
        if await measure(meter, "reconnect_" + str(i), wifi.connect):
            break
        await asyncio.sleep_ms(settings.WIFI_RETRY_S * 1000)
    if wifi.is_connected():
        await measure(meter, "resolve_again", ts.resolve)
        await measure(meter, "bulk_post_after_reconnect",
                      lambda: ts.bulk_post(one_update(clock)), ts)


async def bench(wifi, clock, ts):
    meter = StallMeter()
    asyncio.create_task(meter.run())
    await asyncio.sleep_ms(100)
    if MODE == "clock":
        await run_clock(meter, wifi, clock, ts)
    elif MODE == "upload":
        await run_upload(meter, wifi, clock, ts)
    elif MODE == "fail_password":
        await run_fail_password(meter, wifi)
    elif MODE == "fail_key":
        await run_fail_key(meter, wifi, clock)
    elif MODE == "hotspot_off":
        await run_hotspot_off(meter, wifi, clock, ts)
    else:
        print("# unknown MODE " + MODE)


def main():
    print("mode,step,result,status,resp_bytes,ms,stall_ms")
    try:
        import ntptime   # noqa: F401  (frozen into the Pico W firmware)
        row("import_ntptime", True)
    except ImportError:
        row("import_ntptime", False)
        print("# summary: ntptime missing; it has to be added to lib/")
        return
    # The radio start blocks, so WiFi() is built here, before the event loop.
    password = WRONG_PASSWORD if MODE == "fail_password" else config.WIFI_PASSWORD
    t0 = time.ticks_ms()
    wifi = WiFi(config.WIFI_SSID, password)
    row("wifi_init", True, ms=time.ticks_diff(time.ticks_ms(), t0))
    clock = TimeSync()
    ts = ThingSpeak(config.THINGSPEAK_WRITE_KEY, config.THINGSPEAK_CHANNEL_ID)
    try:
        asyncio.run(bench(wifi, clock, ts))
    finally:
        asyncio.new_event_loop()
    print("# summary: " + "; ".join("{}={}".format(s, r) for s, r in _rows))


main()
