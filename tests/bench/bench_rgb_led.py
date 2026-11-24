"""Bench test for the RGB LED module (run on the Pico).

Wiring (pin by pin in docs/img/ultrasonic_wiring_diagram.svg). Wire with USB
unplugged and check the grounds with a beeper before plugging in.
    * RGB module: - to GND; R, G, B to GP12, GP13, GP14 (pins 16, 17, 19).
    * All grounds common; USB power only, from a port or charger of >= 1 A.

Deploy ``src/`` first (``python tools/deploy.py``), then run:
    mpremote connect auto run tests/bench/bench_rgb_led.py
    mpremote connect auto exec "MODE='yellow'" + run tests/bench/bench_rgb_led.py

Modes (global ``MODE``, default ``"cycle"``):
    cycle:   green, yellow, red, blue, magenta and off, ``HOLD_S`` s each
             (global, default 2); the tester confirms each colour by eye.
    yellow:  red at 255, green stepped over 40, 60, ..., 200, 3 s each; the
             tester picks the best-looking yellow for ``COLOR_YELLOW``.
    compare: red at 255, green over the list ``G_VALUES`` (global), ``HOLD_S``
             s each, to choose between close candidates, e.g.
             ``exec "MODE='compare'; G_VALUES=[80, 90, 100]; HOLD_S=6"``.

Output: one CSV header, one row per step, then a ``# summary:`` line.
"""

import time

import board
import settings
from rgb_led import RGBLed

MODE = globals().get("MODE", "cycle")
HOLD_S = int(globals().get("HOLD_S", 2))   # cycle/compare: seconds per colour
G_VALUES = globals().get("G_VALUES", [80, 90, 100])  # compare: green values


def countdown(n):
    """Print a countdown of ``n`` seconds before the first step."""
    for i in range(n, 0, -1):
        print("# starting in", i)
        time.sleep(1)


def show(led, step, name, rgb, hold_s):
    """Show one colour, print its CSV row and hold it for ``hold_s`` seconds."""
    led.color(rgb)
    # Read the duties back from the PWM outputs (bench only): this is the
    # hardware evidence that value * 257 reached the pins.
    duty = [p.duty_u16() for p in led._pwms]
    r, g, b = led.current
    print("{},{},{},{},{},{},{},{}".format(step, name, r, g, b, duty[0], duty[1], duty[2]))
    time.sleep(hold_s)


def main():
    """Run the selected test pattern."""
    led = RGBLed(board.PIN_LED_R, board.PIN_LED_G, board.PIN_LED_B)
    try:
        print("# mode={} freq_hz={} common_anode={}".format(
            MODE, settings.PWM_FREQ_HZ, board.RGB_COMMON_ANODE))
        countdown(3)
        print("step,name,r,g,b,duty_r,duty_g,duty_b")
        if MODE == "cycle":
            steps = (
                ("green", settings.COLOR_GREEN),
                ("yellow", settings.COLOR_YELLOW),
                ("red", settings.COLOR_RED),
                ("blue", settings.COLOR_BLUE),
                ("magenta", settings.COLOR_MAGENTA),
                ("off", settings.COLOR_OFF),
            )
            for i, (name, rgb) in enumerate(steps):
                show(led, i + 1, name, rgb, HOLD_S)
            print("# summary: cycle of {} colours, {} s each, yellow={}".format(
                len(steps), HOLD_S, settings.COLOR_YELLOW))
        elif MODE == "yellow":
            n = 0
            for g in range(40, 201, 20):
                n += 1
                show(led, n, "yellow_g{}".format(g), (255, g, 0), 3)
            print("# summary: yellow calibration, R=255, G=40..200 step 20, {} steps, 3 s each".format(n))
        elif MODE == "compare":
            for i, g in enumerate(G_VALUES):
                show(led, i + 1, "yellow_g{}".format(g), (255, g, 0), HOLD_S)
            print("# summary: yellow compare, R=255, G={}, {} s each".format(
                list(G_VALUES), HOLD_S))
        else:
            print("# summary: ERROR unknown MODE {!r}".format(MODE))
    finally:
        led.deinit()


main()
