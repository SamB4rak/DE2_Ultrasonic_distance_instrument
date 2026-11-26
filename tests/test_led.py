"""RGB LED test: green, yellow, red and blue, one second each, three times.

Run it on the Pico after uploading src/:  mpremote run tests/test_led.py
"""
import time
from machine import Pin, PWM

import board
import settings

leds = [PWM(Pin(p), freq=settings.PWM_FREQ_HZ)
        for p in (board.PIN_LED_R, board.PIN_LED_G, board.PIN_LED_B)]


def show(color):
    for led, value in zip(leds, color):
        led.duty_u16(value * 257)          # 0-255 -> 0-65535


try:
    for _ in range(3):
        for name in ("GREEN", "YELLOW", "RED", "BLUE"):
            print(name)
            show(getattr(settings, "COLOR_" + name))
            time.sleep(1)
finally:
    show(settings.COLOR_OFF)
    print("Done, LED off.")
