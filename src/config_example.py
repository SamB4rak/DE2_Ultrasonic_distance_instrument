"""Template for the secrets file ``config.py`` (never committed).

Copy this file to ``src/config.py`` and fill in your own values in your
editor. ``config.py`` is git-ignored, and ``tools/deploy.py`` copies it to the
Pico. Only ``main.py`` imports it. Never print these values or paste them
anywhere.
"""

WIFI_SSID = "your-2.4GHz-hotspot"
WIFI_PASSWORD = "your-hotspot-password"
THINGSPEAK_WRITE_KEY = "your-write-api-key"
THINGSPEAK_CHANNEL_ID = "your-channel-id"   # not secret, but per user; the bulk upload needs it
