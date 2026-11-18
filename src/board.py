"""Pin map and wiring polarity of the ultrasonic distance instrument.

Raspberry Pi Pico WH on a DFRobot Pico Gravity board V3.0. Every module
imports its GPIO numbers from here; no other file hard-codes a pin.

Electrical notes:
    * RP2040 pins are 3.3 V only. Each HC-SR04 Echo reaches its GPIO through a
      divider (1 kOhm in series, 2 kOhm to GND), and each Trig line has a
      10 kOhm pull-down.
    * The HC-SR04s and the relay module run from VBUS 5 V (Pico pin 40), never
      from a Gravity 3V3 row. The relay contacts stay unwired.
    * GP23, GP24, GP25 and GP29 belong to the CYW43439 Wi-Fi chip: never use them.
"""

PIN_BUTTON = 17
PIN_TRIG_1 = 20   # HC-SR04 #1 (left)
PIN_ECHO_1 = 21   # via 1 kΩ series / 2 kΩ to GND
PIN_TRIG_2 = 18   # HC-SR04 #2 (right)
PIN_ECHO_2 = 19   # via 1 kΩ series / 2 kΩ to GND
PIN_LED_R = 12
PIN_LED_G = 13
PIN_LED_B = 14
PIN_RELAY = 16
I2C_ID = 0
PIN_SDA = 4
PIN_SCL = 5
ADDR_OLED = 0x3C
RELAY_ACTIVE_LOW = True
RGB_COMMON_ANODE = False
