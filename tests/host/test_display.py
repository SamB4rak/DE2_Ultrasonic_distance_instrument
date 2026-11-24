"""Host tests for src/display.py (the OLED display)."""

import asyncio
import inspect
import time

import pytest

import board
import machine
import settings

import display

ADDR = board.ADDR_OLED


class View:
    """A stand-in for the shared state with the attributes the display reads."""

    def __init__(self, **kw):
        self.mode = "RUN"
        self.d1 = 0.42
        self.d2 = 1.30
        self.d = 0.42
        self.zone = "medium"
        self.wifi = "up"
        self.faults = set()
        self.last_event = "ENTER"
        self.events = [1, 2, 3]
        self.synced = True
        self.cloud = "ok"
        for k, v in kw.items():
            setattr(self, k, v)


@pytest.fixture
def bus():
    """I2C bus 0 with an OLED attached at its address."""
    i2c = machine.I2C(board.I2C_ID)
    dev = machine.I2CDevice()
    i2c.attach(ADDR, dev)
    i2c.dev = dev
    return i2c


def data_writes(i2c, start=0):
    """Page data payloads (after the 0x40 control byte) from the I2C log."""
    out = []
    for entry in i2c.log[start:]:
        if entry[0] in ("writeto", "writevto") and entry[2][:1] == b"\x40":
            out.append(entry[2][1:])
    return out


def commands(i2c, start=0):
    """Command bytes (sent as 0x80, cmd) from the I2C log."""
    return [e[2][1] for e in i2c.log[start:]
            if e[0] == "writeto" and len(e[2]) == 2 and e[2][0] == 0x80]


def pages_sent(i2c, start=0):
    """Page numbers sent (SH1106: the 0xB0|page command before each data)."""
    pages = []
    page = None
    for e in i2c.log[start:]:
        data = e[2] if e[0] in ("writeto", "writevto") else b""
        if len(data) == 2 and data[0] == 0x80 and data[1] & 0xF0 == 0xB0:
            page = data[1] & 0x0F
        elif data[:1] == b"\x40":
            pages.append(page)
    return pages


def rows(disp):
    """Text drawn since the last fill, as {row: text}."""
    out = {}
    for s, x, y in disp._fb.texts:
        assert x == 0 and y % 8 == 0
        out[y // 8] = s
    return out


def pixel(disp, x, y):
    return disp._fb.pixel(x, y)


def make(bus, **kw):
    d = display.Display(bus, **kw)
    return d


# --- construction ----------------------------------------------------------------

def test_starts_ok_with_device(bus):
    d = make(bus)
    assert d.ok is True
    assert data_writes(bus) == []          # start sends no frame, only commands
    assert 0xAF in commands(bus)           # display on


def test_defaults_come_from_board_and_settings(bus, monkeypatch):
    d = make(bus)
    assert isinstance(d._drv, display._SH1106)
    assert d._drv.addr == board.ADDR_OLED
    assert settings.OLED_CONTROLLER == "sh1106"
    monkeypatch.setattr(settings, "OLED_CONTROLLER", "ssd1306")
    assert isinstance(make(bus)._drv, display._SSD1306)


def test_unknown_controller_raises(bus):
    with pytest.raises(ValueError):
        display.Display(bus, controller="st7735")


def test_missing_device_gives_not_ok_and_no_exceptions(vrun):
    i2c = machine.I2C(board.I2C_ID)       # nothing attached
    d = display.Display(i2c)
    assert d.ok is False
    d.render(View())
    d.message("boot", "line 2")
    vrun(d.flush())
    d.show_now()
    assert d.ok is False
    assert i2c.log == []


def test_other_address(bus):
    d = display.Display(bus, addr=0x3D)
    assert d.ok is False                  # nothing at 0x3D


# --- render has no I2C traffic -----------------------------------------------------

def test_render_and_message_make_no_i2c_calls(bus):
    d = make(bus)
    n = len(bus.log)
    d.render(View())
    d.render(View(d1=None, faults={"S1", "S2", "OLED"}, mode="OFF"))
    d.message("Booting", "please wait")
    assert len(bus.log) == n


# --- flush ---------------------------------------------------------------------------

def test_flush_writes_8_pages_in_order(bus, vrun):
    d = make(bus)
    d.render(View())
    n = len(bus.log)
    vrun(d.flush())
    assert pages_sent(bus, n) == list(range(8))
    for payload in data_writes(bus, n):
        assert len(payload) == 128


def test_sh1106_page_commands(bus, vrun):
    d = make(bus)
    n = len(bus.log)
    vrun(d.flush())
    cmds = commands(bus, n)
    expect = []
    for p in range(8):
        expect += [0xB0 | p, 0x02, 0x10]
    assert cmds == expect


def test_page_bytes_match_framebuffer(bus, vrun):
    d = make(bus)
    d.render(View())
    n = len(bus.log)
    vrun(d.flush())
    assert b"".join(data_writes(bus, n)) == bytes(d._buf)


def test_flush_yields_between_pages(bus, vrun):
    d = make(bus)
    d.render(View())
    seen = []

    async def observer(n0):
        while True:
            seen.append(len(data_writes(bus, n0)))
            await asyncio.sleep_ms(0)

    async def main():
        n0 = len(bus.log)
        t = asyncio.create_task(observer(n0))
        await asyncio.sleep_ms(0)
        await d.flush()
        await asyncio.sleep_ms(0)
        t.cancel()

    vrun(main())
    # The observer ran after every single page, so it saw each count.
    for k in range(1, 8):
        assert k in seen


def test_second_flush_unchanged_writes_nothing(bus, vrun):
    d = make(bus)
    d.render(View())
    vrun(d.flush())
    n = len(bus.log)
    d.render(View())
    vrun(d.flush())
    assert len(bus.log) == n


def test_swapped_digits_resend_that_page(bus, vrun):
    d = make(bus)
    d.render(View(d1=0.42))
    vrun(d.flush())
    n = len(bus.log)
    d.render(View(d1=0.24))
    vrun(d.flush())
    assert pages_sent(bus, n) == [1]


def test_only_changed_pages_are_sent(bus, vrun):
    d = make(bus)
    d.render(View())
    vrun(d.flush())
    n = len(bus.log)
    d.render(View(cloud="rej", faults={"S2"}, d2=None))
    vrun(d.flush())
    # Row 2 shows R FAULT, row 6 Up rej, row 7 FAULT R.
    assert pages_sent(bus, n) == [2, 6, 7]


def test_oserror_during_flush_gives_not_ok(bus, vrun):
    d = make(bus)
    d.render(View())
    bus.fail_after(5)                     # fails during the second page
    vrun(d.flush())
    assert d.ok is False


def test_pages_sent_before_failure_are_sent_again(bus, vrun, fake_clock):
    d = make(bus)
    d.render(View())
    bus.fail_after(8)                     # pages 0 and 1, then failure
    vrun(d.flush())
    assert d.ok is False
    bus.fail_after(None)
    fake_clock.advance_ms(settings.DISPLAY_RETRY_MS)
    n = len(bus.log)
    vrun(d.flush())
    assert d.ok is True
    assert pages_sent(bus, n) == list(range(8))


# --- retry -----------------------------------------------------------------------------

def test_retry_after_display_retry_ms(vrun, fake_clock):
    i2c = machine.I2C(board.I2C_ID)
    d = display.Display(i2c)              # missing at t = 0
    assert d.ok is False
    d.render(View())
    i2c.attach(ADDR, machine.I2CDevice())
    fake_clock.advance_ms(settings.DISPLAY_RETRY_MS - 1)
    vrun(d.flush())
    assert d.ok is False
    assert i2c.log == []                  # no retry before the wait
    fake_clock.advance_ms(1)
    vrun(d.flush())
    assert d.ok is True
    assert pages_sent(i2c) == list(range(8))


def test_failed_retry_waits_a_full_period_again(vrun, fake_clock):
    i2c = machine.I2C(board.I2C_ID)
    d = display.Display(i2c)
    fake_clock.advance_ms(settings.DISPLAY_RETRY_MS)
    vrun(d.flush())                       # retry fails: still missing
    assert d.ok is False
    i2c.attach(ADDR, machine.I2CDevice())
    fake_clock.advance_ms(settings.DISPLAY_RETRY_MS - 1)
    vrun(d.flush())
    assert d.ok is False and i2c.log == []
    fake_clock.advance_ms(1)
    vrun(d.flush())
    assert d.ok is True


def test_retry_after_failure_in_flush(bus, vrun, fake_clock):
    d = make(bus)
    d.render(View())
    bus.detach(ADDR)
    vrun(d.flush())
    assert d.ok is False
    bus.attach(ADDR, machine.I2CDevice())
    fake_clock.advance_ms(settings.DISPLAY_RETRY_MS - 1)
    n = len(bus.log)
    vrun(d.flush())
    assert len(bus.log) == n and d.ok is False
    fake_clock.advance_ms(1)
    vrun(d.flush())
    assert d.ok is True
    assert pages_sent(bus, n) == list(range(8))


def test_retry_across_ticks_wraparound(vrun, fake_clock):
    fake_clock.reset((2 ** 30 - 500) * 1000)   # ticks_ms wraps in 500 ms
    i2c = machine.I2C(board.I2C_ID)
    d = display.Display(i2c)
    i2c.attach(ADDR, machine.I2CDevice())
    fake_clock.advance_ms(settings.DISPLAY_RETRY_MS - 1)
    vrun(d.flush())
    assert d.ok is False
    fake_clock.advance_ms(1)
    vrun(d.flush())
    assert d.ok is True


# --- show_now -----------------------------------------------------------------------------

def test_show_now_writes_8_pages_without_awaiting(bus):
    d = make(bus)
    assert not inspect.iscoroutinefunction(d.show_now)
    d.message("Booting")
    n = len(bus.log)
    assert d.show_now() is None
    assert pages_sent(bus, n) == list(range(8))


def test_show_now_then_flush_sends_nothing(bus, vrun):
    d = make(bus)
    d.message("Booting")
    d.show_now()
    n = len(bus.log)
    vrun(d.flush())
    assert len(bus.log) == n


def test_show_now_never_raises(bus):
    d = make(bus)
    bus.fail_after(3)
    d.show_now()
    assert d.ok is False


def test_show_now_never_raises_on_unexpected_error(bus, monkeypatch):
    d = make(bus)

    def boom(page, data):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(d._drv, "send_page", boom)
    d.show_now()
    assert d.ok is False


# --- layout ---------------------------------------------------------------------------------

def test_layout_normal(bus):
    d = make(bus)
    d.render(View())
    assert rows(d) == {
        0: "RUN   WiFi:up",
        1: "L 0.42 m",
        2: "R 1.30 m",
        4: "Last ENTER",
        5: "Queue 3",
        6: "Clock ok  Up ok",
    }


def test_layout_off(bus):
    d = make(bus)
    d.render(View(mode="OFF", d1=None, d2=None, d=None, zone="clear",
                  wifi="down", last_event="MODE", events=[], synced=False,
                  cloud="--"))
    assert rows(d) == {
        0: "OFF   WiFi:--",
        1: "L --",
        2: "R --",
        4: "Last MODE",
        5: "Queue 0",
        6: "Clock --  Up --",
    }


def test_layout_faults(bus):
    d = make(bus)
    d.render(View(d1=None, d2=0.95, d=0.95, faults={"S1", "OLED"},
                  wifi="connecting", last_event=None, events=[0] * 12,
                  cloud="rej"))
    assert rows(d) == {
        0: "RUN   WiFi:..",
        1: "L FAULT",
        2: "R 0.95 m",
        5: "Queue 12",
        6: "Clock ok  Up rej",
        7: "FAULT L",
    }


def test_every_row_fits_16_characters(bus):
    d = make(bus)
    d.render(View(faults={"S1", "S2"}, last_event="SAMPLE", cloud="rej",
                  events=list(range(100)), d1=3.99, d2=3.99))
    for text in rows(d).values():
        assert len(text) <= 16


def test_none_values_show_dashes(bus):
    d = make(bus)
    d.render(View(d1=None, d2=None, d=None))
    r = rows(d)
    assert r[1] == "L --" and r[2] == "R --"


def test_unknown_values_show_dashes(bus):
    d = make(bus)
    d.render(View(mode=None, wifi="weird", synced=None, cloud=None,
                  events=None))
    r = rows(d)
    assert r[0] == "--    WiFi:--"
    assert r[5] == "Queue --"
    assert r[6] == "Clock --  Up --"


def test_faults_fixed_order(bus):
    d = make(bus)
    d.render(View(faults={"S2", "S1"}))
    assert rows(d)[7] == "FAULT L R"
    d.render(View(faults={"S2"}))
    assert rows(d)[7] == "FAULT R"


def test_no_faults_and_oled_fault_leave_row_7_blank(bus):
    d = make(bus)
    d.render(View(faults=set()))
    assert 7 not in rows(d)
    d.render(View(faults={"OLED"}))
    assert 7 not in rows(d)


def test_rows_4_to_6_event_log(bus):
    d = make(bus)
    d.render(View(last_event="LEAVE", events=[1, 2, 3], synced=True,
                  cloud="rej"))
    r = rows(d)
    assert (r[4], r[5], r[6]) == ("Last LEAVE", "Queue 3", "Clock ok  Up rej")


def test_row_4_blank_before_first_event(bus):
    d = make(bus)
    d.render(View(last_event=None))
    assert 4 not in rows(d)


def test_render_works_with_a_real_event_queue_shape(bus):
    class Queue:
        def __len__(self):
            return 7

    d = make(bus)
    d.render(View(events=Queue()))
    assert rows(d)[5] == "Queue 7"


def test_message_rows_and_cut(bus):
    d = make(bus)
    d.render(View())
    d.message("Ultrasonic 2x HC-SR04 sensors", "starting")
    assert rows(d) == {3: "Ultrasonic 2x HC", 5: "starting"}
    d.message("FATAL")
    assert rows(d) == {3: "FATAL"}


# --- the bar -----------------------------------------------------------------------------------

def tick_columns(d):
    """Columns with the top pixel row of the bar (y 24) set: the ticks."""
    return [x for x in range(128) if pixel(d, x, 24)]


def expected_tick(m):
    full = settings.BAR_FULL_SCALE_M
    return 1 + int(126 * min(m, full) / full + 0.5)


def test_bar_ticks_at_zone_thresholds(bus):
    d = make(bus)
    d.render(View(d=None))
    cols = tick_columns(d)
    assert cols == sorted({expected_tick(settings.ZONE_CLOSE_M),
                           expected_tick(settings.ZONE_FAR_M)})
    assert cols == [20, 64]               # 0.3 m and 1.0 m of 2.0 m
    for x in cols:                        # full height: y 24 to 31
        assert all(pixel(d, x, y) for y in range(24, 32))


def test_bar_ticks_follow_settings(bus, monkeypatch):
    monkeypatch.setattr(settings, "ZONE_CLOSE_M", 0.5)
    monkeypatch.setattr(settings, "ZONE_FAR_M", 1.5)
    d = make(bus)
    d.render(View(d=None))
    assert tick_columns(d) == [expected_tick(0.5), expected_tick(1.5)]
    assert tick_columns(d) == [33, 96]


def fill_width(d):
    """Filled columns in the bar's inner row (y 27), not counting ticks."""
    ticks = set(tick_columns(d))
    return sum(1 for x in range(1, 127) if pixel(d, x, 27) and x not in ticks)


def test_bar_fill(bus):
    d = make(bus)
    d.render(View(d=None))
    assert fill_width(d) == 0
    d.render(View(d=1.0))
    assert fill_width(d) == 63 - 1        # columns 1..63, minus the tick at 20
    d.render(View(d=5.0))
    assert fill_width(d) == 126 - 2       # full, minus the two ticks


def test_bar_outline(bus):
    d = make(bus)
    d.render(View(d=None))
    assert all(pixel(d, x, 25) for x in range(128))
    assert all(pixel(d, x, 30) for x in range(128))
    assert pixel(d, 0, 27) and pixel(d, 127, 27)
    assert not pixel(d, 5, 27)


def test_bar_change_resends_page_3(bus, vrun):
    d = make(bus)
    d.render(View(d=0.42))
    vrun(d.flush())
    n = len(bus.log)
    d.render(View(d=0.50))
    vrun(d.flush())
    assert 3 in pages_sent(bus, n)


# --- SSD1306 fallback ------------------------------------------------------------------------------

def test_ssd1306_starts_and_sends_page_windows(bus, vrun):
    d = display.Display(bus, controller="ssd1306")
    assert d.ok is True
    assert 0x8D in commands(bus)          # charge pump set-up was sent
    assert data_writes(bus) == []
    d.render(View())
    n = len(bus.log)
    vrun(d.flush())
    writes = [e for e in bus.log[n:]]
    data = [e for e in writes if e[0] == "writevto"]
    assert len(data) == 8
    assert all(len(e[2]) == 129 and e[2][0] == 0x40 for e in data)
    cmds = commands(bus, n)
    expect = []
    for p in range(8):
        expect += [0x21, 0, 127, 0x22, p, p]
    assert cmds == expect
    assert b"".join(e[2][1:] for e in data) == bytes(d._buf)


def test_ssd1306_missing_and_retry(vrun, fake_clock):
    i2c = machine.I2C(board.I2C_ID)
    d = display.Display(i2c, controller="ssd1306")
    assert d.ok is False
    i2c.attach(ADDR, machine.I2CDevice())
    fake_clock.advance_ms(settings.DISPLAY_RETRY_MS)
    d.render(View())
    vrun(d.flush())
    assert d.ok is True
    assert len([e for e in i2c.log if e[0] == "writevto"]) == 8


# --- timing and integers ------------------------------------------------------------------------------

def test_flush_takes_no_virtual_time(bus, vrun):
    d = make(bus)
    d.render(View())
    t0 = time.ticks_ms()
    vrun(d.flush())
    assert time.ticks_diff(time.ticks_ms(), t0) == 0


def test_float_retry_setting_still_gives_int(vrun, fake_clock, monkeypatch):
    monkeypatch.setattr(settings, "DISPLAY_RETRY_MS", 2000.0)
    i2c = machine.I2C(board.I2C_ID)
    d = display.Display(i2c)              # ticks_add would raise on a float
    fake_clock.advance_ms(2000)
    vrun(d.flush())
    assert d.ok is False


# --- more edge cases ----------------------------------------------------------------------------

def test_cancelled_flush_resumes_with_unsent_pages(bus, vrun):
    d = make(bus)
    d.render(View())
    vrun(d.flush())
    d.render(View(d2=None, faults={"S2"}, cloud="rej", d1=0.43))
    n = len(bus.log)

    async def main():
        t = asyncio.create_task(d.flush())
        while not data_writes(bus, n):
            await asyncio.sleep_ms(0)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass

    vrun(main())
    first = pages_sent(bus, n)
    assert first == [1]                   # cancelled after the first page
    m = len(bus.log)
    vrun(d.flush())
    assert d.ok is True
    assert first + pages_sent(bus, m) == [1, 2, 6, 7]
    assert bytes(d._last) == bytes(d._buf)


def test_limits_render(bus):
    d = make(bus)
    d.render(View(d1=0.02, d2=4.0, d=4.0))
    r = rows(d)
    assert r[1] == "L 0.02 m" and r[2] == "R 4.00 m"
    assert fill_width(d) == 124
    d.render(View(d=0.02))
    assert fill_width(d) == 1


def test_no_start_attempt_before_retry_while_missing(vrun, fake_clock, monkeypatch):
    i2c = machine.I2C(board.I2C_ID)
    d = display.Display(i2c)
    calls = []
    real = d._drv.start

    def counting():
        calls.append(time.ticks_ms())
        real()

    monkeypatch.setattr(d._drv, "start", counting)
    for _ in range(10):
        fake_clock.advance_ms((settings.DISPLAY_RETRY_MS - 1) // 10)
        vrun(d.flush())
    assert calls == []
    fake_clock.advance_ms(settings.DISPLAY_RETRY_MS
                          - 10 * ((settings.DISPLAY_RETRY_MS - 1) // 10))
    vrun(d.flush())
    assert len(calls) == 1
    vrun(d.flush())                       # still missing: the wait starts again
    assert len(calls) == 1


def test_ssd1306_oserror_in_flush(bus, vrun):
    d = display.Display(bus, controller="ssd1306")
    d.render(View())
    bus.fail_after(9)                     # page 0 (7 transfers), then page 1 fails
    vrun(d.flush())
    assert d.ok is False


def test_flush_yields_after_skipped_pages_too(bus, vrun):
    d = make(bus)
    d.render(View())
    vrun(d.flush())
    count = {"n": 0}

    async def observer():
        while True:
            count["n"] += 1
            await asyncio.sleep_ms(0)

    async def main():
        t = asyncio.create_task(observer())
        await asyncio.sleep_ms(0)
        c0 = count["n"]
        await d.flush()                   # nothing changed: 8 compares
        runs = count["n"] - c0
        t.cancel()
        return runs

    assert vrun(main()) >= 8                # one yield per page compare
