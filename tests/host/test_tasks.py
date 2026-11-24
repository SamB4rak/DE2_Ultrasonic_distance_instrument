"""Host tests for src/state.py and src/tasks.py (virtual time, fakes)."""

import asyncio
import time

import pytest

import board
import logic
import machine
import ntptime
import settings
import tasks
import thingspeak
from button import Button
from hcsr04 import HCSR04
from relay import Relay
from rgb_led import RGBLed
from state import EventQueue, State
from timesync import TimeSync
from vtime import CLOCK

SAMPLE, ZONE, ENTER, LEAVE, MODE = range(5)
SLOT = settings.RANGING_SLOT_MS
STEP = settings.INDICATOR_STEP_MS
ANCHOR = 1_800_000_000
DAY_MS = 24 * 3600 * 1000
TICKS_PERIOD = 1 << 30


@pytest.fixture(autouse=True)
def no_secret_in_output(capsys):
    """After every test: captured output never holds a (fake) secret."""
    yield
    out, err = capsys.readouterr()
    for secret in ("test-ssid", "test-password", "TEST-WRITE-KEY"):
        assert secret not in out
        assert secret not in err


def ms():
    return time.ticks_ms()


def ev(t, kind=SAMPLE, d=1.5):
    return (t, kind, "RUN", d, d, None, "far", None, None)


def echo_for(d):
    return int(round(d * 2_000_000 / settings.C_DEFAULT_MPS))


# --- test doubles -------------------------------------------------------------

class FakeSensor:
    """Scripted sensor with HCSR04's ping() timing and fault counter.

    script(t_ms) gives a distance in m, None (no target), 'NO_RISE' or 'BUSY'.
    stalls maps a ping number to a blocking stall in ms (acts out a long block).
    """

    def __init__(self, script, stalls=None, on_ping=None):
        self.script = script
        self.stalls = stalls or {}
        self.on_ping = on_ping
        self.pings = []
        self._bad = 0

    async def ping(self, wait_ms=None):
        n = len(self.pings)
        self.pings.append(ms())
        out = self.script(ms())
        if out == "BUSY":
            self._bad += 1
            return HCSR04.BUSY, 0
        if n in self.stalls:
            time.sleep_ms(self.stalls[n])
        if self.on_ping:
            self.on_ping()
        await asyncio.sleep_ms(settings.ECHO_WAIT_MS)
        if out == "NO_RISE":
            self._bad += 1
            return HCSR04.NO_RISE, 0
        self._bad = 0
        if out is None:
            return HCSR04.NO_TARGET, 0
        return HCSR04.OK, echo_for(out)

    @property
    def faulty(self):
        return self._bad >= settings.FAULT_AFTER


def const(value):
    return lambda t: value


def steps(*pairs):
    """[(until_ms, value), ...]: value until that time; the last one stays."""
    def f(t):
        for until, value in pairs:
            if t < until:
                return value
        return pairs[-1][1]
    return f


class SpyDetector:
    """A real EventDetector that records every call and the faults then."""

    def __init__(self, state):
        self.real = logic.EventDetector()
        self.state = state
        self.calls = []

    def update(self, now_ms, mode, d, d1, d2, zone):
        self.calls.append((now_ms, mode, d, d1, d2, zone, set(self.state.faults)))
        return self.real.update(now_ms, mode, d, d1, d2, zone)


class RecRelay(Relay):
    """The real Relay, recording when it was switched on."""

    def __init__(self):
        super().__init__(board.PIN_RELAY)
        self.on_times = []

    def on(self):
        self.on_times.append(ms())
        super().on()


class FakeDisplay:
    def __init__(self):
        self.ok = True
        self.renders = 0
        self.flushes = 0

    def render(self, view):
        self.renders += 1

    async def flush(self):
        await asyncio.sleep_ms(0)
        self.flushes += 1


def new_led():
    return RGBLed(board.PIN_LED_R, board.PIN_LED_G, board.PIN_LED_B)


async def sampler(led, relay, out, every=10):
    """Record (t, colour, relay on) every 10 ms."""
    while True:
        out.append((ms(), led.current, relay.is_on()))
        await asyncio.sleep_ms(every)


def now_abs():
    """Virtual ms since the test started (no wrap, unlike ticks_ms)."""
    return CLOCK.us // 1000


async def until(t):
    """Wait until virtual time t (ms since the test started)."""
    await asyncio.sleep_ms(max(0, t - now_abs()))


async def at(t, fn):
    """Call fn() at virtual time t (ms since the test started)."""
    await until(t)
    fn()


def run_all(vrun, duration_ms, *coros):
    async def go():
        await asyncio.gather(*[asyncio.create_task(c) for c in coros])
    vrun.for_ms(go(), duration_ms)


def colors_between(samples, t0, t1):
    return {c for t, c, _ in samples if t0 <= t < t1}


# --- EventQueue and State ---------------------------------------------------

def test_queue_keeps_time_order():
    q = EventQueue(10)
    for t in (10, 30, 20):
        q.add(ev(t))
    assert [e[0] for e in q.peek()] == [10, 20, 30]
    assert len(q) == 3


def test_queue_equal_times_keep_arrival_order_and_late_leave_goes_earlier():
    q = EventQueue(10)
    q.add(ev(10, ZONE))
    q.add(ev(10, ENTER))
    q.add(ev(50, SAMPLE))
    q.add(ev(10, LEAVE))         # late, stamped earlier
    assert [(e[0], e[1]) for e in q.peek()] == [(10, ZONE), (10, ENTER), (10, LEAVE), (50, SAMPLE)]


def test_queue_overflow_drops_the_oldest_and_counts():
    q = EventQueue(3)
    for t in (1, 2, 3, 4):
        q.add(ev(t))
    assert [e[0] for e in q.peek()] == [2, 3, 4]
    assert q.dropped == 1
    q.add(ev(0))                 # older than all: it is the oldest, so it goes
    assert [e[0] for e in q.peek()] == [2, 3, 4]
    assert q.dropped == 2


def test_queue_drop():
    q = EventQueue(10)
    for t in range(5):
        q.add(ev(t))
    q.drop(2)
    assert [e[0] for e in q.peek()] == [2, 3, 4]
    q.drop(0)
    assert len(q) == 3
    q.drop(10)
    assert len(q) == 0
    assert q.dropped == 0         # drop() is an upload, not a loss


def test_queue_expire_after_four_days():
    q = EventQueue(10)
    q.add(ev(0))
    q.add(ev(1000))
    four_days = 4 * DAY_MS
    q.expire(four_days)          # exactly 4 days old: kept
    assert len(q) == 2
    q.expire(four_days + 500)
    assert [e[0] for e in q.peek()] == [1000]
    assert q.dropped == 1


def test_queue_order_across_the_ticks_wrap():
    q = EventQueue(10)
    before = TICKS_PERIOD - 10
    q.add(ev(5))
    q.add(ev(before))
    assert [e[0] for e in q.peek()] == [before, 5]
    q.expire(time.ticks_add(before, 4 * DAY_MS + 1))
    assert [e[0] for e in q.peek()] == [5]


def test_state_defaults():
    s = State()
    assert (s.mode, s.d1, s.d2, s.d, s.zone, s.last_event) == ("RUN", None, None, None, "clear", None)
    assert (s.wifi, s.synced, s.cloud, s.faults) == ("down", False, "--", set())
    assert len(s.events) == 0 and s.events.dropped == 0
    assert s.events._maxlen == settings.EVENT_QUEUE_LEN


# --- mode_task ----------------------------------------------------------------

def test_mode_task_toggles_only_mode(vrun):
    class Btn:
        polls = []

        def poll(self):
            self.polls.append(ms())
            return len(self.polls) in (3, 6)

    state = State()
    before = {k: v for k, v in vars(state).items() if k not in ("mode", "events")}
    seen = []
    run_all(vrun, 200, tasks.mode_task(state, Btn()),
            at(50, lambda: seen.append(state.mode)),
            at(110, lambda: seen.append(state.mode)))
    assert Btn.polls[:6] == [0, 20, 40, 60, 80, 100]
    assert seen == ["OFF", "RUN"]
    after = {k: v for k, v in vars(state).items() if k not in ("mode", "events")}
    assert after == before
    assert len(state.events) == 0


# --- ranging_task -------------------------------------------------------------

def test_ranging_alternates_on_a_60_ms_grid(vrun):
    state = State()
    s1, s2 = FakeSensor(const(1.5)), FakeSensor(const(None))
    det = SpyDetector(state)
    vrun.for_ms(tasks.ranging_task(state, s1, s2, det), 600)
    assert s1.pings[:5] == [0, 120, 240, 360, 480]
    assert s2.pings[:5] == [60, 180, 300, 420, 540]
    assert [c[0] for c in det.calls[:4]] == [36, 96, 156, 216]   # after each ping


def test_ranging_skips_missed_slots_after_a_stall(vrun):
    state = State()
    s1 = FakeSensor(const(1.5), stalls={0: 300})   # the first ping blocks 300 ms
    s2 = FakeSensor(const(1.5))
    vrun.for_ms(tasks.ranging_task(state, s1, s2, SpyDetector(state)), 1000)
    starts = sorted(s1.pings + s2.pings)
    assert starts[:3] == [0, 360, 420]
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert min(gaps) >= SLOT
    assert all(t % SLOT == 0 for t in starts)
    assert len(starts) == 1 + (1000 - 360) // SLOT + 1


def test_ranging_distance_pipeline(vrun):
    state = State()
    s1, s2 = FakeSensor(const(1.5)), FakeSensor(const(0.5))
    vrun.for_ms(tasks.ranging_task(state, s1, s2, SpyDetector(state)), 300)
    assert state.d1 == pytest.approx(1.5, abs=1e-3)
    assert state.d2 == pytest.approx(0.5, abs=1e-3)
    assert state.d == pytest.approx(0.5, abs=1e-3)
    assert state.zone == "medium"
    assert state.faults == set()


def test_ranging_clamps_and_drops_far_readings(vrun):
    state = State()
    s1, s2 = FakeSensor(const(0.01)), FakeSensor(const(4.5))
    vrun.for_ms(tasks.ranging_task(state, s1, s2, SpyDetector(state)), 300)
    assert state.d1 == pytest.approx(settings.D_MIN_M, abs=1e-6)
    assert state.d2 is None
    assert state.zone == "close"


def test_no_rise_pushes_none_and_busy_keeps_the_value(vrun):
    state = State()
    det = SpyDetector(state)
    s1 = FakeSensor(steps((200, 1.5), (10**9, "NO_RISE")))
    s2 = FakeSensor(steps((200, 1.5), (10**9, "BUSY")))
    vrun.for_ms(tasks.ranging_task(state, s1, s2, det), 700)
    by_t = {c[0]: c for c in det.calls}
    # s1 slots 240 and 360 are NO_RISE: one dropout is tolerated, two clear it
    assert by_t[276][3] == pytest.approx(1.5, abs=1e-3)
    assert by_t[396][3] is None
    # s2 slots 300, 420, 540 are BUSY (not pinged, no wait): the value stays
    assert by_t[300][4] == pytest.approx(1.5, abs=1e-3)
    assert by_t[420][4] == pytest.approx(1.5, abs=1e-3)
    assert by_t[540][4] == pytest.approx(1.5, abs=1e-3)
    assert "S2" not in by_t[420][6]
    assert "S2" in by_t[540][6]               # 3 x BUSY -> faulty
    assert "S1" in by_t[516][6]               # 3 x NO_RISE (240, 360, 480)


def test_mode_is_read_once_per_slot(vrun):
    state = State()
    det = SpyDetector(state)

    def toggle():
        if len(s1.pings) == 3:
            state.mode = "OFF"        # the button acts while this ping waits

    s1 = FakeSensor(const(0.5), on_ping=toggle)
    s2 = FakeSensor(const(None))
    vrun.for_ms(tasks.ranging_task(state, s1, s2, det), 500)
    by_t = {c[0]: c for c in det.calls}
    assert by_t[276][1] == "RUN"              # the slot keeps the mode it read
    assert by_t[276][2] == pytest.approx(0.5, abs=1e-3)
    assert by_t[300][1] == "OFF" and by_t[300][2] is None
    assert len(s1.pings) == 3


def test_off_clears_readings_and_sensor_faults_and_stops_pinging(vrun):
    state = State()
    det = SpyDetector(state)
    s1 = FakeSensor(const("NO_RISE"))
    s2 = FakeSensor(const(0.5))
    state.faults.add("OLED")
    run_all(vrun, 2000, tasks.ranging_task(state, s1, s2, det),
            at(1000, lambda: setattr(state, "mode", "OFF")))
    assert state.mode == "OFF"
    assert (state.d1, state.d2, state.d, state.zone) == (None, None, None, "clear")
    assert state.faults == {"OLED"}            # only its own codes
    assert max(s1.pings + s2.pings) < 1000
    off_calls = [c for c in det.calls if c[0] >= 1000]
    assert len(off_calls) == pytest.approx(1000 / SLOT, abs=1)   # still every slot
    assert all(c[1] == "OFF" and c[2] is None and c[5] == "clear" for c in off_calls)
    assert any(e[1] == MODE and e[2] == "OFF" for e in state.events.peek())


def test_walk_up_gives_zone_enter_and_leave(vrun):
    state = State()
    s1 = FakeSensor(steps((1000, 2.0), (3000, 0.5), (10**9, None)))
    s2 = FakeSensor(const(None))
    vrun.for_ms(tasks.ranging_task(state, s1, s2, logic.EventDetector()), 8000)
    events = state.events.peek()
    names = [logic.EVENT_NAMES[e[1]] for e in events]
    assert names == ["ZONE", "ENTER", "ZONE", "LEAVE"]
    zone1, enter, zone2, leave = events
    assert zone1[6] == "medium" and zone2[6] == "clear"
    assert enter[0] == zone1[0]                      # the first call inside
    assert leave[0] == zone2[0]                      # stamped when it left
    assert leave[7] == pytest.approx(0.5, abs=1e-3)  # closest
    assert leave[8] == time.ticks_diff(leave[0], enter[0]) // 1000
    assert leave[8] == 1                             # 1.92 s inside ALERT_M
    assert state.last_event == "LEAVE"
    ts = [e[0] for e in events]
    assert ts == sorted(ts)


def test_samples_every_period_and_mode_on_each_toggle(vrun):
    state = State()
    s1, s2 = FakeSensor(const(1.5)), FakeSensor(const(None))
    run_all(vrun, 130_000, tasks.ranging_task(state, s1, s2, logic.EventDetector()),
            at(125_000, lambda: setattr(state, "mode", "OFF")),
            at(127_000, lambda: setattr(state, "mode", "RUN")))
    events = state.events.peek()
    samples = [e[0] for e in events if e[1] == SAMPLE]
    assert len(samples) == 2
    assert samples[0] == pytest.approx(60_000, abs=SLOT)
    assert samples[1] - samples[0] == pytest.approx(60_000, abs=SLOT)
    modes = [e[2] for e in events if e[1] == MODE]
    assert modes == ["OFF", "RUN"]
    ts = [e[0] for e in events]
    assert ts == sorted(ts)


def test_queue_overflow_through_the_ranging_task(vrun, monkeypatch):
    monkeypatch.setattr(settings, "EVENT_QUEUE_LEN", 3)
    monkeypatch.setattr(settings, "SAMPLE_PERIOD_S", 1)
    state = State()
    s1, s2 = FakeSensor(const(1.5)), FakeSensor(const(None))
    vrun.for_ms(tasks.ranging_task(state, s1, s2, logic.EventDetector()), 10_500)
    assert len(state.events) == 3
    assert state.events.dropped >= 6
    ts = [e[0] for e in state.events.peek()]
    assert ts == sorted(ts) and ts[-1] > 9000


# --- indicator_task -----------------------------------------------------------

def zone_state(d, zone=None):
    s = State()
    s.d = d
    s.zone = zone or logic.ZoneTracker().update(d)
    return s


@pytest.mark.parametrize("setup,color", [
    (lambda s: setattr(s, "mode", "OFF"), settings.COLOR_BLUE),
    (lambda s: s.faults.add("S1"), settings.COLOR_MAGENTA),
    (lambda s: s.faults.add("OLED"), settings.COLOR_MAGENTA),
    (lambda s: (setattr(s, "d", None), setattr(s, "zone", "clear")), settings.COLOR_GREEN),
])
def test_indicator_steady_states(vrun, setup, color):
    state = zone_state(0.2)
    setup(state)
    led, relay, samples = new_led(), RecRelay(), []
    run_all(vrun, 2000, tasks.indicator_task(state, led, relay), sampler(led, relay, samples))
    assert colors_between(samples, 1, 2000) == {color}
    assert relay.on_times == []
    assert not any(on for _, _, on in samples)


def test_indicator_off_wins_over_faults():
    state = zone_state(0.2)
    state.mode = "OFF"
    state.faults.add("S2")
    assert tasks._steady_color(state) == settings.COLOR_BLUE


@pytest.mark.parametrize("d,expected", [(2.0, 5), (0.1, 25)])
def test_indicator_pulse_counts_over_5_s(vrun, d, expected):
    state = zone_state(d)
    led, relay = new_led(), RecRelay()
    vrun.for_ms(tasks.indicator_task(state, led, relay), 4999)
    assert abs(len(relay.on_times) - expected) <= 1
    gaps = [b - a for a, b in zip(relay.on_times, relay.on_times[1:])]
    assert min(gaps) >= 200


def test_led_is_on_for_half_the_period(vrun):
    state = zone_state(2.0)              # 1 Hz, far
    led, relay, samples = new_led(), RecRelay(), []
    run_all(vrun, 2000, tasks.indicator_task(state, led, relay), sampler(led, relay, samples))
    lit = [t for t, c, _ in samples if t < 1000 and c == settings.COLOR_GREEN]
    dark = [t for t, c, _ in samples if t < 1000 and c == settings.COLOR_OFF]
    assert lit[0] == 0 and lit[-1] == 490
    assert dark[0] == 500 and len(dark) == 50
    assert relay.on_times[:2] == [0, 1000]
    on = [t for t, _, r in samples if r and t < 1000]
    assert on == [10, 20, 30, 40]        # one 50 ms click (0-50 ms) per flash


def test_zone_change_starts_a_tick_at_once(vrun):
    state = zone_state(2.0)
    led, relay, samples = new_led(), RecRelay(), []

    def nearer():
        state.d, state.zone = 0.5, "medium"

    run_all(vrun, 1000, tasks.indicator_task(state, led, relay),
            sampler(led, relay, samples), at(290, nearer))
    assert relay.on_times[:2] == [0, 300]
    assert colors_between(samples, 300, 310) == {settings.COLOR_YELLOW}


def test_zone_change_waits_for_the_200_ms_gap(vrun):
    state = zone_state(2.0)
    led, relay = new_led(), RecRelay()

    def nearer():
        state.d, state.zone = 0.2, "close"

    run_all(vrun, 400, tasks.indicator_task(state, led, relay), at(80, nearer))
    assert relay.on_times[:2] == [0, 200]


def test_flapping_zone_never_pulses_faster_than_5_hz(vrun):
    state = zone_state(0.5)
    led, relay = new_led(), RecRelay()

    async def flap():
        while True:
            await asyncio.sleep_ms(STEP)
            if state.zone == "medium":
                state.d, state.zone = 0.2, "close"
            else:
                state.d, state.zone = 0.5, "medium"

    run_all(vrun, 3000, tasks.indicator_task(state, led, relay), flap())
    gaps = [b - a for a, b in zip(relay.on_times, relay.on_times[1:])]
    assert len(relay.on_times) >= 10
    assert min(gaps) >= 200


def test_new_distance_in_the_same_zone_changes_the_next_period(vrun):
    state = zone_state(0.9)              # medium, 1.44 Hz -> 692 ms
    led, relay = new_led(), RecRelay()

    def closer():
        state.d = 0.31                    # still medium, 4.07 Hz -> 245 ms

    run_all(vrun, 1000, tasks.indicator_task(state, led, relay), at(100, closer))
    assert tasks._period_ms(0.9) == 692 and tasks._period_ms(0.31) == 245
    assert relay.on_times[:3] == [0, 700, 950]


def test_period_ms_is_an_int_for_every_distance():
    for cm in range(2, 401):
        p = tasks._period_ms(cm / 100)
        assert isinstance(p, int)
        assert 200 <= p <= 1000


def test_next_slot_skips_without_loops(fake_clock):
    assert tasks._next_slot(0, 60) == 60
    fake_clock.advance_ms(1000)
    assert tasks._next_slot(0, 60) == 1020
    fake_clock.advance_ms(20)
    assert tasks._next_slot(960, 60) == 1020          # exactly due: kept


# --- faults --------------------------------------------------------------------

def test_sensor_fault_shows_magenta_and_silences_the_relay(vrun):
    state = State()
    det = SpyDetector(state)
    s1 = FakeSensor(steps((1000, 0.5), (10**9, "NO_RISE")))
    s2 = FakeSensor(const(None))
    led, relay, samples = new_led(), RecRelay(), []
    run_all(vrun, 2500, tasks.ranging_task(state, s1, s2, det),
            tasks.indicator_task(state, led, relay), sampler(led, relay, samples, every=1))
    assert relay.on_times                                  # it was ticking
    first = next(c for c in det.calls if "S1" in c[6])
    # S1 is set in the slot of the third NO_RISE ping (1080, 1200, 1320)
    assert s1.pings[s1.pings.index(1080) + 2] == 1320
    assert first[0] == 1320 + settings.ECHO_WAIT_MS
    t_fault = first[0]
    magenta = next(t for t, c, _ in samples if t >= t_fault and c == settings.COLOR_MAGENTA)
    assert magenta - t_fault <= 2 * SLOT
    assert all(t < t_fault for t in relay.on_times)
    assert not any(on for t, _, on in samples if t >= t_fault + 2 * SLOT)


def test_real_sensor_no_rise_and_busy_give_s1(vrun):
    state = State()
    s1 = HCSR04(board.PIN_TRIG_1, board.PIN_ECHO_1, "L")   # no echo edges: NO_RISE
    s2 = FakeSensor(const(None))
    vrun.for_ms(tasks.ranging_task(state, s1, s2, logic.EventDetector()), 300)
    assert "S1" in state.faults and state.d1 is None


def test_real_sensor_busy_three_times_gives_s1(vrun):
    state = State()
    s1 = HCSR04(board.PIN_TRIG_1, board.PIN_ECHO_1, "L")
    machine.Pin.get(board.PIN_ECHO_1).set_input(1)          # Echo stuck HIGH
    s2 = FakeSensor(const(None))
    seen = []
    run_all(vrun, 400, tasks.ranging_task(state, s1, s2, logic.EventDetector()),
            at(200, lambda: seen.append(set(state.faults))),
            at(260, lambda: seen.append(set(state.faults))))
    assert seen == [set(), {"S1"}]          # BUSY slots 0, 120, 240


def test_display_task_tracks_the_oled_fault(vrun):
    state = State()
    disp = FakeDisplay()
    seen = []
    run_all(vrun, 1100, tasks.display_task(state, disp),
            at(300, lambda: setattr(disp, "ok", False)),
            at(500, lambda: seen.append(set(state.faults))),
            at(700, lambda: setattr(disp, "ok", True)),
            at(900, lambda: seen.append(set(state.faults))))
    assert seen == [{"OLED"}, set()]
    assert disp.renders == disp.flushes == 6        # 0, 200, ... 1000


# --- 10 s system simulation -----------------------------------------------------

def test_ten_second_simulation(vrun):
    import main
    state = State()
    s1 = FakeSensor(steps((2000, 2.0), (4000, 0.5), (6000, 0.1), (8000, None), (10**9, 2.0)))
    s2 = FakeSensor(const(None))
    relay, led = RecRelay(), new_led()
    button = Button(board.PIN_BUTTON)
    pin = machine.Pin.get(board.PIN_BUTTON)
    samples = []

    async def press(t):
        await at(t, lambda: pin.set_input(0))
        await at(t + 100, lambda: pin.set_input(1))

    run_all(vrun, 10_000,
            main.main(state, relay, led, FakeDisplay(), s1, s2, button, logic.EventDetector()),
            sampler(led, relay, samples), press(7000), press(8500))

    def pulses(t0, t1):
        return len([t for t in relay.on_times if t0 <= t < t1])

    # the median filter needs two readings of a sensor after each change
    assert abs(pulses(250, 2000) - 2) <= 1                    # 1 Hz
    assert abs(pulses(2500, 4000) - 1500 / 350) <= 1          # 3.2 Hz, 350 ms steps
    assert abs(pulses(4500, 6000) - 1500 / 200) <= 1          # 5 Hz
    assert pulses(6500, 8500) == 0                            # clear, then OFF
    assert pulses(9000, 10_000) >= 1                          # RUN again, far

    assert settings.COLOR_GREEN in colors_between(samples, 0, 2000)
    assert settings.COLOR_YELLOW in colors_between(samples, 2500, 4000)
    assert settings.COLOR_RED in colors_between(samples, 4500, 6000)
    assert colors_between(samples, 6500, 7000) == {settings.COLOR_GREEN}
    assert colors_between(samples, 7200, 8500) == {settings.COLOR_BLUE}
    assert settings.COLOR_GREEN in colors_between(samples, 8800, 10_000)
    assert not any(on for t, _, on in samples if 7100 <= t < 8600)
    assert not [t for t in s1.pings + s2.pings if 7100 <= t < 8500]     # no pings while OFF

    events = state.events.peek()
    zones = [e[6] for e in events if e[1] == ZONE]
    assert zones == ["medium", "close", "clear", "far"]
    names = [logic.EVENT_NAMES[e[1]] for e in events]
    assert names.count("ENTER") == 1 and names.count("LEAVE") == 1
    assert [e[2] for e in events if e[1] == MODE] == ["OFF", "RUN"]
    leave = next(e for e in events if e[1] == LEAVE)
    assert leave[7] == pytest.approx(0.1, abs=1e-3)
    ts = [e[0] for e in events]
    assert ts == sorted(ts)


# --- logger ----------------------------------------------------------------------

class FakeWiFi:
    """Scripted WiFi: connect() takes 500 ms; result(t) decides it."""

    def __init__(self, result=True):
        self.result = result if callable(result) else (lambda t: result)
        self.connected = False
        self.connects = []
        self.disconnects = 0

    async def connect(self, timeout_s=None):
        self.connects.append(ms())
        await asyncio.sleep_ms(500)
        self.connected = bool(self.result(ms()))
        return self.connected

    def is_connected(self):
        return self.connected

    def disconnect(self):
        self.disconnects += 1
        self.connected = False


class FakeTS:
    """Scripted ThingSpeak: bulk_post() takes 300 ms; result(n, t) decides."""

    def __init__(self, state, result=True, resolve_ok=True):
        self.state = state
        self.result = result if callable(result) else (lambda n, t: result)
        self.resolve_ok = resolve_ok
        self.ip = False
        self.resolves = []
        self.posts = []          # (t, updates, cloud before)
        self.accepted = []       # updates lists that returned True
        self.wifi = None

    @property
    def resolved(self):
        return self.ip

    def resolve(self):
        self.resolves.append(ms())
        self.ip = self.resolve_ok
        return self.ip

    async def bulk_post(self, updates):
        self.posts.append((ms(), updates, self.state.cloud))
        await asyncio.sleep_ms(300)
        result = self.result(len(self.posts), ms())
        if result is True:
            self.accepted.append(updates)
        return result


def good_clock(offset=lambda: 0):
    """A TimeSync whose fake NTP answer follows the virtual clock."""
    def ntp_time():
        ntptime.calls += 1
        return ANCHOR + machine_seconds() + offset()
    return ntp_time


def machine_seconds():
    return CLOCK.us // 1_000_000


async def feed(state, times, kind=SAMPLE):
    for t in times:
        await until(t)
        state.events.add(ev(ms(), kind))


def logger(state, wifi, ts, clock):
    return tasks.logger_task(state, wifi, ts, clock)


def uploaded_statuses(ts):
    names = []
    for updates in ts.accepted:
        for u in updates:
            names += u["status"].split("; ")
    return names


def test_nothing_is_uploaded_before_the_clock_is_synced(vrun):
    ntptime.script(OSError(110), ANCHOR)     # first sync fails, the retry works
    state, clock = State(), TimeSync()
    wifi, ts = FakeWiFi(), None
    ts = FakeTS(state)
    syncs = []
    real_sync = clock.sync
    clock.sync = lambda: (syncs.append((ms(), real_sync())), syncs[-1][1])[1]
    run_all(vrun, 400_000, logger(state, wifi, ts, clock), feed(state, [2000, 4000, 6000]))
    assert syncs[0][1] is False and syncs[1][1] is True
    assert syncs[1][0] - syncs[0][0] >= settings.NTP_RETRY_S * 1000
    assert syncs[1][0] - syncs[0][0] < settings.NTP_RETRY_S * 1000 + 2000
    assert ts.posts and ts.posts[0][0] > syncs[1][0]
    assert len(state.events) == 0 and state.synced is True and state.cloud == "ok"


def test_only_settled_seconds_are_uploaded(vrun, monkeypatch):
    monkeypatch.setattr(ntptime, "time", good_clock())
    state, clock = State(), TimeSync()
    ts = FakeTS(state)
    times = list(range(1000, 120_000, 3700))
    run_all(vrun, 130_000, logger(state, FakeWiFi(), ts, clock), feed(state, times))
    for t_post, updates, _ in ts.posts:
        limit = clock.unix(time.ticks_add(t_post, -(settings.LEAVE_HOLD_MS + 2000)))
        for u in updates:
            assert u["created_at"] < TimeSync.stamp(limit)
    assert uploaded_statuses(ts).count("SAMPLE") == len(times) - len(state.events)
    assert len(state.events) <= 2


def test_requests_are_at_least_the_min_gap_apart(vrun, monkeypatch):
    monkeypatch.setattr(ntptime, "time", good_clock())
    state, clock = State(), TimeSync()
    ts = FakeTS(state, result=lambda n, t: (None, False, True)[n % 3])
    run_all(vrun, 600_000, logger(state, FakeWiFi(), ts, clock),
            feed(state, range(1000, 600_000, 2000)))
    starts = [p[0] for p in ts.posts]
    assert len(starts) >= 25
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert min(gaps) >= settings.UPLOAD_MIN_GAP_S * 1000
    assert max(gaps) <= settings.UPLOAD_PERIOD_S * 1000 + 2000


def test_true_false_none_results(vrun, monkeypatch):
    monkeypatch.setattr(ntptime, "time", good_clock())
    state, clock = State(), TimeSync()
    seen = []
    ts = FakeTS(state, result=lambda n, t: {1: False, 2: None}.get(n, True))

    async def watch():
        while True:
            seen.append((len(ts.posts), state.cloud, len(state.events)))
            await asyncio.sleep_ms(1000)

    run_all(vrun, 90_000, logger(state, FakeWiFi(), ts, clock),
            feed(state, [500, 1500, 2500]), watch())
    assert len(ts.posts) >= 3
    first, second, third = ts.posts[:3]
    assert second[1] is first[1] and third[1] is first[1]         # identical batch
    assert [p[2] for p in ts.posts[1:3]] == ["rej", "--"]           # cloud after 1, 2
    after = {n: (cloud, q) for n, cloud, q in seen if n in (1, 2, 3)}
    assert after[1] == ("rej", 3)
    assert after[2] == ("--", 3)
    assert after[3] == ("ok", 0)
    assert len(ts.resolves) == 1


def test_none_results_resolve_again_after_the_limit(vrun, monkeypatch):
    monkeypatch.setattr(ntptime, "time", good_clock())
    state, clock = State(), TimeSync()
    ts = FakeTS(state, result=None)
    yields = []
    real_yield = tasks._yield_slots

    async def spy_yield():
        yields.append(ms())
        await real_yield()

    monkeypatch.setattr(tasks, "_yield_slots", spy_yield)
    run_all(vrun, 200_000, logger(state, FakeWiFi(), ts, clock), feed(state, [500]))
    n = settings.HTTP_FAILS_BEFORE_RESOLVE
    assert len(ts.posts) >= 2 * n
    # one resolve at the connect, then one after every n failed posts
    assert len(ts.resolves) == 1 + len(ts.posts) // n
    third_end = ts.posts[n - 1][0] + 300
    assert ts.resolves[1] == pytest.approx(third_end, abs=5)
    assert all(p[1] is ts.posts[0][1] for p in ts.posts)
    assert state.cloud == "--" and len(state.events) == 1
    # every blocking call (resolves + the sync at connect) is followed by a yield
    assert len(yields) == len(ts.resolves) + 1


def test_none_with_the_link_down_reconnects_and_resends(vrun, monkeypatch):
    monkeypatch.setattr(ntptime, "time", good_clock())
    state, clock = State(), TimeSync()
    wifi = FakeWiFi()

    def result(n, t):
        if n == 1:
            wifi.connected = False             # the hotspot went away
            return None
        return True

    ts = FakeTS(state, result=result)
    run_all(vrun, 60_000, logger(state, wifi, ts, clock), feed(state, [500, 1500]))
    assert len(wifi.connects) == 2
    assert ts.posts[1][1] is ts.posts[0][1]
    assert ts.posts[1][0] - ts.posts[0][0] >= settings.UPLOAD_MIN_GAP_S * 1000
    assert len(ts.resolves) == 1                 # the IP is still cached
    assert state.wifi == "up" and state.cloud == "ok" and len(state.events) == 0


def test_failed_resolve_disconnects_and_waits(vrun, monkeypatch):
    monkeypatch.setattr(ntptime, "time", good_clock())
    state, clock = State(), TimeSync()
    wifi = FakeWiFi()
    ts = FakeTS(state, resolve_ok=False)
    run_all(vrun, 30_000, logger(state, wifi, ts, clock))
    assert wifi.disconnects >= 2
    gaps = [b - a for a, b in zip(wifi.connects, wifi.connects[1:])]
    assert min(gaps) >= settings.WIFI_RETRY_S * 1000
    assert ts.posts == [] and ntptime.calls == 0
    assert state.wifi == "down"


def test_wifi_failure_waits_before_retrying(vrun, monkeypatch):
    monkeypatch.setattr(ntptime, "time", good_clock())
    state, clock = State(), TimeSync()
    wifi = FakeWiFi(result=lambda t: t > 25_000)
    ts = FakeTS(state)
    states = []
    run_all(vrun, 40_000, logger(state, wifi, ts, clock),
            at(5000, lambda: states.append(state.wifi)),
            at(39_000, lambda: states.append(state.wifi)))
    gaps = [b - a for a, b in zip(wifi.connects, wifi.connects[1:])]
    assert min(gaps) >= settings.WIFI_RETRY_S * 1000
    assert states == ["down", "up"]


def test_true_drops_n_less_the_overflow_since_the_batch(vrun, monkeypatch):
    monkeypatch.setattr(ntptime, "time", good_clock())
    monkeypatch.setattr(settings, "EVENT_QUEUE_LEN", 10)
    state, clock = State(), TimeSync()
    ts = FakeTS(state, result=lambda n, t: None if n == 1 else True)
    early = [1000 + 1000 * i for i in range(8)]            # 8 events, 8 seconds
    late = [26_000 + 200 * i for i in range(5)]            # after post 1, before post 2
    first = {}

    async def snapshot():
        await at(25_900, lambda: first.update(batch=ts.posts[0][1], n=len(ts.posts)))

    run_all(vrun, 50_000, logger(state, FakeWiFi(), ts, clock),
            feed(state, early), feed(state, late), snapshot())
    assert first["n"] == 1 and len(first["batch"]) == 8
    assert ts.posts[1][1] is first["batch"]
    assert state.events.dropped == 3
    # 8 uploaded - 3 lost to overflow = 5 removed; the 5 new events remain
    assert len(ts.posts) == 2 and ts.accepted == [first["batch"]]
    assert [e[0] for e in state.events.peek()] == late


def test_batch_is_resent_across_resync_and_loss_of_sync(vrun, monkeypatch):
    monkeypatch.setattr(settings, "NTP_RESYNC_S", 30)       # synced for 120 s
    ntptime.script(ANCHOR, OSError(110))                    # re-syncs fail
    state, clock = State(), TimeSync()
    ts = FakeTS(state, result=lambda n, t: t > 200_000)
    synced_at_post = []
    real_post = ts.bulk_post

    async def post(updates):
        synced_at_post.append(clock.synced)
        return await real_post(updates)

    ts.bulk_post = post
    run_all(vrun, 230_000, logger(state, FakeWiFi(), ts, clock), feed(state, [500, 1500]))
    assert ntptime.calls >= 2                          # a re-sync was tried
    assert False in synced_at_post                     # re-sent while unsynced
    accepted = [i for i, p in enumerate(ts.posts) if p[0] + 300 > 200_000]
    assert all(p[1] is ts.posts[0][1] for p in ts.posts[:accepted[0] + 1])
    assert len(state.events) == 0


def test_resync_after_the_period_and_retry_wait_after_a_failure(vrun, monkeypatch):
    monkeypatch.setattr(settings, "NTP_RESYNC_S", 100)
    ok = {"v": True}

    def ntp():
        ntptime.calls += 1
        if not ok["v"]:
            raise OSError(110)
        return ANCHOR + machine_seconds()

    monkeypatch.setattr(ntptime, "time", ntp)
    state, clock = State(), TimeSync()
    calls = []
    real_sync = clock.sync
    clock.sync = lambda: (calls.append(ms()), real_sync())[1]
    run_all(vrun, 700_000, logger(state, FakeWiFi(), FakeTS(state), clock),
            at(150_000, lambda: ok.update(v=False)))
    assert calls[0] < 2000
    assert calls[1] - calls[0] == pytest.approx(100_000, abs=1500)
    # third (at ~200 s) fails; the next waits NTP_RETRY_S
    assert calls[3] - calls[2] == pytest.approx(settings.NTP_RETRY_S * 1000, abs=1500)


def test_seven_days_offline(vrun, monkeypatch):
    monkeypatch.setattr(ntptime, "time", good_clock())
    monkeypatch.setattr(tasks, "_PASS_MS", 300_000)   # fast offline passes
    seven = 7 * DAY_MS
    state, clock = State(), TimeSync()
    online = {"v": True}
    wifi = FakeWiFi(result=lambda t: online["v"])

    def result(n, t):
        return True if online["v"] else None

    ts = FakeTS(state, result=result)

    def drop_link():
        online["v"] = False
        wifi.connected = False

    def back():
        online["v"] = True
        tasks._PASS_MS = 1000

    day6 = [6 * DAY_MS + 1000 * i for i in range(3)]
    run_all(vrun, seven + 400_000, logger(state, wifi, ts, clock),
            feed(state, [500, 1500]), at(30 * 60_000, drop_link),
            feed(state, [DAY_MS + 1000 * i for i in range(4)]), feed(state, day6),
            at(seven, back))
    assert state.events.dropped == 4                 # the day-1 events expired
    assert len(ts.accepted) >= 2
    assert ts.accepted[0][0]["status"] == "SAMPLE"
    reconnect_posts = [p for p in ts.posts if p[0] >= seven]
    assert reconnect_posts and reconnect_posts[0][0] - seven < 320_000
    starts = [p[0] for p in reconnect_posts]
    assert all(b - a >= settings.UPLOAD_MIN_GAP_S * 1000 for a, b in zip(starts, starts[1:]))
    # the batch built on day 1 (4 events) was pending and is re-sent unchanged,
    # although those events have expired from the queue meanwhile; nothing is
    # dropped twice, and the day-6 events follow in the next batch
    assert [len(u) for u in ts.accepted] == [2, 4, 3]
    assert uploaded_statuses(ts).count("SAMPLE") == 2 + 4 + 3
    assert len(state.events) == 0 and state.synced and state.wifi == "up"
    retries = [t for t in wifi.connects if 30 * 60_000 < t < seven]
    gaps = [b - a for a, b in zip(retries, retries[1:])]
    assert gaps and min(gaps) >= settings.WIFI_RETRY_S * 1000


def test_blocking_sync_loses_no_leave(vrun):
    ntptime.script(ANCHOR, delay_ms=7000)
    state, clock = State(), TimeSync()
    s1 = FakeSensor(steps((20_000, 0.5), (10**9, None)))
    s2 = FakeSensor(const(None))
    wifi = FakeWiFi(result=lambda t: t > 21_000)
    ts = FakeTS(state)
    run_all(vrun, 60_000, tasks.ranging_task(state, s1, s2, logic.EventDetector()),
            logger(state, wifi, ts, clock))
    statuses = uploaded_statuses(ts)
    assert "ENTER" in statuses
    leave_updates = [u for ups in ts.accepted for u in ups if "LEAVE" in u["status"]]
    assert len(leave_updates) == 1
    # the LEAVE keeps its own second (it left at about 20.2 s)
    sync_ticks = clock._anchor_ticks
    left_s = clock.unix(20_000 + 4 * SLOT)
    assert leave_updates[0]["created_at"] in (TimeSync.stamp(left_s - 1), TimeSync.stamp(left_s),
                                              TimeSync.stamp(left_s + 1))
    assert sync_ticks > 27_000
    stamps = [u["created_at"] for ups in ts.accepted for u in ups]
    assert stamps == sorted(stamps) and len(set(stamps)) == len(stamps)


def test_ten_minutes_offline_keeps_the_original_times(vrun, monkeypatch):
    monkeypatch.setattr(ntptime, "time", good_clock())
    state, clock = State(), TimeSync()
    online = {"v": True}
    wifi = FakeWiFi(result=lambda t: online["v"])
    ts = FakeTS(state, result=lambda n, t: True if online["v"] else None)

    def drop_link():
        online["v"] = False
        wifi.connected = False

    times = list(range(1000, 700_000, 7000))
    run_all(vrun, 720_000, logger(state, wifi, ts, clock), feed(state, times),
            at(60_000, drop_link), at(660_000, lambda: online.update(v=True)))
    by_stamp = {}
    for ups in ts.accepted:
        for u in ups:
            by_stamp[u["created_at"]] = u
    uploaded = len(times) - len(state.events)
    assert uploaded >= len(times) - 3
    for t in times[:uploaded]:
        exact = ANCHOR + (t + 500) // 1000
        assert any(TimeSync.stamp(exact + k) in by_stamp for k in (-1, 0, 1))
    assert len(by_stamp) == uploaded
    stamps = [u["created_at"] for ups in ts.accepted for u in ups]
    assert stamps == sorted(stamps)


def test_resync_that_moves_the_clock_loses_nothing(vrun, monkeypatch):
    monkeypatch.setattr(settings, "NTP_RESYNC_S", 60)
    shift = {"v": 0}
    monkeypatch.setattr(ntptime, "time", good_clock(lambda: shift["v"]))
    state, clock = State(), TimeSync()
    ts = FakeTS(state)
    times = list(range(1000, 200_000, 400))              # 2-3 events per second
    run_all(vrun, 220_000, logger(state, FakeWiFi(), ts, clock), feed(state, times),
            at(50_000, lambda: shift.update(v=1)), at(110_000, lambda: shift.update(v=0)))
    stamps = [u["created_at"] for ups in ts.accepted for u in ups]
    assert stamps == sorted(stamps) and len(set(stamps)) == len(stamps)
    assert uploaded_statuses(ts).count("SAMPLE") == len(times) - len(state.events)
    assert len(state.events) < 20


def test_end_to_end_with_the_real_network_modules(vrun, monkeypatch):
    import socket
    import network
    from net_fakes import FakeGetaddrinfo, FakeOpenConnection, FakeStream
    from thingspeak import ThingSpeak
    from wifi import WiFi
    network.WLAN.script_next([1, 1, network.STAT_GOT_IP])
    monkeypatch.setattr(socket, "getaddrinfo", FakeGetaddrinfo("184.106.153.149"))
    opener = FakeOpenConnection(lambda: FakeStream(
        [b"HTTP/1.1 202 Accepted\r\nContent-Type: application/json\r\n\r\n", b'{"success": true}']))
    monkeypatch.setattr(asyncio, "open_connection", opener)
    ntptime.script(ANCHOR)
    state = State()
    wifi = WiFi("test-ssid", "test-password")
    ts = ThingSpeak("TEST-WRITE-KEY", "1234567")
    run_all(vrun, 40_000, logger(state, wifi, ts, TimeSync()), feed(state, [1000, 2000, 3000]))
    assert state.wifi == "up" and state.synced and state.cloud == "ok"
    assert len(state.events) == 0
    assert len(opener.streams) >= 1
    body = bytes(opener.streams[0].written)
    assert b"bulk_update.json" in body and body.count(b"SAMPLE") == 3


# --- more edge cases --------------------------------------------------------------

def test_system_across_the_ticks_wrap(vrun, fake_clock, monkeypatch):
    fake_clock.reset((TICKS_PERIOD - 3000) * 1000)        # wraps 3 s into the run
    start_s = fake_clock.us // 1_000_000
    monkeypatch.setattr(ntptime, "time", lambda: ANCHOR + fake_clock.us // 1_000_000 - start_s)
    monkeypatch.setattr(settings, "SAMPLE_PERIOD_S", 2)
    state, clock = State(), TimeSync()
    s1 = FakeSensor(lambda t: 2.0 if fake_clock.us // 1000 - start_s * 1000 < 5000 else 0.5)
    s2 = FakeSensor(const(None))
    led, relay = new_led(), RecRelay()
    ts = FakeTS(state)
    run_all(vrun, 40_000, tasks.ranging_task(state, s1, s2, logic.EventDetector()),
            tasks.indicator_task(state, led, relay), logger(state, FakeWiFi(), ts, clock))
    starts = sorted(s1.pings + s2.pings, key=lambda t: time.ticks_diff(t, s1.pings[0]))
    gaps = [time.ticks_diff(b, a) for a, b in zip(starts, starts[1:])]
    assert set(gaps) == {SLOT}
    assert any(t < 1000 for t in starts) and any(t > TICKS_PERIOD - 1000 for t in starts)
    pulse_gaps = [time.ticks_diff(b, a) for a, b in zip(relay.on_times, relay.on_times[1:])]
    assert min(pulse_gaps) >= 200
    assert pulse_gaps[:3] == [1000, 1000, 1000]            # 1 Hz across the wrap
    ts_list = [e[0] for e in state.events.peek()]
    assert all(time.ticks_diff(b, a) >= 0 for a, b in zip(ts_list, ts_list[1:]))
    names = uploaded_statuses(ts)
    assert "ENTER" in names and names.count("SAMPLE") >= 5
    stamps = [u["created_at"] for ups in ts.accepted for u in ups]
    assert stamps == sorted(stamps) and len(set(stamps)) == len(stamps)


def test_sensor_recovers_and_clears_s1(vrun):
    state = State()
    det = SpyDetector(state)
    s1 = FakeSensor(steps((200, 0.5), (600, "NO_RISE"), (10**9, 0.5)))
    s2 = FakeSensor(const(None))
    led, relay, samples = new_led(), RecRelay(), []
    run_all(vrun, 2000, tasks.ranging_task(state, s1, s2, det),
            tasks.indicator_task(state, led, relay), sampler(led, relay, samples))
    with_s1 = [c[0] for c in det.calls if "S1" in c[6]]
    assert with_s1                                      # NO_RISE at 240, 360, 480
    first_good = next(t for t in s1.pings if t >= 600)
    cleared = next(c[0] for c in det.calls if c[0] > with_s1[0] and "S1" not in c[6])
    assert cleared == first_good + settings.ECHO_WAIT_MS
    assert settings.COLOR_YELLOW in colors_between(samples, cleared, 2000)
    assert [t for t in relay.on_times if t > cleared]   # ticking again


def test_batch_is_resent_after_a_successful_resync(vrun, monkeypatch):
    monkeypatch.setattr(settings, "NTP_RESYNC_S", 30)
    shift = {"v": 0}
    monkeypatch.setattr(ntptime, "time", good_clock(lambda: shift["v"]))
    state, clock = State(), TimeSync()
    syncs = []
    real_sync = clock.sync
    clock.sync = lambda: (syncs.append(ms()), real_sync())[1]
    ts = FakeTS(state, result=lambda n, t: None if t < 80_000 else True)
    run_all(vrun, 100_000, logger(state, FakeWiFi(), ts, clock), feed(state, [500, 1500]),
            at(20_000, lambda: shift.update(v=1)))
    assert len(syncs) >= 2 and syncs[1] < 80_000          # a re-sync worked meanwhile
    assert clock.synced
    accepted = next(i for i, p in enumerate(ts.posts) if p[0] + 300 >= 80_000)
    assert all(p[1] is ts.posts[0][1] for p in ts.posts[:accepted + 1])
    assert len(state.events) == 0


def test_display_task_with_the_real_display(vrun):
    from display import Display
    bus = machine.I2C(board.I2C_ID)
    bus.attach(board.ADDR_OLED, machine.I2CDevice())
    disp = Display(bus)
    state = State()
    seen = []
    run_all(vrun, 6000, tasks.display_task(state, disp),
            at(500, lambda: bus.detach(board.ADDR_OLED)),
            at(550, lambda: setattr(state, "d1", 1.23)),     # a changed page is sent
            at(1000, lambda: seen.append(set(state.faults))),
            at(1500, lambda: bus.attach(board.ADDR_OLED, machine.I2CDevice())),
            at(5000, lambda: seen.append(set(state.faults))))
    assert seen == [{"OLED"}, set()]


def test_unknown_fault_code_is_ignored_by_the_indicator():
    state = zone_state(0.2)
    state.faults.add("X")
    assert tasks._steady_color(state) is None
