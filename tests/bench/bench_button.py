"""Bench test for the push button (run on the Pico).

Wiring (pin by pin in docs/img/ultrasonic_wiring_diagram.svg). Wire with
USB unplugged; check it before plugging in, and run nothing until the
tester confirms it with "wired OK".
    * Button: use two diagonally opposite legs (two of the four legs are
      always connected). One leg to the green D pin of the Gravity row GP17
      (Pico pin 22), the other to that row's black GND pin. Nothing on the
      red pin: the Pico's internal pull-up is used.
    * Beeper check with USB unplugged: D to GND is silent until the button
      is pressed. If other parts are already wired, also check the 5 V
      rail: no steady beep to GND, 3V3 or any GPIO row, and the breadboard
      GND rail beeps to a Pico GND pin.
    * All grounds common; USB power only, from a computer port or a charger
      of >= 1 A.

After "wired OK", deploy ``src/`` (``python tools/deploy.py``), then run one mode:
    mpremote connect auto run tests/bench/bench_button.py
    mpremote connect auto exec "MODE='bounce'" + run tests/bench/bench_button.py

Modes (global ``MODE``, default ``"count"``):
    count:  after a countdown, ``Button.poll()`` runs every
            ``settings.BUTTON_POLL_MS`` for 30 s, as in the program. The
            tester presses the button 20 times, at any pace. One CSV row per
            counted press: ``press,t_ms``. The count must be 20.
    bounce: the raw pin is sampled at 1 kHz until 10 presses (and their
            releases) have been seen, or 90 s have passed. For each edge
            the row gives the bounce: the time from the first change to the
            last change within 50 ms, and the number of changes (an edge
            that ends at the old level is a ``glitch``). The same samples
            go to ``Button.poll()`` every ``BUTTON_POLL_MS``; ``debounced``
            counts the presses it reported since the last row (a press
            with long bounce may show up on the next row). PASS needs the
            longest bounce below ``BUTTON_DEBOUNCE_MS`` and as many
            debounced presses as raw presses. CSV ``n,edge,t_ms,bounce_ms,changes,debounced``.

Output: one CSV header, the rows, then a ``# summary:`` line.
"""

import time

import board
import settings
from button import Button

MODE = globals().get("MODE", "count")
COUNT_S = 30          # count mode window
EXPECTED = 20         # presses the tester makes in count mode
PRESSES = 10          # bounce mode: presses to measure
BOUNCE_LIMIT_S = 90   # bounce mode gives up after this
WINDOW_MS = 50        # changes this soon after the first one are bounce


def countdown(n, what):
    """Print what to do, then a countdown of ``n`` seconds."""
    print("# " + what)
    for i in range(n, 0, -1):
        print("# starting in", i)
        time.sleep(1)
    print("# go")


def run_count():
    """Count debounced presses for ``COUNT_S`` seconds."""
    countdown(5, "press the button {} times within {} s".format(EXPECTED, COUNT_S))
    b = Button(board.PIN_BUTTON)
    period = int(settings.BUTTON_POLL_MS)
    t0 = time.ticks_ms()
    end = time.ticks_add(t0, COUNT_S * 1000)
    nxt = t0
    presses = 0
    print("# mode=count poll_ms={} debounce_ms={}".format(
        period, settings.BUTTON_DEBOUNCE_MS))
    print("press,t_ms")
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        wait = time.ticks_diff(nxt, time.ticks_ms())
        if wait > 0:
            time.sleep_ms(wait)
        now = time.ticks_ms()
        if b.poll(now_ms=now):
            presses += 1
            print("{},{}".format(presses, time.ticks_diff(now, t0)))
        nxt = time.ticks_add(nxt, period)
    print("# summary: count={} expected={} {}".format(
        presses, EXPECTED, "PASS" if presses == EXPECTED else "FAIL"))


def run_bounce():
    """Measure the raw bounce of ``PRESSES`` presses and releases at 1 kHz."""
    countdown(5, "press and release the button {} times, about 1 s apart".format(PRESSES))
    b = Button(board.PIN_BUTTON)
    pin = b._pin                  # the raw level, read directly on purpose
    poll_every = int(settings.BUTTON_POLL_MS)
    t0 = time.ticks_ms()
    limit = time.ticks_add(t0, BOUNCE_LIMIT_S * 1000)
    level = pin.value()           # the settled level
    edge_start = None             # ms of the first change of this edge
    last_change = 0
    changes = 0
    prev = level
    debounced = 0
    total = 0             # presses Button.poll() reported
    presses = 0
    releases = 0
    max_bounce = 0
    n = 0
    sample = 0
    nxt = time.ticks_us()
    print("# mode=bounce sample_hz=1000 window_ms={} debounce_ms={}".format(
        WINDOW_MS, settings.BUTTON_DEBOUNCE_MS))
    print("n,edge,t_ms,bounce_ms,changes,debounced")
    while releases < PRESSES and time.ticks_diff(limit, time.ticks_ms()) > 0:
        wait = time.ticks_diff(nxt, time.ticks_us())
        if wait > 0:
            time.sleep_us(wait)
        nxt = time.ticks_add(nxt, 1000)
        now = time.ticks_ms()
        raw = pin.value()
        sample += 1
        if sample % poll_every == 0 and b.poll(now_ms=now):
            debounced += 1
        if raw != prev:
            prev = raw
            if edge_start is None:
                edge_start = now
                changes = 0
            last_change = now
            changes += 1
        if edge_start is not None and time.ticks_diff(now, edge_start) >= WINDOW_MS:
            # The edge is over: report it while the contacts are quiet.
            bounce = time.ticks_diff(last_change, edge_start)
            if prev == level:
                edge = "glitch"     # back at the old level: no real edge
            else:
                level = prev
                max_bounce = max(max_bounce, bounce)
                edge = "press" if level == 0 else "release"
                if level == 0:
                    presses += 1
                else:
                    releases += 1
            n += 1
            print("{},{},{},{},{},{}".format(n, edge, time.ticks_diff(edge_start, t0),
                                             bounce, changes, debounced))
            total += debounced
            debounced = 0
            edge_start = None
            nxt = time.ticks_us()   # printing took time: no burst to catch up
    total += debounced
    ok = max_bounce < settings.BUTTON_DEBOUNCE_MS and total == presses
    print("# summary: presses={} releases={} debounced={} max_bounce_ms={} "
          "debounce_ms={} {}".format(presses, releases, total, max_bounce,
                                     settings.BUTTON_DEBOUNCE_MS,
                                     "PASS" if ok else "FAIL"))


def main():
    """Run the selected mode."""
    if MODE == "count":
        run_count()
    elif MODE == "bounce":
        run_bounce()
    else:
        raise ValueError("MODE must be 'count' or 'bounce'")


main()
