"""Ultrasonic sensor test: prints both distances twice a second for 15 seconds.

Run it on the Pico after uploading src/:  mpremote run tests/test_sensors.py
Move a book in front of the sensors; the numbers should follow it.
"""
import time
from machine import Pin, time_pulse_us

import board
import settings

SENSORS = [
    ("left", Pin(board.PIN_TRIG_1, Pin.OUT, value=0), Pin(board.PIN_ECHO_1, Pin.IN)),
    ("right", Pin(board.PIN_TRIG_2, Pin.OUT, value=0), Pin(board.PIN_ECHO_2, Pin.IN)),
]


def distance_cm(trig, echo):
    trig.value(1)
    time.sleep_us(10)                       # 10 us start pulse
    trig.value(0)
    t = time_pulse_us(echo, 1, 30_000)      # how long Echo stays HIGH, in us
    if t < 0:
        return None                         # no echo within 30 ms
    return t * settings.C_DEFAULT_MPS / 2 / 10_000    # there and back, so halved


for _ in range(30):
    line = []
    for name, trig, echo in SENSORS:
        d = distance_cm(trig, echo)
        line.append("%s: %s" % (name, "---" if d is None else "%.1f cm" % d))
        time.sleep_ms(60)                   # let the echo die out before the next ping
    print("   ".join(line))
    time.sleep_ms(380)
