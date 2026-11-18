"""One virtual clock for the host tests, plus MicroPython-style time shims.

Everything in the host tests reads ``CLOCK``, an integer microsecond counter:
``time.ticks_ms()``, ``time.ticks_us()`` and the event loop's ``time()``.
Virtual time passes instantly and deterministically.

``tests/conftest.py`` installs these functions on CPython's real ``time`` and
``asyncio`` modules, so code written for MicroPython runs unchanged:

* ``time.ticks_ms/ticks_us/ticks_add/ticks_diff`` with the rp2 ticks period
  2**30 and MicroPython's wraparound semantics,
* ``time.sleep_ms/sleep_us``, which advance ``CLOCK`` at once (they model
  blocking calls),
* ``asyncio.sleep_ms``, ``asyncio.wait_for_ms`` and ``asyncio.ThreadSafeFlag``,
* ``asyncio.run`` replaced by :func:`vrun`.

As on the Pico, every millisecond or microsecond argument must be an ``int``:
a float raises ``TypeError``.
"""

import asyncio
import selectors

TICKS_PERIOD = 1 << 30
TICKS_MAX = TICKS_PERIOD - 1
TICKS_HALFPERIOD = TICKS_PERIOD // 2


class VirtualClock:
    """Integer microsecond clock shared by every fake.

    Attributes:
        us (int): current virtual time in microseconds since the reset.
    """

    def __init__(self):
        self.us = 0

    def reset(self, us=0):
        """Set the clock to ``us`` microseconds (default 0)."""
        _need_int("us", us)
        self.us = us

    def advance_ms(self, n):
        """Move the clock forward by ``n`` milliseconds (int, >= 0)."""
        _need_int("n", n)
        if n < 0:
            raise ValueError("the clock never runs backwards")
        self.us += n * 1000

    def advance_us(self, n):
        """Move the clock forward by ``n`` microseconds (int, >= 0)."""
        _need_int("n", n)
        if n < 0:
            raise ValueError("the clock never runs backwards")
        self.us += n


CLOCK = VirtualClock()


def _need_int(name, value):
    """Raise TypeError unless ``value`` is an int, as the Pico does."""
    if not isinstance(value, int):
        raise TypeError("%s must be an int, not %s" % (name, type(value).__name__))


# --- time shims -------------------------------------------------------------

def ticks_ms():
    """Virtual milliseconds, wrapped to the rp2 ticks period (2**30)."""
    return (CLOCK.us // 1000) & TICKS_MAX


def ticks_us():
    """Virtual microseconds, wrapped to the rp2 ticks period (2**30)."""
    return CLOCK.us & TICKS_MAX


def ticks_add(ticks, delta):
    """MicroPython ``ticks_add``: ``(ticks + delta)`` wrapped. Ints only."""
    _need_int("ticks", ticks)
    _need_int("delta", delta)
    return (ticks + delta) & TICKS_MAX


def ticks_diff(ticks1, ticks2):
    """MicroPython ``ticks_diff``: signed ``ticks1 - ticks2`` across the wrap."""
    _need_int("ticks1", ticks1)
    _need_int("ticks2", ticks2)
    return ((ticks1 - ticks2 + TICKS_HALFPERIOD) & TICKS_MAX) - TICKS_HALFPERIOD


def sleep_ms(ms):
    """Blocking sleep: advances the clock by ``ms`` milliseconds at once."""
    _need_int("ms", ms)
    if ms > 0:
        CLOCK.advance_ms(ms)


def sleep_us(us):
    """Blocking sleep: advances the clock by ``us`` microseconds at once."""
    _need_int("us", us)
    if us > 0:
        CLOCK.advance_us(us)


# --- asyncio shims ----------------------------------------------------------

async def asyncio_sleep_ms(ms):
    """``asyncio.sleep_ms``: await ``ms`` milliseconds of virtual time."""
    _need_int("ms", ms)
    await asyncio.sleep(max(ms, 0) / 1000)


class TimeoutError(Exception):
    """MicroPython's ``asyncio.TimeoutError``, which derives from Exception.

    CPython's (3.11+) is the built-in TimeoutError, an OSError subclass, so
    ``except OSError`` would catch a timeout on the host but not on the Pico.
    ``install()`` sets this class as ``asyncio.TimeoutError``.
    """


async def asyncio_wait_for_ms(aw, ms):
    """``asyncio.wait_for_ms``: like ``wait_for`` with a timeout in ms (int).

    Raises:
        TimeoutError: (this module's, installed as ``asyncio.TimeoutError``)
            when ``ms`` passes first; the inner awaitable is cancelled, so its
            ``finally`` blocks run.
    """
    _need_int("ms", ms)
    try:
        return await _CPYTHON_WAIT_FOR(aw, max(ms, 0) / 1000)
    except _CPYTHON_TIMEOUT as e:
        raise _converted(e)


async def asyncio_wait_for(aw, timeout):
    """``asyncio.wait_for`` (timeout in seconds) raising MicroPython's TimeoutError."""
    try:
        return await _CPYTHON_WAIT_FOR(aw, timeout)
    except _CPYTHON_TIMEOUT as e:
        raise _converted(e)


def _converted(exc):
    """MicroPython's TimeoutError if the wait itself expired, else ``exc``.

    CPython's ``wait_for`` raises its TimeoutError from the CancelledError of
    the inner awaitable. Any other TimeoutError (e.g. an ``OSError(ETIMEDOUT)``
    a fake socket raised) passes through unchanged, as on the Pico.
    """
    if isinstance(exc.__cause__, asyncio.CancelledError):
        new = TimeoutError()
        new.__cause__ = exc
        return new
    return exc


_CPYTHON_WAIT_FOR = asyncio.wait_for
_CPYTHON_TIMEOUT = asyncio.TimeoutError


class ThreadSafeFlag:
    """Simple fake of MicroPython's ``asyncio.ThreadSafeFlag``.

    ``set()`` may be called from a (fake) IRQ handler, inside or outside the
    loop. ``wait()`` returns at once if the flag is already set, and clears it.
    Create it inside the test or coroutine, never at import time.
    """

    def __init__(self):
        self._flag = False
        self._waiter = None

    def set(self):
        """Set the flag and wake a waiting task."""
        self._flag = True
        if self._waiter is not None and not self._waiter.done():
            self._waiter.set_result(None)

    def clear(self):
        """Clear the flag."""
        self._flag = False

    async def wait(self):
        """Wait until the flag is set, then clear it."""
        if not self._flag:
            self._waiter = asyncio.get_running_loop().create_future()
            try:
                await self._waiter
            finally:
                self._waiter = None
        self._flag = False


# --- the virtual-time event loop -------------------------------------------

class DeadlockError(RuntimeError):
    """Nothing is ready and nothing is scheduled: the test can never finish."""


class _NoWaitSelector(selectors.DefaultSelector):
    """A selector that never waits: it advances CLOCK to the next timer."""

    def select(self, timeout=None):
        events = super().select(0)
        if events:
            return events
        if timeout is None:
            raise DeadlockError("virtual loop deadlocked: no task is ready and no timer is scheduled")
        if timeout > 0:
            CLOCK.advance_us(int(round(timeout * 1_000_000)))
        return []


class VirtualTimeLoop(asyncio.SelectorEventLoop):
    """A SelectorEventLoop whose ``time()`` reads CLOCK and which never waits.

    When nothing is ready, it advances CLOCK to the next timer. When nothing is
    scheduled at all, it raises :class:`DeadlockError`.
    """

    def __init__(self):
        super().__init__(selector=_NoWaitSelector())
        # Timers due within half a microsecond count as due, so float rounding
        # of the seconds-based asyncio timers never leaves the loop spinning.
        self._clock_resolution = 5e-7

    def time(self):
        return CLOCK.us / 1_000_000


def _cancel_all_tasks(loop):
    tasks = [t for t in asyncio.all_tasks(loop) if not t.done()]
    if not tasks:
        return
    for task in tasks:
        task.cancel()
    loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))


def vrun(coro):
    """Run ``coro`` to completion on a fresh VirtualTimeLoop; return its result.

    Like ``asyncio.run``, it then cancels leftover tasks, so their ``finally``
    blocks run inside the loop. Replaces ``asyncio.run`` in the host tests.
    """
    loop = VirtualTimeLoop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        try:
            _cancel_all_tasks(loop)
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            asyncio.set_event_loop(None)
            loop.close()


_SETTLE_YIELDS = 5


def _vrun_for_ms(coro, ms):
    """Run a never-ending coroutine for ``ms`` of virtual time, then cancel it.

    Everything that falls due at exactly ``ms`` still runs before the cancel.
    Returns the coroutine's result if it ends earlier; re-raises its exception.
    """
    _need_int("ms", ms)

    async def limited():
        task = asyncio.ensure_future(coro)
        done, _ = await asyncio.wait([task], timeout=ms / 1000)
        if task in done:
            return task.result()
        for _ in range(_SETTLE_YIELDS):   # let wakeups due at exactly ms run
            await asyncio.sleep(0)
            if task.done():
                return task.result()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return None

    return vrun(limited())


vrun.for_ms = _vrun_for_ms


def install(time_module, asyncio_module):
    """Install the shims on CPython's real ``time`` and ``asyncio`` modules."""
    time_module.ticks_ms = ticks_ms
    time_module.ticks_us = ticks_us
    time_module.ticks_add = ticks_add
    time_module.ticks_diff = ticks_diff
    time_module.sleep_ms = sleep_ms
    time_module.sleep_us = sleep_us
    asyncio_module.sleep_ms = asyncio_sleep_ms
    asyncio_module.wait_for_ms = asyncio_wait_for_ms
    asyncio_module.wait_for = asyncio_wait_for
    asyncio_module.TimeoutError = TimeoutError
    asyncio_module.ThreadSafeFlag = ThreadSafeFlag
    asyncio_module.run = vrun

