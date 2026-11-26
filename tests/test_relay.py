"""Relay test: the relay clicks five times, once a second.

Run it on the Pico after uploading src/:  mpremote run tests/test_relay.py
Nothing is connected to the relay contacts.
"""
import time
from machine import Pin

import board

OFF = 1 if board.RELAY_ACTIVE_LOW else 0    # our module switches on with a LOW input
relay = Pin(board.PIN_RELAY, Pin.OUT, value=OFF)

try:
    for i in range(5):
        print("Click", i + 1)
        relay.value(1 - OFF)
        time.sleep_ms(200)
        relay.value(OFF)
        time.sleep_ms(800)
finally:
    relay.value(OFF)
    print("Done, relay off.")
