"""Self-test of the host harness: the virtual clock, the shims and the fakes."""

import asyncio
import importlib
import sys
import time

import pytest

import machine
import ntptime
from net_fakes import FakeStream
from vtime import CLOCK, TICKS_PERIOD

# --- ticks ------------------------------------------------------------------


def test_ticks_diff_and_add_across_wraparound(fake_clock):
    fake_clock.reset(TICKS_PERIOD - 100)
    before = time.ticks_us()
    assert before == TICKS_PERIOD - 100
    fake_clock.advance_us(250)
    after = time.ticks_us()
    assert after == 150                      # wrapped
    assert time.ticks_diff(after, before) == 250
    assert time.ticks_diff(before, after) == -250
    assert time.ticks_add(before, 250) == after
    assert time.ticks_add(after, -250) == before
    assert time.ticks_add(TICKS_PERIOD - 1, 1) == 0


def test_ticks_ms_wraps_too(fake_clock):
    fake_clock.reset((TICKS_PERIOD - 5) * 1000)
    t0 = time.ticks_ms()
    fake_clock.advance_ms(10)
    assert time.ticks_ms() == 5
    assert time.ticks_diff(time.ticks_ms(), t0) == 10


def test_blocking_sleeps_advance_the_clock(fake_clock):
    time.sleep_ms(7)
    time.sleep_us(10)
    assert fake_clock.us == 7010


# --- virtual-time asyncio ---------------------------------------------------


def test_sleep_ms_36_s_is_virtual(vrun, fake_clock):
    real0 = time.perf_counter()
    vrun(asyncio.sleep_ms(36_000))
    assert fake_clock.us == 36_000_000
    assert time.perf_counter() - real0 < 0.5


def test_two_tasks_interleave_in_fixed_order(vrun):
    log = []

    async def ticker(name, period_ms, n):
        for _ in range(n):
            await asyncio.sleep_ms(period_ms)
            log.append((name, time.ticks_ms()))

    async def main():
        await asyncio.gather(ticker("a", 30, 4), ticker("b", 50, 2))

    vrun(main())
    assert log == [("a", 30), ("b", 50), ("a", 60), ("a", 90), ("b", 100), ("a", 120)]


def test_wait_for_ms_timeout_and_inner_finally(vrun):
    seen = {}

    async def inner():
        try:
            await asyncio.sleep_ms(10_000)
        finally:
            seen["finally_at"] = time.ticks_ms()

    async def main():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for_ms(inner(), 250)
        seen["timeout_at"] = time.ticks_ms()

    vrun(main())
    assert seen == {"finally_at": 250, "timeout_at": 250}


def test_wait_for_ms_returns_result_in_time(vrun):
    async def quick():
        await asyncio.sleep_ms(20)
        return 42

    assert vrun(asyncio.wait_for_ms(quick(), 100)) == 42
    assert time.ticks_ms() == 20


def test_cancel_during_sleep_runs_finally(vrun):
    seen = []

    async def worker():
        try:
            await asyncio.sleep_ms(1000)
        finally:
            seen.append(time.ticks_ms())

    async def main():
        task = asyncio.create_task(worker())
        await asyncio.sleep_ms(100)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    vrun(main())
    assert seen == [100]


def test_vrun_cancels_leftover_tasks_inside_the_loop(vrun):
    seen = []

    async def forever():
        try:
            while True:
                await asyncio.sleep_ms(10)
        finally:
            seen.append(asyncio.get_running_loop() is not None)

    async def main():
        asyncio.create_task(forever())
        await asyncio.sleep_ms(35)
        return "done"

    assert vrun(main()) == "done"
    assert seen == [True]


def test_vrun_for_ms_stops_a_never_ending_coroutine(vrun):
    count = []

    async def forever():
        while True:
            await asyncio.sleep_ms(100)
            count.append(1)

    vrun.for_ms(forever(), 1000)
    assert time.ticks_ms() == 1000
    assert len(count) == 10


def test_deadlock_raises(vrun):
    async def stuck():
        await asyncio.Event().wait()

    with pytest.raises(RuntimeError, match="deadlock"):
        vrun(stuck())


@pytest.mark.parametrize("call", [
    lambda: time.sleep_ms(1.5),
    lambda: time.sleep_us(10.0),
    lambda: time.ticks_add(0, 1.0),
    lambda: time.ticks_diff(1.0, 0),
])
def test_floats_raise_type_error(call):
    with pytest.raises(TypeError):
        call()


def test_float_to_asyncio_sleep_ms_raises(vrun):
    with pytest.raises(TypeError):
        vrun(asyncio.sleep_ms(0.5))
    inner = asyncio.sleep_ms(1)
    with pytest.raises(TypeError):
        vrun(asyncio.wait_for_ms(inner, 10.0))
    inner.close()


def test_thread_safe_flag_wakes_a_waiting_task(vrun):
    order = []

    async def main():
        flag = asyncio.ThreadSafeFlag()

        async def waiter():
            await flag.wait()
            order.append(("woke", time.ticks_ms()))

        task = asyncio.create_task(waiter())
        await asyncio.sleep_ms(40)
        order.append(("set", time.ticks_ms()))
        flag.set()
        await task

    vrun(main())
    assert order == [("set", 40), ("woke", 40)]


def test_thread_safe_flag_set_before_wait_returns_at_once(vrun):
    async def main():
        flag = asyncio.ThreadSafeFlag()
        flag.set()
        await flag.wait()
        return time.ticks_ms()

    assert vrun(main()) == 0


def test_asyncio_run_is_virtual(fake_clock):
    async def main():
        t0 = time.ticks_ms()
        await asyncio.sleep_ms(300)
        return time.ticks_diff(time.ticks_ms(), t0)

    real0 = time.perf_counter()
    assert asyncio.run(main()) == 300
    assert time.perf_counter() - real0 < 0.5


# --- machine fakes ----------------------------------------------------------


def test_set_input_fires_hard_irq_with_virtual_timestamp(fake_clock):
    stamps = []
    echo = machine.Pin(21, machine.Pin.IN)

    def handler(pin):
        stamps.append((pin.value(), time.ticks_us()))

    echo.irq(handler=handler, trigger=machine.Pin.IRQ_RISING | machine.Pin.IRQ_FALLING, hard=True)
    assert echo.irq_hard is True
    fake_clock.advance_us(1000)
    echo.set_input(1)
    fake_clock.advance_us(5831)
    echo.set_input(0)
    echo.set_input(0)                         # no edge, no call
    assert stamps == [(1, 1000), (0, 6831)]


def test_irq_trigger_filters_edges():
    calls = []
    pin = machine.Pin(19, machine.Pin.IN)
    pin.irq(handler=lambda p: calls.append(p.value()), trigger=machine.Pin.IRQ_FALLING)
    pin.set_input(1)
    pin.set_input(0)
    assert calls == [0]


def test_pin_history_starts_with_constructor_value():
    relay = machine.Pin(16, machine.Pin.OUT, value=1)
    relay.value(0)
    relay.off()
    relay.on()
    assert relay.history == [1, 0, 0, 1]
    assert machine.Pin.get(16) is relay
    assert relay.value() == 1


def test_pin_drive_keyword_raises():
    with pytest.raises(TypeError):
        machine.Pin(12, machine.Pin.OUT, drive=3)


def test_pin_preset_is_read_by_a_later_pin():
    machine.Pin.preset(17, 0)
    button = machine.Pin(17, machine.Pin.IN, machine.Pin.PULL_UP)
    assert button.value() == 0


def test_pin_default_levels():
    assert machine.Pin(2, machine.Pin.IN, machine.Pin.PULL_UP).value() == 1
    assert machine.Pin(3, machine.Pin.IN).value() == 0


def test_pwm_records_freq_and_duty():
    pwm = machine.PWM(machine.Pin(12), freq=1000, duty_u16=0)
    pwm.duty_u16(65535)
    assert machine.PWM.get(12) is pwm
    assert pwm.freq() == 1000
    assert pwm.duty_history == [0, 65535]
    with pytest.raises(ValueError):
        pwm.duty_u16(70000)


def test_i2c_missing_device_raises_and_fail_after_works():
    i2c = machine.I2C(0, scl=machine.Pin(5), sda=machine.Pin(4), freq=400_000)
    with pytest.raises(OSError):
        i2c.writeto(0x3C, b"\x00")
    dev = machine.I2CDevice()
    i2c.attach(0x3C, dev)
    assert i2c.scan() == [0x3C]
    i2c.fail_after(2)
    i2c.writeto(0x3C, b"\x00\xAE")
    i2c.writevto(0x3C, [b"\x40", b"\x01\x02"])
    with pytest.raises(OSError):
        i2c.writeto(0x3C, b"\x00")
    assert dev.received == [b"\x00\xAE", b"\x40\x01\x02"]
    assert [entry[0] for entry in i2c.log] == ["writeto", "writevto"]
    i2c.fail_after(None)
    i2c.detach(0x3C)
    with pytest.raises(OSError):
        i2c.readfrom(0x3C, 1)


def test_i2c_register_device_reads_and_writes():
    i2c = machine.I2C(0)
    i2c.attach(0x76, machine.I2CDevice({0xD0: 0x60}))
    assert i2c.readfrom_mem(0x76, 0xD0, 1) == b"\x60"
    i2c.writeto_mem(0x76, 0x10, b"\x01\x02")
    buf = bytearray(2)
    i2c.readfrom_mem_into(0x76, 0x10, buf)
    assert buf == b"\x01\x02"


def test_time_pulse_us_raises():
    with pytest.raises(RuntimeError):
        machine.time_pulse_us(machine.Pin(21), 1, 30_000)


def test_mem32_records_writes():
    machine.mem32[0x4001C000] = 0x56
    assert machine.mem32[0x4001C000] == 0x56
    assert machine.mem32.writes == [(0x4001C000, 0x56)]


def test_registries_reset_between_tests_part_1():
    machine.Pin.preset(5, 1)
    machine.Pin(5, machine.Pin.OUT)


def test_registries_reset_between_tests_part_2(fake_clock):
    assert fake_clock.us == 0
    with pytest.raises(KeyError):
        machine.Pin.get(5)
    assert machine.Pin(5, machine.Pin.IN).value() == 0


# --- module aliases, print_exception, config --------------------------------


@pytest.mark.parametrize("alias", [
    "utime", "ustruct", "uerrno", "uos", "usys", "ubinascii", "uasyncio",
    "usocket", "uselect", "ucollections", "uarray",
])
def test_u_alias_imports(alias):
    mod = importlib.import_module(alias)
    assert mod is sys.modules[alias[1:]]


def test_print_exception_prints_traceback(capsys):
    try:
        raise ValueError("boom")
    except ValueError as e:
        sys.print_exception(e)
    out = capsys.readouterr().out
    assert "Traceback" in out and "ValueError: boom" in out


def test_import_config_gives_the_fake():
    import config
    assert config.WIFI_SSID == "test-ssid"
    assert config.WIFI_PASSWORD == "test-password"
    assert config.THINGSPEAK_WRITE_KEY == "TEST-WRITE-KEY"
    assert config.THINGSPEAK_CHANNEL_ID == "1234567"


def test_no_config_makes_import_fail(no_config):
    with pytest.raises(ImportError):
        import config  # noqa: F401


def test_config_is_back_after_no_config():
    import config
    assert config.WIFI_SSID == "test-ssid"


# --- ntptime and network fakes ----------------------------------------------


def test_fake_ntptime_returns_scripted_value_or_raises(fake_clock):
    with pytest.raises(OSError):
        ntptime.time()                       # unscripted: no network
    ntptime.script(OSError(110), 1_800_000_000)
    with pytest.raises(OSError):
        ntptime.time()
    assert ntptime.time() == 1_800_000_000
    assert ntptime.time() == 1_800_000_000
    assert ntptime.calls == 4
    assert fake_clock.us == 0


def test_fake_ntptime_can_act_out_a_block(fake_clock):
    ntptime.script(1_800_000_000, delay_ms=7000)
    assert ntptime.time() == 1_800_000_000
    assert time.ticks_ms() == 7000


def test_fake_ntptime_is_reset_between_tests():
    assert ntptime.calls == 0
    with pytest.raises(OSError):
        ntptime.time()


def test_fake_wlan_follows_its_script():
    import network
    network.WLAN.script_next([network.STAT_CONNECTING, network.STAT_GOT_IP])
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect("test-ssid", "test-password")
    assert not wlan.isconnected()
    assert wlan.status() == network.STAT_CONNECTING
    assert wlan.status() == network.STAT_GOT_IP
    assert wlan.status() == network.STAT_GOT_IP     # the last value stays
    assert wlan.isconnected()
    assert wlan.password_was("test-password")
    assert "test-password" not in repr(wlan.calls)
    wlan.set_status(network.STAT_IDLE)
    assert not wlan.isconnected()


def test_fake_stream_close_alone_leaves_it_open(vrun):
    async def main():
        stream = FakeStream([b"HTTP/1.0 202 Accepted\r\n", (20, b"\r\nbody")])
        stream.write(b"GET / HTTP/1.0\r\n\r\n")
        await stream.drain()
        assert await stream.readline() == b"HTTP/1.0 202 Accepted\r\n"
        assert await stream.read(100) == b"\r\nbody"
        assert time.ticks_ms() == 20
        assert await stream.read(100) == b""
        stream.close()
        assert stream.close_called and not stream.closed
        await stream.wait_closed()
        assert stream.closed
        return bytes(stream.written)

    assert vrun(main()) == b"GET / HTTP/1.0\r\n\r\n"


def test_clock_is_shared_by_everything(vrun):
    async def main():
        await asyncio.sleep_ms(5)
        return CLOCK.us, time.ticks_us(), asyncio.get_running_loop().time()

    us, ticks, loop_time = vrun(main())
    assert us == ticks == 5000
    assert loop_time == pytest.approx(0.005)


# --- review additions --------------------------------------------------------


def test_wait_for_ms_timeout_is_not_an_oserror(vrun):
    async def main():
        try:
            await asyncio.wait_for_ms(asyncio.sleep_ms(100), 10)
        except Exception as e:
            return e
        return None

    exc = vrun(main())
    assert isinstance(exc, asyncio.TimeoutError)
    assert not isinstance(exc, OSError)           # as on MicroPython


def test_wait_for_raises_the_same_timeout(vrun):
    with pytest.raises(asyncio.TimeoutError):
        vrun(asyncio.wait_for(asyncio.sleep_ms(100), 0.01))


def test_unpatched_network_is_refused(vrun):
    import socket
    with pytest.raises(RuntimeError):
        socket.getaddrinfo("api.thingspeak.com", 80)
    with pytest.raises(RuntimeError):
        vrun(asyncio.open_connection("184.106.153.149", 80))


def test_a_real_config_is_never_imported(monkeypatch):
    monkeypatch.delitem(sys.modules, "config")
    with pytest.raises(ImportError, match="never import a real config"):
        import config  # noqa: F401


def test_timers_due_together_run_in_a_fixed_order(vrun):
    log = []

    async def ticker(name, period_ms):
        for _ in range(2):
            await asyncio.sleep_ms(period_ms)
            log.append((name, time.ticks_ms()))

    async def main():
        await asyncio.gather(ticker("a", 30), ticker("b", 60))

    # Two wake-ups due at the same instant run in the same order on every run.
    # Which one goes first is an implementation detail (MicroPython may break
    # ties differently), so module code must never rely on it.
    runs = []
    for _ in range(3):
        log.clear()
        CLOCK.reset(0)
        vrun(main())
        runs.append(list(log))
    assert runs[0] == runs[1] == runs[2]
    assert sorted(runs[0]) == [("a", 30), ("a", 60), ("b", 60), ("b", 120)]


def test_pwm_freq_rejects_floats():
    pwm = machine.PWM(machine.Pin(12))
    with pytest.raises(TypeError):
        pwm.freq(1000.0)


def test_pin_toggle_and_init():
    led = machine.Pin(25, machine.Pin.OUT, value=0)
    led.toggle()
    led.toggle()
    assert led.history == [0, 1, 0]
    led.init(machine.Pin.OUT, value=1)
    assert led.value() == 1


def test_framebuf_writes_vlsb_bits_and_records_text():
    import framebuf
    buf = bytearray(128 * 64 // 8)
    fb = framebuf.FrameBuffer(buf, 128, 64, framebuf.MONO_VLSB)
    fb.pixel(3, 9, 1)
    assert buf[128 + 3] == 0x02                    # page 1, bit 1
    assert fb.pixel(3, 9) == 1 and fb.pixel(3, 8) == 0
    fb.hline(0, 63, 128, 1)
    assert all(buf[7 * 128 + x] & 0x80 for x in range(128))

    fb.fill(0)
    assert not any(buf) and fb.texts == []
    fb.text("L 0.42", 0, 8)
    first = bytes(buf[128:256])
    assert fb.texts == [("L 0.42", 0, 8)]
    assert any(first) and not any(buf[:128])       # drawn on page 1 only
    fb.fill(0)
    fb.text("L 0.24", 0, 8)
    assert bytes(buf[128:256]) != first            # different digits, different bits
    fb.fill_rect(0, 8, 128, 8, 0)
    assert not any(buf) and fb.texts == [("L 0.24", 0, 8)]


def test_fake_open_connection_and_getaddrinfo(vrun, fake_clock):
    from net_fakes import FakeGetaddrinfo, FakeOpenConnection

    lookup = FakeGetaddrinfo(OSError(-2), "184.106.153.149", delay_ms=700)
    with pytest.raises(OSError):
        lookup("api.thingspeak.com", 80)
    assert lookup("api.thingspeak.com", 80)[0][-1] == ("184.106.153.149", 80)
    assert lookup("api.thingspeak.com", 80)[0][-1][0] == "184.106.153.149"   # last repeats
    assert lookup.calls == [("api.thingspeak.com", 80)] * 3
    assert time.ticks_ms() == 2100                 # each call blocked 700 ms

    connect = FakeOpenConnection(OSError(113), lambda: FakeStream([b"x"]), delay_ms=50)

    async def main():
        with pytest.raises(OSError):
            await connect("184.106.153.149", 80)
        r1, w1 = await connect("184.106.153.149", 80)
        r2, _ = await connect("184.106.153.149", 80)
        assert r1 is w1 and r1 is not r2           # the factory makes a new stream
        return time.ticks_ms()

    assert vrun(main()) == 2100 + 150
    assert connect.calls == [("184.106.153.149", 80)] * 3
    assert len(connect.streams) == 2


def test_never_answering_stream_times_out_exactly(vrun):
    async def main():
        stream = FakeStream(never_answers=True)
        t0 = time.ticks_ms()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for_ms(stream.read(100), 500)
        return time.ticks_diff(time.ticks_ms(), t0)

    assert vrun(main()) == 500


def test_fake_stream_raises_a_scripted_error(vrun):
    async def main():
        stream = FakeStream([b"HTTP/1.0 2", OSError(104)])
        assert await stream.read(100) == b"HTTP/1.0 2"
        with pytest.raises(OSError):
            await stream.read(100)
        await stream.wait_closed()
        with pytest.raises(OSError):
            stream.write(b"late")

    vrun(main())


def test_wait_for_ms_passes_an_inner_oserror_through(vrun):
    import errno
    import vtime

    async def inner():
        await asyncio.sleep_ms(10)
        raise OSError(errno.ETIMEDOUT, "timed out")

    async def main():
        try:
            await asyncio.wait_for_ms(inner(), 1000)
        except Exception as e:
            return e

    exc = vrun(main())
    assert isinstance(exc, OSError) and exc.errno == errno.ETIMEDOUT
    assert not isinstance(exc, vtime.TimeoutError)


def test_create_connection_is_refused():
    import socket
    with pytest.raises(RuntimeError):
        socket.create_connection(("184.106.153.149", 80))
