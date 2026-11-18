"""Pin map of the instrument.

Raspberry Pi Pico WH on the DFRobot Pico Gravity board. Every module takes
its pin numbers from here.

    * The Pico's pins are 3.3 V only. Each HC-SR04 Echo goes through a divider
      (1 kOhm in series, 2 kOhm to GND), and each Trig has 10 kOhm to GND.
    * The HC-SR04s and the relay run from VBUS 5 V (pin 40). Nothing is
      connected to the relay contacts.
    * GP23, GP24, GP25 and GP29 are used by the Wi-Fi chip.
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
