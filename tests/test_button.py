"""Button test: prints every press for 20 seconds.

Run it on the Pico after uploading src/:  mpremote run tests/test_button.py
"""
import time
from machine import Pin

import board

button = Pin(board.PIN_BUTTON, Pin.IN, Pin.PULL_UP)    # pressed = 0
presses = 0
was_pressed = False
end = time.ticks_add(time.ticks_ms(), 20_000)

print("Press the button a few times...")
while time.ticks_diff(end, time.ticks_ms()) > 0:
    pressed = button.value() == 0
    if pressed and not was_pressed:
        presses += 1
        print("Press", presses)
    was_pressed = pressed
    time.sleep_ms(20)    # checking every 20 ms also skips the contact bounce

print("Done,", presses, "presses.")
