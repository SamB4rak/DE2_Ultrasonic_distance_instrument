# Display evidence

Bench tests of the OLED display (`tests/bench/bench_display.py`). Not run yet.

What each run records:

| File | What | How |
|---|---|---|
| `<date>-scan.csv` | I2C scan and the controller check (test pattern with a border) | `MODE='scan'`; the tester checks the pattern by eye |
| `<date>-timing.csv` | Full-frame time and the longest loop block per page, at 100 kHz and 400 kHz, 20 frames each | `MODE='timing'`, timed with `ticks_us` |
| `<date>-layout.jpg` | Photo of the real screen layout with sample values | `MODE='layout'` |
| `<date>-fault.csv` | SDA pulled while running: `ok` goes False, nothing crashes, the picture returns after re-plugging | `MODE='fault'` |
