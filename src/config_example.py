"""Template for ``config.py``, the file with your Wi-Fi and ThingSpeak details.

Copy it to ``src/config.py`` and fill in your own values. ``config.py`` stays
out of git, and ``tools/deploy.py`` copies it to the Pico.
"""

WIFI_SSID = "your-2.4GHz-hotspot"
WIFI_PASSWORD = "your-hotspot-password"
THINGSPEAK_WRITE_KEY = "your-write-api-key"
THINGSPEAK_CHANNEL_ID = "your-channel-id"   # not secret, but everyone has their own
