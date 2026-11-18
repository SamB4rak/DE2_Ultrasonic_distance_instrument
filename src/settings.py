"""Settings of the ultrasonic distance instrument, grouped by part.

Times (``*_MS``, ``*_US``, ``*_S``) are whole numbers, because the Pico's
timing functions don't accept floats; convert seconds with ``* 1000``.
Distances are in metres, colours are ``(r, g, b)`` with values 0-255.
"""

# --- General ---
I2C_FREQ_HZ = 400_000        # drop to 100_000 if bus errors appear

# --- Distances, zones and events ---
D_MIN_M = 0.02               # closer readings are clamped to this
D_MAX_M = 4.0                # farther readings count as "no target"
ZONE_FAR_M = 1.0             # d > 1.0 m -> far
ZONE_CLOSE_M = 0.3           # d < 0.3 m -> close
ZONE_HYST_M = 0.03
TICK_HZ_MIN = 1.0
TICK_HZ_MAX = 5.0            # also the relay limit: tick starts are >= 200 ms apart
TICK_D_FAR_M = 1.0           # >= this -> TICK_HZ_MIN
TICK_D_NEAR_M = 0.1          # <= this -> TICK_HZ_MAX
C_DEFAULT_MPS = 343.4        # speed of sound at 20 °C (no temperature sensor)
BAR_FULL_SCALE_M = 2.0
ALERT_M = 1.0                # an object closer than this is a visit (ENTER/LEAVE)
ALERT_CONFIRM = 2            # detector calls (slots) in a row inside ALERT_M before ENTER
LEAVE_HOLD_MS = 3000         # outside ALERT_M this long before LEAVE
SAMPLE_PERIOD_S = 60         # a SAMPLE event this often while RUN

# --- Ultrasonic sensors ---
ECHO_WAIT_MS = 36            # evaluate the edges this long after Trig
ECHO_TIMEOUT_US = 30_000     # rise -> fall limit (about 5.1 m)
FAULT_AFTER = 3              # consecutive NO_RISE or BUSY outcomes before a sensor is faulty

# --- RGB LED ---
PWM_FREQ_HZ = 1000
COLOR_GREEN = (0, 255, 0)
COLOR_YELLOW = (255, 90, 0)  # calibrated on the bench (R 255, G picked from 40..200)
COLOR_RED = (255, 0, 0)
COLOR_BLUE = (0, 0, 255)
COLOR_MAGENTA = (255, 0, 255)
COLOR_OFF = (0, 0, 0)

# --- Relay ---
RELAY_PULSE_MS = 50

# --- Button ---
BUTTON_POLL_MS = 20
BUTTON_DEBOUNCE_MS = 40      # raw level stable this long (3 polls)

# --- Display ---
DISPLAY_PERIOD_MS = 200
DISPLAY_RETRY_MS = 2000      # re-initialise a missing display at most this often
OLED_CONTROLLER = "sh1106"   # or "ssd1306" if the bench test says so

# --- Wi-Fi, clock and ThingSpeak ---
UPLOAD_PERIOD_S = 20         # try a batch upload this often
UPLOAD_MIN_GAP_S = 15        # ThingSpeak free plan: at least 15 s between bulk updates
UPLOAD_BATCH_MAX = 50        # updates per bulk request (ThingSpeak allows 960)
WIFI_CONNECT_TIMEOUT_S = 15
WIFI_RETRY_S = 10
HTTP_TIMEOUT_S = 5
HTTP_FAILS_BEFORE_RESOLVE = 3  # failed uploads in a row before DNS is asked again
NTP_RESYNC_S = 21600         # re-sync the clock every 6 h
NTP_RETRY_S = 300            # after a failed sync, wait this long before the next try
THINGSPEAK_HOST = "api.thingspeak.com"
THINGSPEAK_PORT = 80

# --- Task timing ---
RANGING_SLOT_MS = 60
INDICATOR_STEP_MS = 50
EVENT_QUEUE_LEN = 100        # events kept while offline; the oldest go first
