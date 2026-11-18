"""Fake ``ntptime`` module for the host tests. It never touches the network.

``time()`` returns the scripted epoch seconds or raises the scripted
exception. Before that it can advance the virtual clock by ``delay_ms``, to act
out the blocking NTP exchange.

Scripting:
    ``ntptime.script(1_800_000_000)``: every call returns that value.
    ``ntptime.script(OSError(110), 1_800_000_000)``: the first call raises,
    later calls return the value (the last result repeats).
    ``ntptime.script(..., delay_ms=7000)``: each call blocks 7 s of virtual time.
    Unscripted, ``time()`` raises ``OSError`` (no network).

Attributes:
    host (str), timeout (int): as in MicroPython's ``ntptime``.
    calls (int): how many times ``time()`` ran.
"""

from vtime import CLOCK

host = "pool.ntp.org"
timeout = 1
calls = 0
delay_ms = 0
_results = []


def script(*results, delay_ms=0):
    """Script the results of the next ``time()`` calls (ints or exceptions)."""
    global _results
    _results = list(results)
    globals()["delay_ms"] = delay_ms


def time():
    """Return the scripted epoch seconds, or raise the scripted exception."""
    global calls
    calls += 1
    if delay_ms:
        CLOCK.advance_ms(delay_ms)
    if not _results:
        raise OSError(110, "ETIMEDOUT (fake ntptime: not scripted)")
    result = _results.pop(0) if len(_results) > 1 else _results[0]
    if isinstance(result, BaseException):
        raise result
    return result


def settime():
    """Like MicroPython's ``settime``: runs ``time()`` (the fake has no RTC)."""
    time()


def _reset():
    """Back to unscripted (conftest, before each test)."""
    global host, timeout, calls, delay_ms, _results
    host = "pool.ntp.org"
    timeout = 1
    calls = 0
    delay_ms = 0
    _results = []
