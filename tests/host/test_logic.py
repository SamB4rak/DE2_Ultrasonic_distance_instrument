"""Host tests for src/logic.py (pure application logic and the event detector)."""

import time

import pytest

import settings

import logic

SLOT = 60
SAMPLE, ZONE, ENTER, LEAVE, MODE = range(5)
CLOSE_FIELD, DUR_FIELD = 7, 8
MAX_WAIT = settings.LEAVE_HOLD_MS // SLOT + 2   # calls to wait for a LEAVE


def approx(x):
    """Tolerance for metres (the Pico computes in single precision)."""
    return pytest.approx(x, abs=1e-4)


# ---------------------------------------------------------------- constants

def test_constants_have_the_expected_values():
    assert logic.ZONES == ("far", "medium", "close", "clear")
    assert logic.ZONE_CODES == {"clear": 0, "far": 1, "medium": 2, "close": 3}
    assert logic.EVENT_NAMES == ("SAMPLE", "ZONE", "ENTER", "LEAVE", "MODE")


# ------------------------------------------------------------ small functions

def test_echo_to_m():
    assert logic.echo_to_m(5831, 343.4) == pytest.approx(1.001, abs=1e-3)
    assert logic.echo_to_m(0, 343.4) == approx(0.0)
    assert logic.echo_to_m(5831, settings.C_DEFAULT_MPS) == approx(343.4 * 5831 / 2e6)


@pytest.mark.parametrize("d, want", [
    (0.01, 0.02), (0.0, 0.02), (0.02, 0.02), (1.5, 1.5), (4.0, 4.0),
])
def test_clamp_distance_values(d, want):
    assert logic.clamp_distance(d) == approx(want)


@pytest.mark.parametrize("d", [4.01, 5.0, None])
def test_clamp_distance_none(d):
    assert logic.clamp_distance(d) is None


@pytest.mark.parametrize("d1, d2, want", [
    (None, 1.2, 1.2), (1.2, None, 1.2), (0.5, 1.2, 0.5), (1.2, 0.5, 0.5),
    (0.7, 0.7, 0.7),
])
def test_nearest(d1, d2, want):
    assert logic.nearest(d1, d2) == approx(want)


def test_nearest_both_none():
    assert logic.nearest(None, None) is None


@pytest.mark.parametrize("d, want", [
    (2.0, 1.0), (1.0, 1.0), (0.55, 3.0), (0.1, 5.0), (0.05, 5.0), (0.02, 5.0),
])
def test_tick_hz(d, want):
    assert logic.tick_hz(d) == pytest.approx(want, abs=1e-4)


def test_tick_hz_none():
    assert logic.tick_hz(None) is None


def test_format_m():
    assert logic.format_m(0.4249) == "0.42 m"
    assert logic.format_m(1.3) == "1.30 m"
    assert logic.format_m(0.02) == "0.02 m"
    assert logic.format_m(None) == "--"


@pytest.mark.parametrize("d, want", [
    (None, 0.0), (0.0, 0.0), (1.0, 0.5), (2.0, 1.0), (3.0, 1.0),
])
def test_bar_fraction(d, want):
    assert logic.bar_fraction(d) == pytest.approx(want, abs=1e-6)


# ------------------------------------------------------------------- Median3

def test_median3_one_two_three_values():
    m = logic.Median3()
    assert m.push(1.0) == approx(1.0)
    assert m.push(1.2) == approx(1.1)
    assert m.push(0.2) == approx(1.0)


def test_median3_keeps_last_three():
    m = logic.Median3()
    for x in (1.0, 1.2, 0.2):
        m.push(x)
    # history is now 1.2, 0.2, 0.3 -> median 0.3
    assert m.push(0.3) == approx(0.3)
    # 0.2, 0.3, 5.0 -> 0.3 (a spike is rejected)
    assert m.push(5.0) == approx(0.3)


def test_median3_single_none_keeps_last_output():
    m = logic.Median3()
    m.push(1.0)
    m.push(1.2)
    assert m.push(None) == approx(1.1)
    # history survives: 1.0, 1.2, 0.2 -> 1.0
    assert m.push(0.2) == approx(1.0)


def test_median3_two_nones_clear_history():
    m = logic.Median3()
    for x in (1.0, 1.2, 0.2):
        m.push(x)
    assert m.push(None) == approx(1.0)
    assert m.push(None) is None
    assert m.push(None) is None
    # fresh history: one value only
    assert m.push(2.0) == approx(2.0)


def test_median3_mixed_none_sequence():
    m = logic.Median3()
    outs = [m.push(x) for x in (1.0, None, 2.0, None, None, 3.0, None, 4.0)]
    assert outs[0] == approx(1.0)
    assert outs[1] == approx(1.0)          # one dropout
    assert outs[2] == approx(1.5)          # 1.0, 2.0
    assert outs[3] == approx(1.5)
    assert outs[4] is None                 # second None in a row
    assert outs[5] == approx(3.0)          # history was cleared
    assert outs[6] == approx(3.0)
    assert outs[7] == approx(3.5)          # 3.0, 4.0


def test_median3_none_on_empty_and_reset():
    m = logic.Median3()
    assert m.push(None) is None
    assert m.push(0.5) == approx(0.5)      # a number resets the counter
    assert m.push(None) == approx(0.5)
    m.reset()
    assert m.push(None) is None
    assert m.push(0.8) == approx(0.8)      # history was cleared by reset


# --------------------------------------------------------------- ZoneTracker

def tracker_in(zone, d):
    """Return a ZoneTracker that is in ``zone``, reached from clear with ``d``."""
    t = logic.ZoneTracker()
    assert t.update(d) == zone
    return t


def test_zone_starts_clear_and_classifies_from_clear():
    assert logic.ZoneTracker().zone == "clear"
    assert tracker_in("far", 2.0).zone == "far"
    assert tracker_in("medium", 0.5).zone == "medium"
    assert tracker_in("close", 0.1).zone == "close"


@pytest.mark.parametrize("start, centre", [(1.1, 1.0), (0.9, 1.0), (0.5, 0.3), (0.1, 0.3)])
def test_zone_no_flapping(start, centre):
    t = logic.ZoneTracker()
    first = t.update(start)
    # move to the boundary first, then oscillate +-0.02 m
    settled = t.update(centre)
    assert settled == first
    for _ in range(20):
        assert t.update(centre + 0.02) == first
        assert t.update(centre - 0.02) == first


def test_zone_far_jumps():
    assert tracker_in("far", 2.0).update(0.1) == "close"
    assert tracker_in("far", 2.0).update(0.28) == "medium"
    assert tracker_in("far", 2.0).update(0.98) == "far"


def test_zone_close_jumps():
    assert tracker_in("close", 0.1).update(1.02) == "medium"
    assert tracker_in("close", 0.1).update(1.05) == "far"
    assert tracker_in("close", 0.1).update(0.32) == "close"


def test_zone_medium_jumps():
    assert tracker_in("medium", 0.5).update(1.05) == "far"
    assert tracker_in("medium", 0.5).update(0.25) == "close"
    assert tracker_in("medium", 0.5).update(1.02) == "medium"
    assert tracker_in("medium", 0.5).update(0.28) == "medium"


def test_zone_none_and_reset():
    t = tracker_in("close", 0.1)
    assert t.update(None) == "clear"
    assert t.zone == "clear"
    t.update(0.5)
    t.reset()
    assert t.zone == "clear"


def sweep(start_cm, stop_cm, step):
    """Distances in metres from start_cm to stop_cm (inclusive), 1 cm steps."""
    return [round(c / 100, 2) for c in range(start_cm, stop_cm + step, step)]


def dedupe(seq):
    """Collapse runs of equal items."""
    out = []
    for x in seq:
        if not out or out[-1] != x:
            out.append(x)
    return out


def test_zone_slow_approach_sweep_is_monotonic():
    t = logic.ZoneTracker()
    zones = [t.update(logic.clamp_distance(d)) for d in sweep(420, 0, -1)]
    assert dedupe(zones) == ["clear", "far", "medium", "close"]


def test_zone_slow_retreat_sweep_is_monotonic():
    t = logic.ZoneTracker()
    zones = [t.update(logic.clamp_distance(d)) for d in sweep(0, 420, 1)]
    assert dedupe(zones) == ["close", "medium", "far", "clear"]


def test_tick_hz_sweep_range_and_monotonic():
    prev = None
    for d in sweep(420, 0, -1):
        f = logic.tick_hz(logic.clamp_distance(d))
        if f is None:
            assert d > settings.D_MAX_M
            continue
        assert settings.TICK_HZ_MIN - 1e-6 <= f <= settings.TICK_HZ_MAX + 1e-6
        if prev is not None:
            assert f >= prev - 1e-9
        prev = f
    assert prev == pytest.approx(settings.TICK_HZ_MAX)


# ------------------------------------------------------------- EventDetector

class Walk:
    """Drive an EventDetector with one call per 60 ms slot."""

    def __init__(self, start=0):
        self.det = logic.EventDetector()
        self.now = start
        self.tracker = logic.ZoneTracker()
        self.events = []
        self.calls = 0

    def step(self, d, mode="RUN", d1="same", d2=None, zone=None):
        """Make one call; d1 defaults to d, zone to the tracker's zone."""
        if self.calls:
            self.now = time.ticks_add(self.now, SLOT)
        self.calls += 1
        if d1 == "same":
            d1 = d
        if zone is None:
            if mode == "OFF":
                self.tracker.reset()
                zone = "clear"
            else:
                zone = self.tracker.update(d)
        out = self.det.update(self.now, mode, d, d1, d2, zone)
        assert_sorted(out, self.now)
        for e in out:
            assert len(e) == 9
            if e[1] != LEAVE:
                assert e[CLOSE_FIELD] is None and e[DUR_FIELD] is None
        self.events.extend(out)
        return out

    def hold(self, d, ms, mode="RUN"):
        """Repeat d for ``ms`` of slots."""
        out = []
        for _ in range(ms // SLOT):
            out.extend(self.step(d, mode))
        return out

    def kinds(self):
        return [e[1] for e in self.events]


def assert_sorted(events, now):
    keys = [(time.ticks_diff(e[0], now), e[1]) for e in events]
    assert keys == sorted(keys)
    for k in keys:
        assert k[0] <= 0


def only(events, kind):
    return [e for e in events if e[1] == kind]


def test_first_call_gives_no_events():
    det = logic.EventDetector()
    assert det.update(0, "RUN", 0.5, 0.5, None, "medium") == []
    det = logic.EventDetector()
    assert det.update(0, "OFF", None, None, None, "clear") == []


def test_walk_in_one_step_and_back():
    w = Walk()
    w.hold(2.0, 600)                               # far
    out = w.step(0.5, d2=0.7)                      # first call inside
    t_in = w.now
    assert [e[1] for e in out] == [ZONE]
    assert out[0][6] == "medium" and out[0][0] == t_in
    out = w.step(0.5, d2=0.7)                      # confirm call
    assert [e[1] for e in out] == [ENTER]
    enter = out[0]
    assert enter[0] == t_in                        # same stamp as the ZONE
    assert enter[2:7] == ("RUN", approx(0.5), approx(0.5), approx(0.7), "medium")
    w.hold(0.5, 1200)
    w.step(0.45, d2=0.6)                           # closest
    w.hold(0.5, 600)
    out = w.step(2.0, d2=2.1)                      # first call outside
    t_out = w.now
    assert [e[1] for e in out] == [ZONE]
    assert out[0][6] == "far" and out[0][0] == t_out
    # LEAVE comes LEAVE_HOLD_MS after the first call outside
    got = []
    for _ in range(MAX_WAIT):
        got = only(w.step(2.0, d2=2.1), LEAVE)
        if got:
            break
    assert got
    leave = got[0]
    assert time.ticks_diff(w.now, t_out) == settings.LEAVE_HOLD_MS
    assert leave[0] == t_out
    assert leave[2:7] == ("RUN", approx(2.0), approx(2.0), approx(2.1), "far")
    assert leave[CLOSE_FIELD] == approx(0.45)
    assert leave[DUR_FIELD] == 1                   # 2580 - 600 ms
    assert isinstance(leave[DUR_FIELD], int)
    assert w.kinds() == [ZONE, ENTER, ZONE, LEAVE]


def test_slow_approach_enter_before_zone():
    w = Walk()
    for d in sweep(120, 90, -1):
        w.step(d)
    enters = only(w.events, ENTER)
    zones = only(w.events, ZONE)
    assert len(enters) == 1 and len(zones) == 1
    assert enters[0][3] == approx(0.99)            # the first call below 1.0 m
    assert zones[0][6] == "medium"
    assert 0.955 < zones[0][3] < 0.975             # below FAR - HYST
    assert time.ticks_diff(zones[0][0], enters[0][0]) > 0
    assert w.kinds().index(ENTER) < w.kinds().index(ZONE)


def test_single_call_inside_gives_no_enter():
    w = Walk()
    w.hold(2.0, 300)
    w.step(0.5)
    w.hold(2.0, 300)
    w.step(0.5)
    w.step(None)
    w.hold(2.0, 300)
    assert only(w.events, ENTER) == []


def test_enter_counts_calls_not_fresh_readings():
    # d keeps its value through the other sensor's slot: two equal calls confirm
    w = Walk()
    w.hold(2.0, 300)
    w.step(0.8)
    out = w.step(0.8)
    assert only(out, ENTER)


def test_gap_shorter_than_hold_gives_no_leave():
    w = Walk()
    w.hold(2.0, 300)
    w.hold(0.6, 300)
    assert len(only(w.events, ENTER)) == 1
    w.hold(None, settings.LEAVE_HOLD_MS - 2 * SLOT)   # gone, but not long enough
    w.hold(0.4, 300)                                   # back: visit continues
    w.hold(1.5, settings.LEAVE_HOLD_MS - SLOT)
    assert only(w.events, LEAVE) == []
    w.hold(1.5, 2 * SLOT)
    leaves = only(w.events, LEAVE)
    assert len(leaves) == 1
    assert leaves[0][CLOSE_FIELD] == approx(0.4)
    assert len(only(w.events, ENTER)) == 1


def test_none_counts_as_outside():
    w = Walk()
    w.hold(0.5, 300)
    w.hold(None, settings.LEAVE_HOLD_MS + SLOT)
    leave = only(w.events, LEAVE)[0]
    assert leave[3] is None and leave[6] == "clear"


def test_new_visit_after_leave():
    w = Walk()
    for _ in range(2):
        w.hold(0.5, 600)
        w.hold(2.0, settings.LEAVE_HOLD_MS + SLOT)
    assert [k for k in w.kinds() if k in (ENTER, LEAVE)] == [ENTER, LEAVE, ENTER, LEAVE]


def test_samples_every_period_while_run_over_five_minutes():
    w = Walk()
    period_ms = settings.SAMPLE_PERIOD_S * 1000
    w.hold(1.5, 5 * period_ms + SLOT)
    samples = only(w.events, SAMPLE)
    assert len(samples) == 5
    assert samples[0][0] == period_ms
    for a, b in zip(samples, samples[1:]):
        assert time.ticks_diff(b[0], a[0]) == period_ms
    assert all(s[2] == "RUN" and s[3] == approx(1.5) for s in samples)


def test_no_samples_while_off_and_restart_after_run():
    w = Walk()
    period_ms = settings.SAMPLE_PERIOD_S * 1000
    w.hold(1.5, period_ms // 2)
    w.hold(None, 3 * period_ms, mode="OFF")
    assert only(w.events, SAMPLE) == []
    w.step(1.5)
    t_run = w.now
    w.hold(1.5, period_ms + SLOT)
    samples = only(w.events, SAMPLE)
    assert len(samples) == 1
    assert time.ticks_diff(samples[0][0], t_run) == period_ms


def test_off_during_visit_while_inside():
    w = Walk()
    w.hold(2.0, 300)
    w.step(0.6)
    t_in = w.now
    w.hold(0.5, 2400)
    out = w.step(None, mode="OFF", d1=None)
    assert [e[1] for e in out] == [LEAVE, MODE]
    leave, mode = out
    assert leave[0] == w.now == mode[0]
    assert leave[2:7] == ("OFF", None, None, None, "clear")
    assert leave[CLOSE_FIELD] == approx(0.5)
    assert leave[DUR_FIELD] == 2                   # 2760 - 300 ms
    assert mode[2] == "OFF"
    assert only(out, ZONE) == []                   # no ZONE while OFF


def test_off_during_visit_after_object_left():
    w = Walk()
    w.hold(0.5, 2400)
    t_in = only(w.events, ENTER)[0][0]
    w.step(1.8, d2=1.9)                            # first call outside
    t_out = w.now
    w.hold(1.8, 1200)                              # hold still running
    out = w.step(None, mode="OFF", d1=None)
    assert [e[1] for e in out] == [LEAVE, MODE]
    leave = out[0]
    assert leave[0] == t_out
    assert leave[2:7] == ("RUN", approx(1.8), approx(1.8), approx(1.9), "far")
    assert leave[DUR_FIELD] == 2                   # 2400 - 0 ms
    assert t_in == 0
    assert out[1][0] == w.now
    # no second LEAVE later
    w.hold(None, 6000, mode="OFF")
    assert len(only(w.events, LEAVE)) == 1


def test_off_without_visit_gives_only_mode():
    w = Walk()
    w.hold(2.0, 300)
    out = w.step(None, mode="OFF", d1=None)
    assert [e[1] for e in out] == [MODE]


def test_off_cancels_pending_enter_confirm():
    w = Walk()
    w.hold(2.0, 300)
    w.step(0.5)                                    # one call inside
    w.step(None, mode="OFF", d1=None)
    w.step(2.0)                                    # back to RUN, outside
    w.step(0.5)                                    # only one call inside again
    assert only(w.events, ENTER) == []


def test_off_to_run_with_object_in_range():
    w = Walk()
    w.hold(None, 600, mode="OFF")
    out = w.step(0.5, d2=0.6)
    t_run = w.now
    assert [e[1] for e in out] == [ZONE, MODE]
    assert out[0][0] == out[1][0] == t_run
    assert out[0][6] == "medium"
    assert out[1][2] == "RUN"
    out = w.step(0.5, d2=0.6)
    assert [e[1] for e in out] == [ENTER]
    assert out[0][0] == t_run


def test_off_to_run_with_nothing_in_range():
    w = Walk()
    w.hold(None, 300, mode="OFF")
    out = w.step(None, d1=None)
    assert [e[1] for e in out] == [MODE]


def test_events_carry_their_call_mode():
    w = Walk()
    w.hold(2.0, 300)
    w.hold(0.5, 300)
    w.step(None, mode="OFF", d1=None)
    w.hold(None, 300, mode="OFF")
    w.hold(0.5, 300)
    modes = [(logic.EVENT_NAMES[e[1]], e[2]) for e in w.events]
    assert modes == [
        ("ZONE", "RUN"), ("ENTER", "RUN"), ("LEAVE", "OFF"), ("MODE", "OFF"),
        ("ZONE", "RUN"), ("MODE", "RUN"), ("ENTER", "RUN"),
    ]


def test_zone_events_not_emitted_while_off_even_if_zone_passed():
    w = Walk()
    w.hold(None, 120, mode="OFF")
    w.step(None, mode="OFF", d1=None, zone="far")  # misuse: still no ZONE
    assert w.events == []


def test_ticks_wraparound():
    start = time.ticks_add(0, -1500)               # wraps 1.5 s into the walk
    w = Walk(start=start)
    w.hold(2.0, 600)
    w.hold(0.5, 1800)                              # crosses the wrap
    w.hold(2.0, settings.LEAVE_HOLD_MS + SLOT)
    enter = only(w.events, ENTER)[0]
    leave = only(w.events, LEAVE)[0]
    assert time.ticks_diff(enter[0], start) == 600
    assert enter[0] > leave[0]                     # raw values wrapped
    assert leave[DUR_FIELD] == 1
    assert time.ticks_diff(leave[0], enter[0]) == 1800
    assert w.kinds() == [ZONE, ENTER, ZONE, LEAVE]


def test_samples_across_wraparound():
    start = time.ticks_add(0, -30_000)
    w = Walk(start=start)
    w.hold(1.5, 2 * settings.SAMPLE_PERIOD_S * 1000 + SLOT)
    samples = only(w.events, SAMPLE)
    assert len(samples) == 2
    assert time.ticks_diff(samples[1][0], samples[0][0]) == settings.SAMPLE_PERIOD_S * 1000


def test_returned_list_sorted_when_several_events():
    # LEAVE (earlier stamp) and SAMPLE/ZONE in the same call come back in time order
    w = Walk()
    period_ms = settings.SAMPLE_PERIOD_S * 1000
    w.hold(0.5, period_ms - settings.LEAVE_HOLD_MS)
    w.hold(2.0, settings.LEAVE_HOLD_MS)
    out = w.step(2.0)
    assert [e[1] for e in out] == [LEAVE, SAMPLE]
    assert time.ticks_diff(out[1][0], out[0][0]) == settings.LEAVE_HOLD_MS


def test_enter_carries_first_inside_call_values():
    w = Walk()
    w.hold(2.0, 300)
    w.step(0.6, d2=0.7)
    t_first = w.now
    out = only(w.step(0.5, d2=0.8), ENTER)
    assert out and out[0][0] == t_first
    assert out[0][3:6] == (approx(0.6), approx(0.6), approx(0.7))
    assert out[0][6] == "medium"


def test_leave_carries_first_outside_call_values():
    w = Walk()
    w.hold(0.5, 600)
    w.step(2.0, d2=2.1)
    t_out = w.now
    leaves = []
    for _ in range(MAX_WAIT):
        leaves = only(w.step(None, d1=None, d2=None), LEAVE)
        if leaves:
            break
    assert leaves
    assert leaves[0][0] == t_out
    assert leaves[0][3:7] == (approx(2.0), approx(2.0), approx(2.1), "far")


def test_enter_closest_includes_confirm_call():
    w = Walk()
    w.hold(2.0, 300)
    w.step(0.8)
    w.step(0.6)
    w.hold(0.7, 600)
    w.hold(2.0, settings.LEAVE_HOLD_MS + SLOT)
    assert only(w.events, LEAVE)[0][CLOSE_FIELD] == approx(0.6)


def test_sample_and_zone_in_same_call_keep_name_order():
    w = Walk()
    period_ms = settings.SAMPLE_PERIOD_S * 1000
    w.hold(2.0, period_ms)
    out = w.step(0.5)                              # zone change on the SAMPLE call
    assert [e[1] for e in out] == [SAMPLE, ZONE]
    assert out[0][0] == out[1][0] == period_ms


def test_first_call_off_then_run_sample_timer():
    w = Walk()
    w.hold(None, 600, mode="OFF")
    w.step(1.5)
    t_run = w.now
    w.hold(1.5, settings.SAMPLE_PERIOD_S * 1000 + SLOT)
    samples = only(w.events, SAMPLE)
    assert samples[0][0] == time.ticks_add(t_run, settings.SAMPLE_PERIOD_S * 1000)


def test_zone_from_clear_at_exact_boundary_and_format_max():
    assert logic.ZoneTracker().update(1.0) == "medium"
    assert logic.format_m(4.0) == "4.00 m"


def test_leave_hold_across_wraparound():
    start = time.ticks_add(0, -2100)
    w = Walk(start=start)
    w.hold(0.5, 600)                               # 10 calls
    w.step(2.0)                                    # first outside: wrap - 1500
    t_out = w.now
    assert time.ticks_diff(0, t_out) == 1500
    leaves = []
    for _ in range(MAX_WAIT):
        leaves = only(w.step(2.0), LEAVE)
        if leaves:
            break
    assert leaves
    assert leaves[0][0] == t_out
    assert time.ticks_diff(w.now, t_out) == settings.LEAVE_HOLD_MS
