"""Wi-Fi test: connects to the hotspot from config.py and sets the clock from NTP.

Run it on the Pico after uploading src/ with your config.py:
    mpremote run tests/test_wifi.py
It prints the IP address and the time, never the network name or password.
"""
import time
import network
import ntptime

import config

wlan = network.WLAN(network.STA_IF)
wlan.active(True)
wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)

for _ in range(20):                         # wait up to 10 s
    if wlan.isconnected():
        break
    time.sleep_ms(500)

if not wlan.isconnected():
    print("Not connected, status", wlan.status(),
          "(-2: hotspot not found, is it on 2.4 GHz? -3: wrong password)")
else:
    print("Connected, IP address:", wlan.ifconfig()[0])
    try:
        ntptime.settime()                   # sets the Pico's clock to UTC
        print("Time from NTP (UTC): %04d-%02d-%02d %02d:%02d:%02d" % time.gmtime()[:6])
    except OSError as e:
        print("NTP failed:", e)
    wlan.disconnect()
