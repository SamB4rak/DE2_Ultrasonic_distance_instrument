"""Bench test for the relay module (run on the Pico).

Wiring (pin by pin in docs/img/ultrasonic_wiring_diagram.svg). Wire with
USB unplugged; check it before plugging in, and run nothing until the
tester confirms it with "wired OK". Nothing is ever connected to the relay
contacts.
    * Power: the module's VCC (DC+) on the VBUS 5 V rail (Pico pin 40,
      centre header), never a 3V3 pin; GND (DC-) to GND; all grounds
      common; USB power only, from a port or charger of >= 1 A. 100 nF plus
      10-47 uF (rated >= 10 V, stripe/- leg on GND) across the module's VCC
      and GND. The VCC-JD_VCC jumper fitted and the H/L jumper on L, if the
      module has them. Use single jumpers: the module's pin order differs
      from a Gravity row.
    * Beeper check from the 5 V rail before plugging in: no steady beep to
      GND, 3V3 or any GPIO row; the breadboard GND rail beeps to a Pico GND.
    * Pre-check before IN touches GP16: IN on 3V3 (pin 36), power up: the
      module LED stays dark and the coil silent. Unplug, IN on GND, power
      up: the LED lights and the relay clicks. Unplug.
    * Then IN -> GP16 (pin 21), plus 4.7 kOhm from IN to 3V3, so the module
      stays off while the Pico isn't driving the pin.
    * If the pre-check showed any glow or click with IN on 3V3, stop: the
      module needs an NPN transistor stage, which makes it active HIGH.

Power-up check (before the first run): plug USB in and watch the module
for 5 s; once the full program is installed, also once with the button
held during reset (safe boot). Until a program sets up GP16 the Pico
doesn't drive it, so only the 4.7 kOhm pull-up holds IN: the module LED
must stay dark and the coil silent.

After "wired OK", deploy ``src/`` (``python tools/deploy.py``), then run one mode:
    mpremote connect auto run tests/bench/bench_relay.py
    mpremote connect auto exec "MODE='rates'" + run tests/bench/bench_relay.py

Modes (global ``MODE``, default ``"level"``):
    level:  the relay is created (it must stay dark and silent), held off
            for 5 s, then switched on for 1 s and off for 2 s, three times.
            The tester confirms: dark and silent while off, LED and a click
            on each ``on()``. CSV ``step,action,pin_level,is_on,t_ms``.
            Stop if the module LED glows faintly while off, or the relay
            won't release.
    rates:  pulses of ``settings.RELAY_PULSE_MS`` at 1, 2, 3, 4 and 5 Hz,
            10 s each, with 3 s of silence between rates. The pulses start
            on a fixed grid (``ticks_add``), each as its own task, as in the
            program. The tester confirms one clean click per pulse. CSV
            ``rate_hz,pulses,period_ms,mean_ms,max_dev_ms`` (the measured
            start-to-start interval and its largest deviation). Never faster
            than 5 Hz: the relay is rated for 5 operations per second.

Output: one CSV header, the rows, then a ``# summary:`` line.
"""

import asyncio
import time

import board
import settings
from relay import Relay

MODE = globals().get("MODE", "level")
RATES_HZ = (1, 2, 3, 4, 5)
RATE_S = 10          # seconds per rate
GAP_S = 3            # silence between rates
MAX_HZ = 5           # the relay's rating; never exceed it


def countdown(n):
    """Print a countdown of ``n`` seconds before the first step."""
    print("# before the relay is used: the LED must be dark, the coil silent")
    print("# (if it glows, an earlier program left GP16 on: unplug USB and retry)")
    for i in range(n, 0, -1):
        print("# starting in", i)
        time.sleep(1)


def run_level():
    """Check the relay's resting state and its active level."""
    t0 = time.ticks_ms()
    relay = Relay(board.PIN_RELAY)
    step = 0

    def row(action):
        # pin_level is the raw GPIO level, read back for the log.
        print("{},{},{},{},{}".format(step, action, relay._pin.value(),
                                      int(relay.is_on()),
                                      time.ticks_diff(time.ticks_ms(), t0)))

    try:
        print("# mode=level active_low={}".format(board.RELAY_ACTIVE_LOW))
        print("# created: the module LED must be dark and the coil silent")
        print("step,action,pin_level,is_on,t_ms")
        row("created")
        time.sleep(5)
        for _ in range(3):
            step += 1
            relay.on()
            row("on")
            time.sleep(1)
            relay.off()
            row("off")
            time.sleep(2)
        print("# summary: on/off cycles=3 active_low={} off_level={}".format(
            board.RELAY_ACTIVE_LOW, relay._pin.value()))  # raw GPIO level
    finally:
        relay.off()


async def one_rate(relay, hz):
    """Pulse the relay at ``hz`` for ``RATE_S`` seconds.

    Args:
        relay (Relay): the relay.
        hz (int): pulses per second, at most ``MAX_HZ``.

    Returns:
        tuple: ``(pulses, period_ms, mean_ms, max_dev_ms)``.
    """
    assert 0 < hz <= MAX_HZ
    period = int(1000 // hz)
    min_gap = 1000 // MAX_HZ
    # A pulse must end before the next one starts.
    assert settings.RELAY_PULSE_MS < min_gap
    count = int(RATE_S * hz)
    starts = []
    task = None
    nxt = time.ticks_ms()
    for _ in range(count):
        wait = time.ticks_diff(nxt, time.ticks_ms())
        if wait > 0:
            await asyncio.sleep_ms(wait)
        start = time.ticks_ms()
        starts.append(start)
        task = asyncio.create_task(relay.pulse())
        nxt = time.ticks_add(nxt, period)
        # If this start ran late, never let the next one come sooner than
        # 200 ms after it: the relay is rated for 5 operations per second.
        if time.ticks_diff(nxt, start) < min_gap:
            nxt = time.ticks_add(start, min_gap)
    # Only the last pulse can still be running: each one is shorter than
    # the gap to the next start.
    await task
    gaps = [time.ticks_diff(starts[i], starts[i - 1]) for i in range(1, len(starts))]
    mean = sum(gaps) / len(gaps) if gaps else 0
    dev = max(abs(g - period) for g in gaps) if gaps else 0
    return count, period, mean, dev


async def rates_main():
    """Run every rate in ``RATES_HZ`` and print one CSV row per rate."""
    relay = Relay(board.PIN_RELAY)
    total = 0
    try:
        print("# mode=rates pulse_ms={} rate_s={}".format(settings.RELAY_PULSE_MS, RATE_S))
        print("rate_hz,pulses,period_ms,mean_ms,max_dev_ms")
        for hz in RATES_HZ:
            print("# {} Hz for {} s: count the clicks".format(hz, RATE_S))
            pulses, period, mean, dev = await one_rate(relay, hz)
            total += pulses
            print("{},{},{},{:.1f},{}".format(hz, pulses, period, mean, dev))
            print("# silence for {} s".format(GAP_S))
            await asyncio.sleep_ms(GAP_S * 1000)
        print("# summary: rates={} total_pulses={} relay_off={}".format(
            len(RATES_HZ), total, int(not relay.is_on())))
    finally:
        relay.off()


def main():
    """Run the selected mode."""
    countdown(3)
    if MODE == "level":
        run_level()
    elif MODE == "rates":
        asyncio.run(rates_main())
    else:
        raise ValueError("MODE must be 'level' or 'rates'")


main()
