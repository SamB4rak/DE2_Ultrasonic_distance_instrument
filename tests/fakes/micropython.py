"""Fake ``micropython`` module for the host tests.

``const`` returns its argument, ``native`` and ``viper`` are no-op
decorators, ``schedule`` calls the function at once and records it, and
``alloc_emergency_exception_buf`` records the size it was given.
"""

emergency_buf_size = None
scheduled = []


def const(value):
    """Return ``value`` unchanged."""
    return value


def native(func):
    """No-op decorator."""
    return func


def viper(func):
    """No-op decorator."""
    return func


def alloc_emergency_exception_buf(size):
    """Record ``size`` in ``emergency_buf_size``."""
    global emergency_buf_size
    emergency_buf_size = size


def schedule(func, arg):
    """Call ``func(arg)`` at once and record it in ``scheduled``."""
    scheduled.append((func, arg))
    func(arg)


def kbd_intr(char):
    """No-op."""
    return None


def _reset():
    """Forget the recorded calls (conftest, before each test)."""
    global emergency_buf_size
    emergency_buf_size = None
    scheduled.clear()
