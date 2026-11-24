"""Bench test for the OLED display (run on the Pico).

Wiring (pin by pin in docs/img/ultrasonic_wiring_diagram.svg). Wire with
USB unplugged, and run nothing until the tester confirms the wiring with
"wired OK".
    * The OLED takes 3V3, never 5 V. Use four single jumpers and follow the
      labels printed on the module: GND -> GND, VCC -> 3V3, SCL (SCK) -> GP5
      (pin 7), SDA -> GP4 (pin 6). The order of VCC and GND differs between
      modules, and neither matches the Gravity I2C0 header (SDA SCL GND
      3V3), so never plug a 4-way connector straight onto it.
    * 4.7 kOhm from SDA (GP4) to 3V3 and 4.7 kOhm from SCL (GP5) to 3V3:
      the module's own pull-ups are too weak for 400 kHz. Keep the I2C
      jumpers short (about 20 cm or less).
    * All grounds common: the breadboard GND rail must beep to a black GND
      pin on the Gravity board. After any change to the 5 V or ground
      wiring, with USB unplugged, the 5 V rail must not beep steadily to
      GND, 3V3 or any GPIO row. USB power only, from a computer port or a
      charger of at least 1 A.

After "wired OK", deploy ``src/`` (``python tools/deploy.py``; ``--dry-run``
first to preview what is copied), then run one mode:
    mpremote connect auto run tests/bench/bench_display.py
    mpremote connect auto exec "MODE='timing'" + run tests/bench/bench_display.py
    mpremote connect auto exec "MODE='scan'; CONTROLLER='ssd1306'" + run tests/bench/bench_display.py

Modes (global ``MODE``, default ``"scan"``; optional global ``CONTROLLER``,
default ``settings.OLED_CONTROLLER``):
    scan:    I2C scan, then a test pattern for 60 s: a 1-pixel border round
             the whole screen, a cross in each corner and three lines of
             text. The tester checks it is clean: all four border lines
             visible, nothing cut off. Garbage, or a border shifted by
             2 columns, means the other controller ('ssd1306').
             CSV ``freq_hz,addresses,controller,ok``.
    timing:  at 100 kHz and at 400 kHz, 20 blocking full frames
             (``show_now``) and 20 paged flushes. Two screens that differ
             on every page alternate, so every flush sends all 8 pages. A
             second task measures the longest time the loop was held up
             during a flush (the per-page block). Also timed: a flush of an
             unchanged frame (only the page compares) and ``render()``.
             CSV ``freq_hz,kind,n,mean_us,max_us,page_max_us``.
    layout:  the real layout with sample values, for 60 s. Take the photo
             ``evidence/display/<date>-layout.jpg`` then.
    fault:   the display loop (draw, then flush every DISPLAY_PERIOD_MS)
             for 120 s, with a 10 ms watchdog task. At the printed prompt
             pull the OLED's SDA jumper at the module (only that wire; park
             its free end in an empty breadboard row, away from 5 V and any
             VCC pin) and later plug it back. The program must keep running;
             the picture returns within DISPLAY_RETRY_MS after re-plugging.
             CSV ``t_ms,ok,event``, one row per change of ``ok``.

Output: one CSV header, the rows, then a ``# summary:`` line.
"""

import asyncio
import time

from machine import I2C, Pin

import board
import settings
from display import Display

MODE = globals().get("MODE", "scan")
CONTROLLER = globals().get("CONTROLLER", settings.OLED_CONTROLLER)
FREQS_HZ = (100_000, 400_000)
N_FRAMES = 20
FAULT_RUN_S = 120
PULL_AT_S = 20       # prompt to pull SDA
REPLUG_AT_S = 60     # prompt to plug it back
REACTION_MS = 10_000 # time allowed from the prompt to the actual re-plug


class View:
    """Sample values with the shared state's attributes."""

    def __init__(self, mode="RUN", d1=0.42, d2=1.30, wifi="up", faults=(),
                 last_event="ENTER", queued=3, synced=True, cloud="ok"):
        self.mode = mode
        self.d1 = d1
        self.d2 = d2
        ds = [v for v in (d1, d2) if v is not None]
        self.d = min(ds) if ds else None
        self.zone = "medium"
        self.wifi = wifi
        self.faults = set(faults)
        self.last_event = last_event
        self.events = [0] * queued
        self.synced = synced
        self.cloud = cloud


# Two screens that differ on every page, so every flush sends all 8 pages.
VIEW_A = View()
VIEW_B = View(mode="OFF", d1=1.11, d2=0.27, wifi="connecting",
              faults=("S1", "S2"), last_event="LEAVE", queued=17,
              synced=False, cloud="rej")


def make_display(freq):
    """Create the bus and the display.

    Args:
        freq (int): I2C clock in Hz.

    Returns:
        tuple: ``(display, i2c)``; ``display.ok`` is False if it didn't
        answer.
    """
    i2c = I2C(board.I2C_ID, sda=Pin(board.PIN_SDA), scl=Pin(board.PIN_SCL),
              freq=freq)
    return Display(i2c, controller=CONTROLLER), i2c


def countdown(n):
    """Print a countdown of ``n`` seconds."""
    for i in range(n, 0, -1):
        print("# starting in", i)
        time.sleep(1)


def run_scan():
    """I2C scan, then a test pattern for the tester to check."""
    freq = settings.I2C_FREQ_HZ
    disp, i2c = make_display(freq)
    found = i2c.scan()
    print("freq_hz,addresses,controller,ok")
    print("{},{},{},{}".format(freq, " ".join(hex(a) for a in found),
                               CONTROLLER, int(disp.ok)))
    fb = disp._fb           # bench only: draw a raw test pattern
    fb.fill(0)
    fb.rect(0, 0, 128, 64, 1)
    for x, y in ((2, 2), (121, 2), (2, 57), (121, 57)):
        fb.line(x, y, x + 4, y + 4, 1)
        fb.line(x + 4, y, x, y + 4, 1)
    fb.text(CONTROLLER, 12, 12, 1)
    fb.text("0123456789ABCDE", 4, 28, 1)
    fb.text("border all round", 0, 44, 1)
    disp.show_now()
    print("# check the screen now: 4 border lines, 4 corner crosses, 3 text lines")
    time.sleep(60)
    print("# summary: addresses={} oled_found={} ok={}".format(
        len(found), int(board.ADDR_OLED in found), int(disp.ok)))


async def watch(stats):
    """Record the longest gap between two runs of this task (us)."""
    last = time.ticks_us()
    while True:
        await asyncio.sleep_ms(0)
        now = time.ticks_us()
        gap = time.ticks_diff(now, last)
        if stats["on"] and gap > stats["max"]:
            stats["max"] = gap
        last = now


async def time_paged(disp, same=False):
    """Time ``N_FRAMES`` paged flushes.

    Args:
        disp (Display): the display.
        same (bool): False: alternate two screens, so all 8 pages are sent;
            True: render the same screen each time, so nothing is sent and
            only the page compares run.

    Returns:
        tuple: ``(mean_us, max_us, page_max_us)``.
    """
    stats = {"on": False, "max": 0}
    w = asyncio.create_task(watch(stats))
    await asyncio.sleep_ms(0)
    total = 0
    worst = 0
    if same:
        disp.render(VIEW_A)
        await disp.flush()
    for i in range(N_FRAMES):
        disp.render(VIEW_A if (same or i % 2) else VIEW_B)
        stats["on"] = True
        t0 = time.ticks_us()
        await disp.flush()
        dt = time.ticks_diff(time.ticks_us(), t0)
        stats["on"] = False
        total += dt
        worst = max(worst, dt)
        await asyncio.sleep_ms(20)
    w.cancel()
    await asyncio.sleep_ms(0)    # let the cancelled watcher finish in this run
    return total // N_FRAMES, worst, stats["max"]


def run_timing():
    """Measure blocking and paged frame times at 100 and 400 kHz."""
    print("freq_hz,kind,n,mean_us,max_us,page_max_us")
    results = []
    for freq in FREQS_HZ:
        disp, _ = make_display(freq)
        if not disp.ok:
            print("# no display at {} Hz".format(freq))
            continue
        total = 0
        worst = 0
        for i in range(N_FRAMES):
            disp.render(VIEW_A if i % 2 else VIEW_B)
            t0 = time.ticks_us()
            disp.show_now()
            dt = time.ticks_diff(time.ticks_us(), t0)
            total += dt
            worst = max(worst, dt)
        print("{},show_now,{},{},{},".format(freq, N_FRAMES,
                                              total // N_FRAMES, worst))
        mean, mx, page_max = asyncio.run(time_paged(disp))
        print("{},flush,{},{},{},{}".format(freq, N_FRAMES, mean, mx, page_max))
        results.append(("page_max_us@{}".format(freq), page_max))
        mean, mx, page_max = asyncio.run(time_paged(disp, same=True))
        print("{},flush_unchanged,{},{},{},{}".format(freq, N_FRAMES, mean, mx,
                                                      page_max))
        results.append(("unchanged_page_max_us@{}".format(freq), page_max))
        total = 0
        worst = 0
        for i in range(N_FRAMES):
            t0 = time.ticks_us()
            disp.render(VIEW_A if i % 2 else VIEW_B)
            dt = time.ticks_diff(time.ticks_us(), t0)
            total += dt
            worst = max(worst, dt)
        print("{},render,{},{},{},".format(freq, N_FRAMES, total // N_FRAMES,
                                            worst))
        if not disp.ok:
            print("# display failed during the run at {} Hz".format(freq))
    print("# summary: " + " ".join(
        "{}={}".format(name, p) for name, p in results))


def run_layout():
    """Show the real layout with sample values for the photo."""
    disp, _ = make_display(settings.I2C_FREQ_HZ)
    disp.render(VIEW_A)
    disp.show_now()
    print("# layout on screen for 60 s: take evidence/display/<date>-layout.jpg")
    time.sleep(60)
    print("# summary: ok={}".format(int(disp.ok)))


async def fault_main():
    """Run the display loop while the tester pulls and re-plugs SDA."""
    disp, _ = make_display(settings.I2C_FREQ_HZ)
    t0 = time.ticks_ms()
    lateness = {"max": 0}

    async def watchdog():
        nxt = time.ticks_add(time.ticks_ms(), 10)
        while True:
            await asyncio.sleep_ms(max(0, time.ticks_diff(nxt, time.ticks_ms())))
            late = time.ticks_diff(time.ticks_ms(), nxt)
            if late > lateness["max"]:
                lateness["max"] = late
            nxt = time.ticks_add(nxt, 10)
            if time.ticks_diff(nxt, time.ticks_ms()) < 0:
                nxt = time.ticks_add(time.ticks_ms(), 10)

    wd = asyncio.create_task(watchdog())
    print("t_ms,ok,event")
    print("0,{},start".format(int(disp.ok)))
    was_ok = disp.ok
    lost_at = None
    back_after = []
    prompted = 0
    n = 0
    while True:
        t = time.ticks_diff(time.ticks_ms(), t0)
        if t >= FAULT_RUN_S * 1000:
            break
        if prompted == 0 and t >= PULL_AT_S * 1000:
            print("# PULL only the OLED's SDA jumper at the module now; park "
                  "its free end in an empty row, away from 5 V and VCC")
            prompted = 1
        elif prompted == 1 and t >= REPLUG_AT_S * 1000:
            print("# PLUG the SDA jumper back now")
            prompted = 2
        n += 1
        disp.render(View(d1=(n % 200) / 100, queued=n % 50))
        await disp.flush()
        if disp.ok != was_ok:
            was_ok = disp.ok
            print("{},{},{}".format(t, int(disp.ok), "back" if disp.ok else "lost"))
            if disp.ok:
                if lost_at is not None:
                    back_after.append(t)
            else:
                lost_at = t
        await asyncio.sleep_ms(settings.DISPLAY_PERIOD_MS)
    wd.cancel()
    replug_ms = REPLUG_AT_S * 1000
    late_back = [b - replug_ms for b in back_after if b >= replug_ms]
    # The re-plug happens a few seconds after its prompt, so allow for that.
    within = int(bool(late_back) and late_back[0] <= settings.DISPLAY_RETRY_MS
                 + settings.DISPLAY_PERIOD_MS + REACTION_MS)
    print("# summary: frames={} lost={} back={} back_after_replug_prompt_ms={} "
          "back_within_retry={} max_loop_lateness_ms={} retry_ms={} "
          "ok_end={}".format(
              n, int(lost_at is not None), len(back_after),
              late_back[0] if late_back else "-", within, lateness["max"],
              settings.DISPLAY_RETRY_MS, int(disp.ok)))


def main():
    """Run the selected mode."""
    countdown(3)
    if MODE == "scan":
        run_scan()
    elif MODE == "timing":
        run_timing()
    elif MODE == "layout":
        run_layout()
    elif MODE == "fault":
        asyncio.run(fault_main())
    else:
        raise ValueError("MODE must be 'scan', 'timing', 'layout' or 'fault'")


main()
