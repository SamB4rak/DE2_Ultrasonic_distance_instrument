# Whole-system tests

Not run yet: the test on the hardware is still to come. This folder will hold:

| File | What |
|---|---|
| `<date>-system.csv` | output of `tests/bench/bench_system.py` (5 minutes): events, uploads, zone-to-LED delays and 10 s statistics, with the loop lateness (maximum and 99th percentile), pings per second per sensor, display updates per second and the upload interval |
| `<date>-stopwatch.csv` | the same script while walking into range and out again 3 times, with the stopwatch times noted next to the ENTER and LEAVE rows |
| `<date>-faults.csv` | the same script while the Echo jumper of the left sensor and then the OLED's SDA jumper are pulled and re-plugged, and while the hotspot is off for 2 minutes |
| `<date>-thingspeak.png` | the channel's chart or table view with the logged events (never the API Keys tab) |
| `<date>-setup.jpg` | the whole setup from above |
| `<date>-sensors.jpg` | the two sensors with their dividers |
| `<date>-oled.jpg` | the display showing a reading and the event rows |
| `<date>-led-green.jpg`, `-yellow.jpg`, `-red.jpg`, `-blue.jpg`, `-magenta.jpg` | the LED in each state |

How: wire with USB unplugged, check the 5 V rail and both dividers, and start only after the wiring is confirmed; then deploy `src/` to the Pico and run `mpremote connect auto run tests/bench/bench_system.py`.
