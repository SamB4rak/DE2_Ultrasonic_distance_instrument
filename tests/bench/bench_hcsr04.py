"""Bench test for the two HC-SR04 sensors (run on the Pico).

Wiring (pin by pin in docs/img/ultrasonic_wiring_diagram.svg). Wire with
USB unplugged; check it before plugging in, and run nothing until the
tester confirms it with the 5 V rail and both divider readings, e.g.
"5.02/3.33/3.32 V, wired OK".
    * Power: both sensors' VCC on the VBUS 5 V rail (Pico pin 40, centre
      header), never a 3V3 pin; all grounds common; USB power only, from a
      port or charger of >= 1 A. 100 nF across each sensor's VCC and GND
      (plus 10 uF if you have it, rated >= 10 V, stripe/- leg on GND).
    * Beeper check from the 5 V rail before plugging in: no steady beep to
      GND, 3V3 or any GPIO row; the breadboard GND rail beeps to a Pico GND.
    * Trig: Trig #1 -> GP20 (pin 26), Trig #2 -> GP18 (pin 24), and 10 kOhm
      from each Trig line to GND.
    * Echo through a divider, one per sensor: Echo -> 1 kOhm -> node,
      node -> 2 kOhm -> GND, node -> GP21 for #1, GP19 for #2. Check every
      divider before its node touches a GPIO:
        1. Build the divider without the Echo wire and the node -> GPIO wire.
        2. USB unplugged: jumper the divider's top to the 5 V rail, beeper
           check, plug USB in.
        3. Measure the rail V5 and the node Vn to GND: V5 must be
           4.75-5.25 V and Vn/V5 0.64-0.69 (3.20-3.45 V at 5.00 V).
           Vn ~ V5: the 2 kOhm is open; Vn ~ V5/3: the resistors are
           swapped; Vn ~ 0: a short to GND or the 1 kOhm is open.
        4. Unplug USB, remove the jumper, fit the Echo wire to the divider's
           top and the node -> GPIO wire. Beeper from the HC-SR04's Echo pin
           to that GPIO row's D pin: it must NOT beep (about 1 kOhm; a beep
           means Echo landed in the node row). Redo the 5 V beeper check.

After "wired OK", deploy ``src/`` (``python tools/deploy.py``), then run one mode:
    mpremote connect auto exec "MODE='accuracy'; TRUE_M=0.30" + run tests/bench/bench_hcsr04.py
    mpremote connect auto exec "MODE='notarget'" + run tests/bench/bench_hcsr04.py
    mpremote connect auto exec "MODE='crosstalk'" + run tests/bench/bench_hcsr04.py
    mpremote connect auto exec "MODE='fault'" + run tests/bench/bench_hcsr04.py

Modes (global ``MODE``):
    accuracy:  a flat target at ``TRUE_M`` metres (global, required) in front
               of both sensors; 20 pings of #1, then 20 of #2, one ping per
               60 ms (never both at once). CSV ``sensor,true_m,echo_us,d_m``;
               the summary gives the mean error and standard deviation per
               sensor. Run it at 0.10, 0.30, 0.50, 1.00 and 2.00 m.
    notarget:  point both sensors at open space. Measures the Echo width
               with no target (10 pulses per sensor, ``machine.time_pulse_us``,
               allowed only here in the bench), then runs the driver at the
               real cadence for 100 slots and reports the longest BUSY run.
    crosstalk: both sensors face a wall at about 1 m; alternate #1 and #2
               every 60 ms for 200 slots and report outliers.
    fault:     alternate #1 and #2 every 60 ms for about 40 s. Pull the Echo
               jumper of #2 at the sensor when told, re-plug it when told
               (never pull VCC or GND; park the free end in an empty row,
               away from the 5 V rail). Shows NO_RISE -> faulty -> OK again.

Output: one CSV header, rows, ``#`` comment lines, then ``# summary:`` lines.
"""

import asyncio
import time

import machine

import board
import logic
import settings
from hcsr04 import HCSR04

MODE = globals().get("MODE", "accuracy")
TRUE_M = globals().get("TRUE_M")
N_PINGS = 20               # accuracy: pings per sensor
NO_TARGET_PULSES = 10      # notarget: pulses measured per sensor
SLOTS = 200                # crosstalk: slots (12 s)
FAULT_S = 40               # fault: run time in seconds
STATUS = ("OK", "NO_TARGET", "NO_RISE", "BUSY")


def countdown(n, what="starting"):
    """Print a countdown of ``n`` seconds."""
    for i in range(n, 0, -1):
        print("# {} in {}".format(what, i))
        time.sleep(1)


def mean_std(values):
    """Return (mean, standard deviation) of a list, or (0, 0) if empty."""
    n = len(values)
    if n == 0:
        return 0.0, 0.0
    m = sum(values) / n
    if n == 1:
        return m, 0.0
    var = sum((v - m) * (v - m) for v in values) / (n - 1)
    return m, var ** 0.5


def median(values):
    """Return the median of a non-empty list."""
    v = sorted(values)
    n = len(v)
    if n % 2:
        return v[n // 2]
    return (v[n // 2 - 1] + v[n // 2]) / 2


def d_m(echo_us):
    """Echo time in µs -> distance in m (c = settings.C_DEFAULT_MPS)."""
    return logic.echo_to_m(echo_us, settings.C_DEFAULT_MPS)


async def slot_loop(sensors, n_slots, on_result):
    """Ping the sensors in turn, one per ``RANGING_SLOT_MS`` slot.

    Args:
        sensors (list): the HCSR04 instances, pinged in this order.
        n_slots (int): number of slots.
        on_result (callable): ``on_result(slot, index, status, echo_us)``.
    """
    nxt = time.ticks_ms()
    for slot in range(n_slots):
        i = slot % len(sensors)
        status, echo_us = await sensors[i].ping()
        on_result(slot, i, status, echo_us)
        nxt = time.ticks_add(nxt, settings.RANGING_SLOT_MS)
        wait = time.ticks_diff(nxt, time.ticks_ms())
        if wait > 0:
            await asyncio.sleep_ms(wait)


def run_accuracy(sensors):
    """Accuracy at one distance (``TRUE_M``) for both sensors."""
    if TRUE_M is None:
        print("# summary: ERROR set TRUE_M first, e.g. exec \"MODE='accuracy'; TRUE_M=0.30\"")
        return
    true_m = float(TRUE_M)
    print("# accuracy true_m={} c={} pings={}".format(true_m, settings.C_DEFAULT_MPS, N_PINGS))
    countdown(3)
    print("sensor,true_m,echo_us,d_m")
    errors = ([], [])
    other = [0, 0]

    def on_result(slot, i, status, echo_us):
        if status == HCSR04.OK:
            d = d_m(echo_us)
            errors[i].append(d - true_m)
            print("{},{},{},{:.4f}".format(i + 1, true_m, echo_us, d))
        else:
            other[i] += 1
            print("# sensor {} {}".format(i + 1, STATUS[status]))

    for i in range(2):
        asyncio.run(slot_loop([sensors[i]], N_PINGS, on_result))
    for i in range(2):
        m, sd = mean_std(errors[i])
        print("# summary: sensor={} true_m={} n_ok={} n_other={} mean_err_m={:.4f} std_m={:.4f}".format(
            i + 1, true_m, len(errors[i]), other[i], m, sd))


def run_notarget(sensors):
    """No-target Echo width, then the longest BUSY run at the real cadence."""
    print("# notarget: point both sensors at open space")
    countdown(3)
    for s in sensors:
        s.deinit()                       # the raw measurement needs the pins alone
    pins = ((board.PIN_TRIG_1, board.PIN_ECHO_1), (board.PIN_TRIG_2, board.PIN_ECHO_2))
    print("sensor,pulse,width_us")
    widest = [0, 0]
    too_long = [0, 0]                    # time_pulse_us -1: still HIGH after 1 s
    no_rise = [0, 0]                     # time_pulse_us -2: never went HIGH
    stuck = [0, 0]                       # Echo still HIGH 2 s after the last pulse
    for i, (tp, ep) in enumerate(pins):
        trig = machine.Pin(tp, machine.Pin.OUT, value=0)
        echo = machine.Pin(ep, machine.Pin.IN)
        for k in range(NO_TARGET_PULSES):
            waited = 0
            while echo.value() and waited < 2000:    # let an old echo end (max 2 s)
                time.sleep_ms(1)
                waited += 1
            if echo.value():
                print("# sensor {} Echo stuck HIGH".format(i + 1))
                stuck[i] += 1
                continue
            trig.value(1)
            time.sleep_us(10)
            trig.value(0)
            w = machine.time_pulse_us(echo, 1, 1_000_000)
            print("{},{},{}".format(i + 1, k + 1, w))
            if w == -1:
                too_long[i] += 1
            elif w < 0:
                no_rise[i] += 1
            elif w > widest[i]:
                widest[i] = w
            time.sleep_ms(100)
        trig.value(0)
    # Fresh driver instances (the raw Pins above replaced their IRQs).
    drv = [HCSR04(board.PIN_TRIG_1, board.PIN_ECHO_1, "1"),
           HCSR04(board.PIN_TRIG_2, board.PIN_ECHO_2, "2")]
    runs = [0, 0]
    longest = [0, 0]
    counts = [[0, 0, 0, 0], [0, 0, 0, 0]]

    def on_result(slot, i, status, echo_us):
        counts[i][status] += 1
        if status == HCSR04.BUSY:
            runs[i] += 1
            if runs[i] > longest[i]:
                longest[i] = runs[i]
        else:
            runs[i] = 0

    try:
        asyncio.run(slot_loop(drv, 100, on_result))
    finally:
        for s in drv:
            s.deinit()
    limit_us = settings.FAULT_AFTER * 2 * settings.RANGING_SLOT_MS * 1000
    for i in range(2):
        w = widest[i]
        if stuck[i]:
            verdict = "ECHO STUCK HIGH (check wiring or sensor)"
        elif too_long[i] or w >= limit_us:
            verdict = "RAISE FAULT_AFTER"
        elif w == 0:
            verdict = "NO PULSE MEASURED (check wiring)"
        elif no_rise[i]:
            verdict = "SOME NO_RISE (check wiring)"
        else:
            verdict = "OK"
        print("# summary: sensor={} max_width_us={} over_1s={} no_rise={} stuck={} limit_us={} "
              "longest_busy_run={} ok/no_target/no_rise/busy={} fault_after={} -> {}".format(
                  i + 1, w, too_long[i], no_rise[i], stuck[i], limit_us, longest[i], counts[i],
                  settings.FAULT_AFTER, verdict))


def run_crosstalk(sensors):
    """Alternate the sensors every slot at a wall; report outliers."""
    print("# crosstalk: both sensors face a wall at about 1 m; slots={}".format(SLOTS))
    countdown(3)
    print("slot,sensor,status,echo_us,d_m")
    ds = ([], [])
    counts = [[0, 0, 0, 0], [0, 0, 0, 0]]

    def on_result(slot, i, status, echo_us):
        counts[i][status] += 1
        d = d_m(echo_us) if status == HCSR04.OK else 0.0
        if status == HCSR04.OK:
            ds[i].append(d)
        print("{},{},{},{},{:.4f}".format(slot, i + 1, STATUS[status], echo_us, d))

    asyncio.run(slot_loop(sensors, SLOTS, on_result))
    for i in range(2):
        if ds[i]:
            med = median(ds[i])
            out = [d for d in ds[i] if abs(d - med) > 0.05]
        else:
            med, out = 0.0, []
        print("# summary: sensor={} n_ok={} median_m={:.4f} outliers_gt_5cm={} "
              "ok/no_target/no_rise/busy={}".format(i + 1, len(ds[i]), med, len(out), counts[i]))


def run_fault(sensors):
    """Pull and re-plug the Echo jumper of #2; watch NO_RISE -> faulty -> OK."""
    n_slots = FAULT_S * 1000 // settings.RANGING_SLOT_MS
    pull_slot = 5000 // settings.RANGING_SLOT_MS       # about 5 s in
    plug_slot = 20000 // settings.RANGING_SLOT_MS      # about 20 s in
    print("# fault: pull the Echo jumper of #2 AT THE SENSOR when told; never VCC or GND")
    countdown(3)
    print("slot,sensor,status,echo_us,faulty")
    st = {"first_no_rise": None, "faulty_at": None, "recovered_at": None, "pings2": 0}

    def on_result(slot, i, status, echo_us):
        s = sensors[i]
        if slot == pull_slot:
            print("# >>> PULL the Echo jumper of #2 at the sensor NOW")
        if slot == plug_slot:
            print("# >>> RE-PLUG the Echo jumper of #2 NOW")
        print("{},{},{},{},{}".format(slot, i + 1, STATUS[status], echo_us, int(s.faulty)))
        if i != 1:
            return
        st["pings2"] += 1
        if status == HCSR04.NO_RISE and st["first_no_rise"] is None:
            st["first_no_rise"] = st["pings2"]
        if s.faulty and st["faulty_at"] is None:
            st["faulty_at"] = st["pings2"]
        if st["faulty_at"] is not None and st["recovered_at"] is None and status == HCSR04.OK:
            st["recovered_at"] = st["pings2"]

    asyncio.run(slot_loop(sensors, n_slots, on_result))
    after = None
    if st["first_no_rise"] is not None and st["faulty_at"] is not None:
        after = st["faulty_at"] - st["first_no_rise"] + 1
    print("# summary: sensor 2 first_no_rise_ping={} faulty_at_ping={} pings_to_faulty={} "
          "recovered_ok_ping={} sensor1_faulty_end={} fault_after={}".format(
              st["first_no_rise"], st["faulty_at"], after, st["recovered_at"],
              sensors[0].faulty, settings.FAULT_AFTER))


def main():
    """Run the selected mode."""
    sensors = [HCSR04(board.PIN_TRIG_1, board.PIN_ECHO_1, "1"),
               HCSR04(board.PIN_TRIG_2, board.PIN_ECHO_2, "2")]
    try:
        print("# mode={} wait_ms={} timeout_us={} fault_after={} slot_ms={}".format(
            MODE, settings.ECHO_WAIT_MS, settings.ECHO_TIMEOUT_US,
            settings.FAULT_AFTER, settings.RANGING_SLOT_MS))
        if MODE == "accuracy":
            run_accuracy(sensors)
        elif MODE == "notarget":
            run_notarget(sensors)
        elif MODE == "crosstalk":
            run_crosstalk(sensors)
        elif MODE == "fault":
            run_fault(sensors)
        else:
            print("# summary: ERROR unknown MODE {!r}".format(MODE))
    finally:
        for s in sensors:
            s.deinit()


main()
