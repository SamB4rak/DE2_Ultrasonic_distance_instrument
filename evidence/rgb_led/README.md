# RGB LED bench evidence

**Date:** 2026-11-18. **Board:** Raspberry Pi Pico WH (MicroPython 1.29.0) on the DFRobot Pico Gravity board V3.0, powered over USB.
**Wiring:** common-cathode RGB module, − to GND, R/G/B to GP12/GP13/GP14 (pins 16, 17, 19). Default 4 mA pad drive. PWM at 1000 Hz on all three channels.
**Script:** `tests/bench/bench_rgb_led.py`, run with `mpremote connect auto run …` after `python tools/deploy.py`. The tester watched the LED and confirmed each colour by eye.

| File | What | Result |
|---|---|---|
| `2026-11-18-cycle.csv` | green, yellow (255, 120, 0), red, blue, magenta, off; 2 s each | all colours correct |
| `2026-11-18-cycle-5s.csv` | the same cycle, 5 s each (`HOLD_S = 5`) | all colours correct |
| `2026-11-18-yellow.csv` | yellow sweep: R = 255, G = 40, 60, …, 200, 3 s each | G = 80 and G = 100 looked best |
| `2026-11-18-yellow-repeat.csv` | the same sweep again | same choice |
| `2026-11-18-yellow-compare.csv` | G = 80, 90, 100, 6 s each (`MODE='compare'`) | **G = 90 picked** |
| `2026-11-18-cycle-calibrated.csv` | the cycle with the calibrated yellow (255, 90, 0), 5 s each | all colours correct |

**Outcome:** `COLOR_YELLOW = (255, 90, 0)` in `src/settings.py`. No colour looked too dim, so the pins keep the default 4 mA drive; the optional 12 mA pad setting was not needed.

The planned cycle holds each colour 2 s (`2026-11-18-cycle.csv`). The tester asked for 5 s per colour to judge them more easily, so the later cycles, including the calibrated one, ran at 5 s; the colours and duties are the same.

Lines starting with `#` are comments (the run settings, the countdown and the summary); the rest is CSV. The duty columns show `duty_u16 = value * 257` (0–65535) as read back from the PWM outputs.

**Photo to add:** `2026-11-18-yellow.jpg`, the LED showing the calibrated yellow (255, 90, 0), with the module and its three wires visible.
