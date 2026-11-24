"""Entry point of the ultrasonic distance instrument on the Pico.

``run()`` builds the hardware in a safe order (the relay first, already off),
shows a boot screen, then runs the asyncio tasks from ``tasks.py`` until an
error stops them. Whatever happens, the relay and the LED end up off.

Hold the button while resetting the board to skip the start (safe boot): the
program then ends at once and the REPL stays free for development.

The Wi-Fi and ThingSpeak settings come from ``config.py`` (not part of the
repository). Without it the instrument runs without the cloud log.

New code for this project.
"""

import asyncio
import sys

import micropython
from machine import I2C, Pin

import board
import logic
import settings
import tasks
from button import Button
from display import Display
from hcsr04 import HCSR04
from relay import Relay
from rgb_led import RGBLed
from state import State


def _network():
    """The Wi-Fi, ThingSpeak and clock objects, if ``config.py`` exists.

    Constructing ``WiFi`` starts the radio, which blocks for a moment, so
    this runs before the event loop starts.

    Returns:
        tuple: ``(wifi, ts, clock)``, or ``(None, None, None)`` without a
        config (the logger then isn't started).
    """
    try:
        import config
    except ImportError:
        return None, None, None
    from thingspeak import ThingSpeak
    from timesync import TimeSync
    from wifi import WiFi
    wifi = WiFi(config.WIFI_SSID, config.WIFI_PASSWORD)
    ts = ThingSpeak(config.THINGSPEAK_WRITE_KEY, config.THINGSPEAK_CHANNEL_ID)
    return wifi, ts, TimeSync()


def run():
    """Start the instrument. Never calls ``sys.exit()``.

    On MicroPython ``sys.exit()`` soft-reboots the board, which would run
    ``main.py`` again, so every way out simply returns.

    1. Reserve memory for error messages from interrupts.
    2. Safe boot: if the button is held, print a note and return.
    3. Build the relay first (off from the start), then the LED (off).
    4. Build the display (boot screen), the sensors, the button, the event
       detector and, with a config, the network objects; then run
       ``main()`` until it ends.

    Any ``Exception`` is printed and, if the display works, shown on a fatal
    screen. In every case (also Ctrl-C) the relay and the LED are switched
    off at the end.
    """
    micropython.alloc_emergency_exception_buf(100)
    if Button.held_at_boot(board.PIN_BUTTON):
        print("safe boot: app not started")
        return
    relay = Relay(board.PIN_RELAY)
    led = RGBLed(board.PIN_LED_R, board.PIN_LED_G, board.PIN_LED_B)
    display = None
    try:
        i2c = I2C(board.I2C_ID, sda=Pin(board.PIN_SDA), scl=Pin(board.PIN_SCL),
                  freq=settings.I2C_FREQ_HZ)
        display = Display(i2c)
        display.message("Ultrasonic", "starting...")
        display.show_now()
        s1 = HCSR04(board.PIN_TRIG_1, board.PIN_ECHO_1, "L")
        s2 = HCSR04(board.PIN_TRIG_2, board.PIN_ECHO_2, "R")
        button = Button(board.PIN_BUTTON)
        detector = logic.EventDetector()
        wifi, ts, clock = _network()
        asyncio.run(main(State(), relay, led, display, s1, s2, button,
                         detector, wifi, ts, clock))
    except Exception as e:
        sys.print_exception(e)
        if display is not None and display.ok:
            display.message("FATAL ERROR", type(e).__name__)
            display.show_now()
    finally:
        relay.off()
        led.off()


async def main(state, relay, led, display, s1, s2, button, detector,
               wifi=None, ts=None, clock=None):
    """Run every task until one of them raises.

    Args:
        state (State): the shared state.
        relay (Relay), led (RGBLed), display (Display): the outputs.
        s1 (HCSR04), s2 (HCSR04): the left and right sensors.
        button (Button): the RUN/OFF button.
        detector (logic.EventDetector): the event detector.
        wifi (WiFi | None), ts (ThingSpeak | None), clock (TimeSync | None):
            the network objects; the logger runs only if ``wifi`` is given.

    Raises:
        Exception: whatever ended a task; ``run()`` handles it.
    """
    running = [
        asyncio.create_task(tasks.mode_task(state, button)),
        asyncio.create_task(tasks.ranging_task(state, s1, s2, detector)),
        asyncio.create_task(tasks.indicator_task(state, led, relay)),
        asyncio.create_task(tasks.display_task(state, display)),
    ]
    if wifi is not None:
        running.append(asyncio.create_task(tasks.logger_task(state, wifi, ts, clock)))
    await asyncio.gather(*running)


if __name__ == "__main__":
    run()
