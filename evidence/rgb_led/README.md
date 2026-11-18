# RGB LED test

Tested on 18 Nov 2026: Raspberry Pi Pico WH with MicroPython 1.29.0 on the DFRobot Gravity board, powered over USB. The common-cathode RGB module is wired to GP12, GP13 and GP14 (pins 16, 17 and 19), with PWM at 1000 Hz. The test script is `tests/bench/bench_rgb_led.py`; we watched the LED and checked each colour by eye.

| File | What we ran | Result |
|---|---|---|
| `2026-11-18-cycle.csv` | all colours, 2 s each | all correct |
| `2026-11-18-cycle-5s.csv` | the same, 5 s each | all correct |
| `2026-11-18-yellow.csv` | yellow with R = 255 and G from 40 to 200 | G = 80 and 100 looked best |
| `2026-11-18-yellow-repeat.csv` | the same sweep again | same result |
| `2026-11-18-yellow-compare.csv` | G = 80, 90 and 100 side by side | **G = 90** |
| `2026-11-18-cycle-calibrated.csv` | all colours with the new yellow, 5 s each | all correct |

Result: yellow is (255, 90, 0). The default 4 mA pin drive is bright enough.

In the CSV files, lines starting with `#` are notes (settings and summary). The duty columns are the PWM values read back from the pins (0-65535).
