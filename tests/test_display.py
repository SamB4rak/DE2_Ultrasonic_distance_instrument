"""Display test: finds the OLED on I2C and writes "Hello world." on it.

Run it on the Pico after uploading src/:  mpremote run tests/test_display.py
"""
from machine import I2C, Pin

import board
import settings
import sh1106

i2c = I2C(board.I2C_ID, sda=Pin(board.PIN_SDA), scl=Pin(board.PIN_SCL),
          freq=settings.I2C_FREQ_HZ)
found = i2c.scan()
print("I2C devices:", [hex(a) for a in found])

if board.ADDR_OLED not in found:
    print("No display at 0x3c: check SDA (GP4), SCL (GP5), 3V3 and GND.")
else:
    oled = sh1106.SH1106_I2C(128, 64, i2c, addr=board.ADDR_OLED)
    oled.fill(0)
    oled.text("Hello world.", 0, 0)
    oled.text("DE2 ultrasonic", 0, 16)
    oled.show()
    print("Done, look at the display.")
