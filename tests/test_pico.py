"""Pico test: blinks the Pico's own LED, then checks how six input pins are wired.
Run it on the Pico after uploading src/:  mpremote run tests/test_pico.py
Don't press the button while it runs.
"""
import os
import time
from machine import Pin
import board

# Trig pins low first, as in the program, so no echo is running during the check.
Pin(board.PIN_TRIG_1, Pin.OUT, value=0)
Pin(board.PIN_TRIG_2, Pin.OUT, value=0)

led = Pin("LED", Pin.OUT)
for _ in range(5):                          # the Pico runs code: its LED blinks 5 times
    led.value(1)
    time.sleep_ms(200)
    led.value(0)
    time.sleep_ms(200)
print(os.uname().machine, "- MicroPython", os.uname().release)

# (pin, pull, value with the right wiring, what to check if not)
CHECKS = [
    (board.PIN_BUTTON, Pin.PULL_UP, 1, "button: two diagonal legs, GP17 and GND"),
    (board.PIN_ECHO_1, Pin.PULL_UP, 0, "Echo 1: GP21 to the middle of its 1k/2k divider, not to 3V3"),
    (board.PIN_ECHO_2, Pin.PULL_UP, 0, "Echo 2: GP19 to the middle of its 1k/2k divider, not to 3V3"),
    (board.PIN_RELAY, Pin.PULL_DOWN, 1, "relay: IN on GP16 with the 4.7k pull-up to 3V3"),
    (board.PIN_SDA, Pin.PULL_DOWN, 1, "SDA: GP4 (pin 6) to the OLED, 4.7k pull-up to 3V3"),
    (board.PIN_SCL, Pin.PULL_DOWN, 1, "SCL: GP5 (pin 7) to the OLED, 4.7k pull-up to 3V3"),
]
bad = 0
for pin, pull, expected, hint in CHECKS:
    value = Pin(pin, Pin.IN, pull).value()
    if value == expected:
        print("GP%d ok" % pin)
    else:
        bad += 1
        print("GP%d reads %d, expected %d: check %s" % (pin, value, expected, hint))
print("Done, wiring looks right." if bad == 0 else "Done, %d pin(s) to check." % bad)
