"""Host tests for src/wifi.py, src/timesync.py and src/thingspeak.py."""

import asyncio
import json
import random
import socket
import time

import pytest

import network
import ntptime
import settings
from net_fakes import FakeGetaddrinfo, FakeOpenConnection, FakeStream

import thingspeak
from thingspeak import ThingSpeak, make_updates
from timesync import TimeSync
from wifi import WiFi

SSID = "test-ssid"
PASSWORD = "test-password"
KEY = "TEST-WRITE-KEY"
CHANNEL = "1234567"
IP = "184.106.153.149"
ANCHOR = 1_800_000_000          # Unix seconds the fake NTP server returns
TICKS_PERIOD_MS = 1 << 30

SAMPLE, ZONE, ENTER, LEAVE, MODE = range(5)


@pytest.fixture(autouse=True)
def no_secret_in_output(capsys):
    """After every test: captured stdout/stderr never holds a secret."""
    yield
    out, err = capsys.readouterr()
    for secret in (SSID, PASSWORD, KEY):
        assert secret not in out
        assert secret not in err


def ms():
    return time.ticks_ms()


# --- WiFi --------------------------------------------------------------------

def test_wifi_constructor_activates_the_radio():
    WiFi(SSID, PASSWORD)
    wlan = network.WLAN.last()
    assert wlan.interface == network.STA_IF
    assert ("active", True) in wlan.calls
    assert wlan.active() is True


def test_connect_succeeds_after_polls(vrun):
    network.WLAN.script_next([1, 1, 1, network.STAT_GOT_IP])
    w = WiFi(SSID, PASSWORD)
    t0 = ms()
    assert vrun(w.connect()) is True
    assert time.ticks_diff(ms(), t0) == 3 * 250      # three 250 ms waits
    wlan = network.WLAN.last()
    assert ("connect", SSID) in wlan.calls
    assert wlan.password_was(PASSWORD)
    assert w.is_connected() is True
    assert w.state == "up"


def test_connect_times_out_after_the_setting(vrun):
    network.WLAN.script_next([network.STAT_CONNECTING])
    w = WiFi(SSID, PASSWORD)
    t0 = ms()
    assert vrun(w.connect()) is False
    elapsed = time.ticks_diff(ms(), t0)
    assert settings.WIFI_CONNECT_TIMEOUT_S * 1000 <= elapsed < settings.WIFI_CONNECT_TIMEOUT_S * 1000 + 250
    assert w.state == "down"


def test_connect_timeout_argument(vrun):
    network.WLAN.script_next([network.STAT_CONNECTING])
    w = WiFi(SSID, PASSWORD)
    t0 = ms()
    assert vrun(w.connect(timeout_s=2)) is False
    assert 2000 <= time.ticks_diff(ms(), t0) < 2250


@pytest.mark.parametrize("status", [network.STAT_WRONG_PASSWORD,
                                    network.STAT_NO_AP_FOUND,
                                    network.STAT_CONNECT_FAIL])
def test_connect_fails_at_once_on_negative_status(vrun, status):
    network.WLAN.script_next([status])
    w = WiFi(SSID, "wrong-password")
    t0 = ms()
    assert vrun(w.connect()) is False
    assert time.ticks_diff(ms(), t0) == 0
    assert w.state == "down"


def test_connect_fails_when_status_turns_negative_later(vrun):
    network.WLAN.script_next([1, 1, network.STAT_WRONG_PASSWORD])
    w = WiFi(SSID, PASSWORD)
    t0 = ms()
    assert vrun(w.connect()) is False
    assert time.ticks_diff(ms(), t0) == 500


def test_state_connecting_up_down(vrun):
    network.WLAN.script_next([1, 1, 1, 1, network.STAT_GOT_IP])
    w = WiFi(SSID, PASSWORD)
    seen = []

    async def scenario():
        seen.append(w.state)
        task = asyncio.create_task(w.connect())
        await asyncio.sleep_ms(300)
        seen.append(w.state)
        result = await task
        seen.append(w.state)
        network.WLAN.last().set_status(network.STAT_IDLE)    # the link drops
        seen.append(w.state)
        return result

    assert vrun(scenario()) is True
    assert seen == ["down", "connecting", "up", "down"]


def test_connect_timeout_across_ticks_wrap(vrun, fake_clock):
    fake_clock.reset((TICKS_PERIOD_MS - 1000) * 1000)
    network.WLAN.script_next([network.STAT_CONNECTING])
    w = WiFi(SSID, PASSWORD)
    t0 = ms()
    assert vrun(w.connect()) is False
    assert ms() < t0                           # the ticks value wrapped
    limit_ms = settings.WIFI_CONNECT_TIMEOUT_S * 1000
    assert limit_ms <= time.ticks_diff(ms(), t0) < limit_ms + 250


def test_connect_float_timeout(vrun):
    network.WLAN.script_next([network.STAT_CONNECTING])
    w = WiFi(SSID, PASSWORD)
    t0 = ms()
    assert vrun(w.connect(timeout_s=0.5)) is False
    assert 500 <= time.ticks_diff(ms(), t0) < 750


def test_connect_radio_error_gives_false(vrun, monkeypatch):
    w = WiFi(SSID, PASSWORD)

    def broken(*args, **kwargs):
        raise OSError(5, "EIO")

    monkeypatch.setattr(network.WLAN.last(), "connect", broken)
    assert vrun(w.connect()) is False
    assert w.state == "down"


def test_disconnect(vrun):
    network.WLAN.script_next([network.STAT_GOT_IP])
    w = WiFi(SSID, PASSWORD)
    assert vrun(w.connect()) is True
    w.disconnect()
    assert ("disconnect",) in network.WLAN.last().calls
    assert w.is_connected() is False
    assert w.state == "down"


def test_cancelled_connect_is_not_left_connecting(vrun):
    network.WLAN.script_next([network.STAT_CONNECTING])
    w = WiFi(SSID, PASSWORD)

    async def scenario():
        task = asyncio.create_task(w.connect())
        await asyncio.sleep_ms(600)
        assert w.state == "connecting"
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return w.state

    assert vrun(scenario()) == "down"


def test_connect_awaits_instead_of_blocking(vrun):
    # A second task keeps running every 10 ms while connect() waits.
    network.WLAN.script_next([1] * 8 + [network.STAT_GOT_IP])
    w = WiFi(SSID, PASSWORD)
    ticks = []

    async def ticker():
        while True:
            ticks.append(ms())
            await asyncio.sleep_ms(10)

    async def scenario():
        t = asyncio.create_task(ticker())
        ok = await w.connect()
        t.cancel()
        return ok

    assert vrun(scenario()) is True
    gaps = [time.ticks_diff(b, a) for a, b in zip(ticks, ticks[1:])]
    assert max(gaps) == 10


# --- TimeSync ----------------------------------------------------------------

def synced_clock(at_ms=0, anchor=ANCHOR):
    """A TimeSync synced at ticks ``at_ms`` to ``anchor`` Unix seconds."""
    from vtime import CLOCK
    CLOCK.reset(at_ms * 1000)
    ntptime.script(anchor)
    clock = TimeSync()
    assert clock.sync() is True
    return clock


def test_sync_success():
    ntptime.script(ANCHOR)
    clock = TimeSync()
    assert clock.synced is False
    assert clock.age_s() is None
    assert clock.sync() is True
    assert clock.synced is True
    assert clock.age_s() == 0
    assert clock.unix(ms()) == ANCHOR
    assert ntptime.calls == 1


def test_sync_failure_keeps_unsynced():
    ntptime.script(OSError(110))
    clock = TimeSync()
    assert clock.sync() is False
    assert clock.synced is False
    assert clock.unix(ms()) is None
    assert clock.age_s() is None


def test_failed_resync_keeps_old_anchor(fake_clock):
    clock = synced_clock()
    fake_clock.advance_ms(10_000)
    ntptime.script(OSError(110))
    assert clock.sync() is False
    assert clock.synced is True
    assert clock.unix(ms()) == ANCHOR + 10
    assert clock.age_s() == 10


def test_anchor_is_taken_after_the_blocking_reply(fake_clock):
    ntptime.script(ANCHOR, delay_ms=700)     # the exchange blocks 700 ms
    clock = TimeSync()
    assert clock.sync() is True
    assert clock.unix(ms()) == ANCHOR
    assert clock.unix(time.ticks_add(ms(), -700)) == ANCHOR - 1   # (-700 + 500) // 1000


def test_unix_before_sync_is_none():
    assert TimeSync().unix(0) is None


def test_unix_rounding(fake_clock):
    clock = synced_clock(at_ms=10_000)
    t0 = 10_000
    assert clock.unix(t0 + 2400) == ANCHOR + 2
    assert clock.unix(t0 + 2499) == ANCHOR + 2
    assert clock.unix(t0 + 2500) == ANCHOR + 3
    assert clock.unix(t0 - 1200) == ANCHOR - 1
    assert clock.unix(t0 - 500) == ANCHOR
    assert clock.unix(t0 - 501) == ANCHOR - 1


def test_unix_across_ticks_wrap(fake_clock):
    start = TICKS_PERIOD_MS - 1000
    clock = synced_clock(at_ms=start)
    fake_clock.advance_ms(2500)
    assert ms() < start                        # the ticks value wrapped
    assert clock.unix(ms()) == ANCHOR + 3
    assert clock.unix(time.ticks_add(start, -1200)) == ANCHOR - 1


def test_age_over_seven_days(fake_clock):
    # 7 days is past ticks_diff's 6.2-day range; the running total still works.
    clock = synced_clock(at_ms=TICKS_PERIOD_MS - 5000)
    step_s = 20
    for i in range(1, 7 * 86_400 // step_s + 1):
        fake_clock.advance_ms(step_s * 1000)
        assert clock.age_s() == i * step_s
    assert clock.synced is False


def test_synced_expires_after_24_hours_and_returns(fake_clock):
    clock = synced_clock()
    limit_s = 4 * settings.NTP_RESYNC_S
    assert limit_s == 86_400
    fake_clock.advance_ms((limit_s - 1) * 1000)
    assert clock.synced is True
    fake_clock.advance_ms(1000)
    assert clock.synced is False
    assert clock.unix(ms()) is not None         # unix() still answers
    ntptime.script(ANCHOR + limit_s)
    assert clock.sync() is True
    assert clock.synced is True
    assert clock.age_s() == 0


def test_epoch_1970():
    assert time.gmtime(0)[0] == 1970
    ntptime.script(ANCHOR)
    clock = TimeSync()
    clock.sync()
    assert clock.unix(ms()) == ANCHOR


def test_epoch_2000(monkeypatch):
    real = time.gmtime

    def gmtime_2000(secs=None):
        if secs == 0:
            return (2000, 1, 1, 0, 0, 0, 5, 1)
        return real(secs)

    monkeypatch.setattr(time, "gmtime", gmtime_2000)
    ntptime.script(ANCHOR - 946_684_800)        # seconds since 2000-01-01
    clock = TimeSync()
    clock.sync()
    assert clock.unix(ms()) == ANCHOR


def test_stamp_matches_cpython_strftime():
    rng = random.Random(8)
    lo = 1_704_067_200     # 2024-01-01
    hi = 2_240_611_200     # 2041-01-01
    for _ in range(10_000):
        x = rng.randrange(lo, hi)
        assert TimeSync.stamp(x) == time.strftime("%Y-%m-%d %H:%M:%S +0000", time.gmtime(x))


@pytest.mark.parametrize("x, text", [
    (0, "1970-01-01 00:00:00 +0000"),
    (951_782_400, "2000-02-29 00:00:00 +0000"),
    (1_709_251_199, "2024-02-29 23:59:59 +0000"),
    (1_798_761_599, "2026-12-31 23:59:59 +0000"),
])
def test_stamp_known_values(x, text):
    assert TimeSync.stamp(x) == text


def test_stamp_is_integer_only(monkeypatch):
    def refuse(*args):
        raise AssertionError("stamp() must not use time.gmtime")
    monkeypatch.setattr(time, "gmtime", refuse)
    assert TimeSync.stamp(ANCHOR) == "2027-01-15 08:00:00 +0000"


# --- make_updates ------------------------------------------------------------

def ev(t_ms, kind, mode="RUN", d=0.5, d1=0.5, d2=None, zone="medium",
       closest=None, duration_s=None):
    """An event tuple as logic.EventDetector makes it."""
    return (t_ms, kind, mode, d, d1, d2, zone, closest, duration_s)


FAR_NOW = 600_000    # every event below is long settled at this time


def test_unsynced_gives_nothing():
    clock = TimeSync()
    assert make_updates([ev(1000, SAMPLE)], clock, FAR_NOW) == ([], 0, None)


def test_empty_queue():
    clock = synced_clock()
    assert make_updates([], clock, FAR_NOW) == ([], 0, None)


def test_same_second_merges_with_priority_and_latest_values():
    clock = synced_clock()
    events = [
        ev(1100, ZONE, d=0.95, d1=0.95, d2=1.20, zone="far"),
        ev(1300, ENTER, d=0.80, d1=0.80, d2=None, zone="medium"),
    ]
    updates, n, last = make_updates(events, clock, FAR_NOW)
    assert n == 2
    assert last == ANCHOR + 1
    assert updates == [{
        "created_at": TimeSync.stamp(ANCHOR + 1),
        "field1": "0.80",
        "field2": "0.80",
        "field4": 2,
        "field5": ENTER,
        "status": "ZONE far; ENTER",
    }]


@pytest.mark.parametrize("kinds, best", [
    ((SAMPLE, ZONE), ZONE),
    ((ZONE, MODE), MODE),
    ((MODE, ENTER), ENTER),
    ((ENTER, LEAVE), LEAVE),
    ((LEAVE, SAMPLE, ZONE, MODE, ENTER), LEAVE),
    ((SAMPLE,), SAMPLE),
])
def test_priority_rule(kinds, best):
    clock = synced_clock()
    events = [ev(1000 + 10 * i, k, closest=0.3, duration_s=4) for i, k in enumerate(kinds)]
    updates, n, _ = make_updates(events, clock, FAR_NOW)
    assert n == len(kinds)
    assert len(updates) == 1
    assert updates[0]["field5"] == best


def test_field_mapping_and_none_left_out():
    clock = synced_clock()
    events = [
        ev(1000, SAMPLE, d=1.234, d1=1.234, d2=2.5, zone="far"),
        ev(2000, SAMPLE, d=None, d1=None, d2=None, zone="clear"),
    ]
    updates, _, _ = make_updates(events, clock, FAR_NOW)
    assert updates[0] == {
        "created_at": TimeSync.stamp(ANCHOR + 1),
        "field1": "1.23", "field2": "1.23", "field3": "2.50",
        "field4": 1, "field5": SAMPLE, "status": "SAMPLE",
    }
    assert updates[1] == {
        "created_at": TimeSync.stamp(ANCHOR + 2),
        "field4": 0, "field5": SAMPLE, "status": "SAMPLE",
    }


def test_leave_fields_only_with_a_leave():
    clock = synced_clock()
    events = [
        ev(1000, ENTER, d=0.4, zone="medium"),
        ev(2000, LEAVE, d=None, d1=None, zone="clear", closest=0.314, duration_s=12),
        ev(3000, ZONE, d=None, d1=None, zone="clear"),
    ]
    updates, n, _ = make_updates(events, clock, FAR_NOW)
    assert n == 3
    assert "field6" not in updates[0] and "field7" not in updates[0]
    assert updates[1]["field6"] == "0.31"
    assert updates[1]["field7"] == 12
    assert updates[1]["field5"] == LEAVE
    assert updates[1]["status"] == "LEAVE 12 s 0.31 m"
    assert "field6" not in updates[2] and "field7" not in updates[2]


def test_status_names_every_event():
    clock = synced_clock()
    events = [
        ev(1000, ZONE, zone="clear", d=None, d1=None),
        ev(1100, LEAVE, zone="clear", d=None, d1=None, closest=0.31, duration_s=12),
        ev(1200, MODE, mode="OFF", zone="clear", d=None, d1=None),
        ev(1300, SAMPLE, mode="OFF", zone="clear", d=None, d1=None),
        ev(1400, ENTER),
    ]
    updates, _, _ = make_updates(events, clock, FAR_NOW)
    assert updates[0]["status"] == "ZONE clear; LEAVE 12 s 0.31 m; MODE OFF; SAMPLE; ENTER"
    assert updates[0]["field4"] == 2       # the latest event's zone (ENTER, medium)
    assert updates[0]["field6"] == "0.31"


def test_mode_status():
    clock = synced_clock()
    updates, _, _ = make_updates([ev(1000, MODE, mode="OFF", d=None, d1=None, zone="clear")],
                                 clock, FAR_NOW)
    assert updates[0]["status"] == "MODE OFF"
    assert updates[0]["field5"] == MODE


def test_status_is_cut_to_255_characters():
    clock = synced_clock()
    events = [ev(1000 + i, ZONE, zone="medium") for i in range(40)]
    updates, n, _ = make_updates(events, clock, FAR_NOW)
    assert n == 40
    assert len(updates[0]["status"]) == 255
    assert updates[0]["status"].startswith("ZONE medium; ZONE medium")


def test_only_settled_seconds_and_straddling_second_waits_whole():
    clock = synced_clock()
    hold = settings.LEAVE_HOLD_MS + 2000
    now = 5000 + hold                   # the settle limit is ticks 5000 -> ANCHOR + 5
    events = [
        ev(3000, SAMPLE),               # ANCHOR + 3: settled
        ev(4600, ZONE, zone="far"),     # ANCHOR + 5, before the limit instant
        ev(5400, ENTER),                # ANCHOR + 5, after it
        ev(7000, SAMPLE),               # ANCHOR + 7
    ]
    updates, n, last = make_updates(events, clock, now)
    assert n == 1
    assert last == ANCHOR + 3
    assert [u["created_at"] for u in updates] == [TimeSync.stamp(ANCHOR + 3)]
    # One second later the whole of ANCHOR + 5 goes in together.
    updates, n, last = make_updates(events, clock, now + 1000)
    assert n == 3
    assert last == ANCHOR + 5
    assert updates[1]["status"] == "ZONE far; ENTER"


def test_nothing_settled_yet():
    clock = synced_clock()
    now = 1000 + settings.LEAVE_HOLD_MS + 2000
    assert make_updates([ev(900, SAMPLE), ev(1200, SAMPLE)], clock, now) == ([], 0, None)


def test_max_updates_never_splits_a_second():
    clock = synced_clock()
    events = [
        ev(1000, SAMPLE),
        ev(2000, ZONE), ev(2100, ENTER),
        ev(3000, ZONE), ev(3100, ZONE), ev(3200, ZONE),
        ev(4000, SAMPLE),
    ]
    updates, n, last = make_updates(events, clock, FAR_NOW, max_updates=3)
    assert len(updates) == 3
    assert n == 6                       # all of ANCHOR + 3, nothing of + 4
    assert last == ANCHOR + 3
    updates, n, last = make_updates(events, clock, FAR_NOW, max_updates=2)
    assert (len(updates), n, last) == (2, 3, ANCHOR + 2)


def test_max_updates_default_is_the_setting(monkeypatch):
    clock = synced_clock()
    monkeypatch.setattr(settings, "UPLOAD_BATCH_MAX", 2)
    events = [ev(1000 * i, SAMPLE) for i in range(1, 6)]
    updates, n, _ = make_updates(events, clock, FAR_NOW)
    assert (len(updates), n) == (2, 2)


def test_after_s_raises_and_merges():
    clock = synced_clock()
    events = [
        ev(1000, SAMPLE),               # ANCHOR + 1 -> raised to + 4
        ev(3000, ZONE, zone="far"),     # ANCHOR + 3 -> raised to + 4
        ev(4000, ENTER),                # ANCHOR + 4
        ev(5000, SAMPLE),               # ANCHOR + 5
    ]
    updates, n, last = make_updates(events, clock, FAR_NOW, after_s=ANCHOR + 3)
    assert n == 4
    assert last == ANCHOR + 5
    assert [u["created_at"] for u in updates] == [TimeSync.stamp(ANCHOR + 4),
                                                   TimeSync.stamp(ANCHOR + 5)]
    assert updates[0]["status"] == "SAMPLE; ZONE far; ENTER"
    assert updates[0]["field5"] == ENTER


def test_created_at_unique_and_increasing():
    clock = synced_clock()
    rng = random.Random(3)
    t = 0
    events = []
    for _ in range(300):
        t += rng.choice((0, 50, 300, 900, 1000, 2500))
        events.append(ev(t, rng.choice((SAMPLE, ZONE, ENTER, LEAVE, MODE)),
                         closest=0.2, duration_s=3))
    updates, n, last = make_updates(events, clock, t + 100_000, after_s=ANCHOR + 10,
                                    max_updates=960)
    assert n == len(events)
    seconds = [clock.unix(e[0]) for e in events]
    stamps = [u["created_at"] for u in updates]
    assert stamps == sorted(set(stamps))
    assert stamps[0] == TimeSync.stamp(max(ANCHOR + 11, seconds[0]))
    assert stamps[-1] == TimeSync.stamp(last)
    json.dumps(updates)                 # serialisable as it stands


def test_out_of_order_event_merges_into_the_previous_second():
    clock = synced_clock()
    events = [ev(3000, SAMPLE), ev(2000, ZONE)]
    updates, n, last = make_updates(events, clock, FAR_NOW)
    assert (len(updates), n, last) == (1, 2, ANCHOR + 3)


def test_make_updates_does_not_change_its_input():
    clock = synced_clock()
    events = [ev(1000, SAMPLE), ev(1500, ZONE)]
    copy = list(events)
    make_updates(events, clock, FAR_NOW)
    assert events == copy


def test_make_updates_across_ticks_wrap(fake_clock):
    start = TICKS_PERIOD_MS - 2000
    clock = synced_clock(at_ms=start)
    events = [ev(time.ticks_add(start, 1000), SAMPLE), ev(time.ticks_add(start, 3000), ZONE)]
    now = time.ticks_add(start, 60_000)
    updates, n, last = make_updates(events, clock, now)
    assert (n, last) == (2, ANCHOR + 3)
    assert updates[0]["created_at"] == TimeSync.stamp(ANCHOR + 1)


# --- ThingSpeak: resolve -----------------------------------------------------

def make_ts(monkeypatch, *addr, timeout_s=None):
    gai = FakeGetaddrinfo(*(addr or (IP,)))
    monkeypatch.setattr(socket, "getaddrinfo", gai)
    return ThingSpeak(KEY, CHANNEL, timeout_s=timeout_s), gai


def test_resolve_caches_the_ip(monkeypatch):
    ts, gai = make_ts(monkeypatch)
    assert ts.resolved is False
    assert ts.resolve() is True
    assert ts.resolved is True
    assert gai.calls == [(settings.THINGSPEAK_HOST, settings.THINGSPEAK_PORT)]


def test_failing_resolve(monkeypatch):
    ts, _ = make_ts(monkeypatch, IP, OSError(-2))
    assert ts.resolve() is True
    assert ts.resolve() is False
    assert ts.resolved is False


def test_resolve_with_odd_reply_is_false(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [])
    ts = ThingSpeak(KEY, CHANNEL)
    assert ts.resolve() is False
    assert ts.resolved is False


def test_resolve_custom_host_and_port(monkeypatch):
    gai = FakeGetaddrinfo(IP)
    monkeypatch.setattr(socket, "getaddrinfo", gai)
    ts = ThingSpeak(KEY, CHANNEL, host="example.test", port=8080)
    assert ts.resolve() is True
    assert gai.calls == [("example.test", 8080)]


def test_bulk_post_without_ip_opens_nothing(monkeypatch, vrun):
    ts, _ = make_ts(monkeypatch)
    opener = FakeOpenConnection(FakeStream([b"HTTP/1.0 202 Accepted\r\n\r\n{\"success\": true}"]))
    monkeypatch.setattr(asyncio, "open_connection", opener)
    assert vrun(ts.bulk_post([])) is None
    assert opener.calls == []


def test_bulk_post_after_failed_resolve_opens_nothing(monkeypatch, vrun):
    ts, _ = make_ts(monkeypatch, OSError(-2))
    opener = FakeOpenConnection(FakeStream([]))
    monkeypatch.setattr(asyncio, "open_connection", opener)
    assert ts.resolve() is False
    assert vrun(ts.bulk_post([])) is None
    assert opener.calls == []


# --- ThingSpeak: bulk_post ---------------------------------------------------

UPDATES = [
    {"created_at": "2027-01-15 08:00:01 +0000", "field1": "0.80", "field4": 2,
     "field5": 2, "status": "ZONE far; ENTER"},
    {"created_at": "2027-01-15 08:00:05 +0000", "field4": 0, "field5": 3,
     "field6": "0.31", "field7": 12, "status": "LEAVE 12 s 0.31 m"},
]

OK_202 = b"HTTP/1.1 202 Accepted\r\nContent-Type: application/json\r\n\r\n{\"success\":true}"


def post(monkeypatch, vrun, *results, timeout_s=None, delay_ms=0):
    """Resolve, then bulk_post UPDATES once; return (result, opener, ts)."""
    ts, _ = make_ts(monkeypatch, timeout_s=timeout_s)
    assert ts.resolve() is True
    opener = FakeOpenConnection(*results, delay_ms=delay_ms)
    monkeypatch.setattr(asyncio, "open_connection", opener)
    result = vrun(ts.bulk_post(UPDATES))
    return result, opener, ts


def test_request_bytes_exact(monkeypatch, vrun):
    stream = FakeStream([OK_202])
    result, opener, _ = post(monkeypatch, vrun, stream)
    assert result is True
    assert opener.calls == [(IP, 80)]
    head, sep, body = bytes(stream.written).partition(b"\r\n\r\n")
    assert sep
    assert head == (b"POST /channels/1234567/bulk_update.json HTTP/1.0\r\n"
                    b"Host: api.thingspeak.com\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Content-Length: " + str(len(body)).encode())
    assert json.loads(body.decode("utf-8")) == {"write_api_key": KEY, "updates": UPDATES}
    assert stream.drained >= 1


def test_content_length_counts_bytes(monkeypatch, vrun):
    # MicroPython's json.dumps keeps non-ASCII characters, so the length must
    # be counted after encoding. Simulate that here.
    real_dumps = json.dumps
    monkeypatch.setattr(thingspeak.json, "dumps",
                        lambda obj: real_dumps(obj, ensure_ascii=False))
    stream = FakeStream([OK_202])
    ts, _ = make_ts(monkeypatch)
    ts.resolve()
    monkeypatch.setattr(asyncio, "open_connection", FakeOpenConnection(stream))
    vrun(ts.bulk_post([{"created_at": "x", "status": "20 °C"}]))
    head, _, body = bytes(stream.written).partition(b"\r\n\r\n")
    assert b"Content-Length: %d" % len(body) in head
    assert len(body) > len(body.decode("utf-8"))


@pytest.mark.parametrize("response, expected", [
    (OK_202, True),
    (b"HTTP/1.1 200 OK\r\n\r\n{\"success\": true}", True),
    (b"HTTP/1.1 202 Accepted\r\n\r\n{\"success\": false}", False),
    (b"HTTP/1.1 202 Accepted\r\n\r\nnot json", False),
    (b"HTTP/1.1 202 Accepted\r\n\r\n", False),
    (b"HTTP/1.1 202 Accepted\r\n\r\n[true]", False),
    (b"HTTP/1.1 202 Accepted\r\n\r\n{\"success\": \"true\"}", False),
    (b"HTTP/1.1 202 Accepted\r\n\r\n\xff\xfe", False),
    (b"HTTP/1.1 400 Bad Request\r\n\r\n{\"status\": \"400\"}", False),
    (b"HTTP/1.1 401 Unauthorized\r\n\r\n{\"status\": \"401\"}", False),
    (b"HTTP/1.1 500 Internal Server Error\r\n\r\n", None),
    (b"HTTP/1.1 302 Found\r\n\r\n", None),
    (b"", None),
    (b"hello", None),
    (b"HTTP/1.1 abc\r\n\r\n", None),
])
def test_response_results(monkeypatch, vrun, response, expected):
    stream = FakeStream([response] if response else [])
    result, _, _ = post(monkeypatch, vrun, stream)
    assert result is expected
    assert stream.closed is True


def test_response_in_two_chunks(monkeypatch, vrun):
    stream = FakeStream([b"HTTP/1.1 202 Accepted\r\nContent-Type: applic",
                         (30, b"ation/json\r\n\r\n{\"succ"), b"ess\": true}"])
    result, _, ts = post(monkeypatch, vrun, stream)
    assert result is True
    assert ts._last_status == 202


def test_response_with_long_headers(monkeypatch, vrun):
    headers = b"".join(b"X-Pad-%02d: %s\r\n" % (i, b"a" * 90) for i in range(15))
    assert len(headers) > 1500
    stream = FakeStream([b"HTTP/1.1 202 Accepted\r\n" + headers + b"\r\n{\"success\": true}"])
    result, _, _ = post(monkeypatch, vrun, stream)
    assert result is True


def test_response_is_read_to_eof_but_capped(monkeypatch, vrun):
    big = OK_202 + b" " * 6000
    stream = FakeStream([big[i:i + 1000] for i in range(0, len(big), 1000)])
    result, _, ts = post(monkeypatch, vrun, stream)
    assert result is True
    assert ts._last_len == len(big)
    assert vrun(stream.read()) == b""   # every chunk was read


def test_exchange_keeps_at_most_4096_bytes(monkeypatch, vrun):
    ts, _ = make_ts(monkeypatch)
    ts.resolve()
    stream = FakeStream([b"x" * 3000, b"y" * 3000])
    monkeypatch.setattr(asyncio, "open_connection", FakeOpenConnection(stream))
    kept = vrun(ts._exchange(b"GET / HTTP/1.0\r\n\r\n", b""))
    assert len(kept) == 4096
    assert kept == b"x" * 3000 + b"y" * 1096


def test_never_answering_times_out(monkeypatch, vrun):
    stream = FakeStream(never_answers=True)
    t0 = ms()
    result, _, _ = post(monkeypatch, vrun, stream)
    assert result is None
    assert time.ticks_diff(ms(), t0) == settings.HTTP_TIMEOUT_S * 1000
    assert stream.closed is True


def test_timeout_covers_the_whole_exchange(monkeypatch, vrun):
    # A slow connect plus a slow answer together pass the limit.
    stream = FakeStream([(3000, OK_202)])
    t0 = ms()
    result, _, _ = post(monkeypatch, vrun, stream, delay_ms=3000)
    assert result is None
    assert time.ticks_diff(ms(), t0) == settings.HTTP_TIMEOUT_S * 1000
    assert stream.closed is True


def test_timeout_argument(monkeypatch, vrun):
    stream = FakeStream(never_answers=True)
    t0 = ms()
    result, _, _ = post(monkeypatch, vrun, stream, timeout_s=2)
    assert result is None
    assert time.ticks_diff(ms(), t0) == 2000


def test_float_timeout_argument(monkeypatch, vrun):
    stream = FakeStream(never_answers=True)
    t0 = ms()
    result, _, _ = post(monkeypatch, vrun, stream, timeout_s=1.5)
    assert result is None
    assert time.ticks_diff(ms(), t0) == 1500


def test_read_error_gives_none_and_closes(monkeypatch, vrun):
    stream = FakeStream([b"HTTP/1.1 20", OSError(104, "ECONNRESET")])
    result, _, _ = post(monkeypatch, vrun, stream)
    assert result is None
    assert stream.closed is True


def test_connect_error_gives_none(monkeypatch, vrun):
    result, opener, _ = post(monkeypatch, vrun, OSError(113, "EHOSTUNREACH"))
    assert result is None
    assert opener.streams == []


@pytest.mark.parametrize("response, expected", [
    (OK_202, True),
    (b"HTTP/1.1 401 Unauthorized\r\n\r\n{}", False),
    (b"HTTP/1.1 500 Internal Server Error\r\n\r\n", None),
])
def test_wait_closed_awaited_on_every_result(monkeypatch, vrun, response, expected):
    stream = FakeStream([response])
    result, _, _ = post(monkeypatch, vrun, stream)
    assert result is expected
    assert stream.close_called is True
    assert stream.closed is True


def test_wait_closed_error_is_ignored(monkeypatch, vrun):
    class BadClose(FakeStream):
        async def wait_closed(self):
            await super().wait_closed()
            raise OSError(9, "EBADF")

    stream = BadClose([OK_202])
    result, _, _ = post(monkeypatch, vrun, stream)
    assert result is True
    assert stream.closed is True


def test_resend_after_reconnect_uses_a_new_connection(monkeypatch, vrun):
    ts, _ = make_ts(monkeypatch)
    ts.resolve()
    opener = FakeOpenConnection(lambda: FakeStream([OK_202]))
    monkeypatch.setattr(asyncio, "open_connection", opener)
    assert vrun(ts.bulk_post(UPDATES)) is True
    assert vrun(ts.bulk_post(UPDATES)) is True
    assert len(opener.streams) == 2
    assert opener.streams[0].written == opener.streams[1].written
    assert all(s.closed for s in opener.streams)


def test_bulk_post_lets_other_tasks_run(monkeypatch, vrun):
    ts, _ = make_ts(monkeypatch)
    ts.resolve()
    stream = FakeStream([(400, OK_202)])
    monkeypatch.setattr(asyncio, "open_connection", FakeOpenConnection(stream, delay_ms=200))
    ticks = []

    async def ticker():
        while True:
            ticks.append(ms())
            await asyncio.sleep_ms(10)

    async def scenario():
        t = asyncio.create_task(ticker())
        result = await ts.bulk_post(UPDATES)
        t.cancel()
        return result

    assert vrun(scenario()) is True
    gaps = [time.ticks_diff(b, a) for a, b in zip(ticks, ticks[1:])]
    assert len(ticks) >= 50 and max(gaps) == 10


def test_no_module_prints(monkeypatch, vrun, capsys):
    # Exercise every path once; the autouse fixture checks the secrets, and
    # here nothing at all may be printed.
    network.WLAN.script_next([1, network.STAT_GOT_IP])
    w = WiFi(SSID, PASSWORD)
    vrun(w.connect())
    ntptime.script(ANCHOR)
    TimeSync().sync()
    post(monkeypatch, vrun, FakeStream([OK_202]))
    post(monkeypatch, vrun, FakeStream(never_answers=True))
    out, err = capsys.readouterr()
    assert out == "" and err == ""
