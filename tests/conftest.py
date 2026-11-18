"""Host test harness: runs the MicroPython code under CPython in virtual time.

At import (before any test module is collected) it:

* puts ``tests/fakes/``, ``src/`` and ``src/lib/`` at the front of
  ``sys.path``, in that order, so the fakes win over any vendored copy;
* installs the MicroPython time and asyncio shims from ``vtime`` on CPython's
  ``time`` and ``asyncio`` (``asyncio.run`` runs in virtual time too);
* sets up the ``u``-module aliases and ``sys.print_exception``;
* puts a fake ``config`` module with obviously fake values into
  ``sys.modules``, so no test ever imports a real ``src/config.py``.

Fixtures:
    fake_clock: the virtual clock (``.us``, ``reset``, ``advance_ms``,
        ``advance_us``).
    vrun: ``vrun(coro)`` runs a coroutine on a fresh virtual-time loop;
        ``vrun.for_ms(coro, ms)`` runs a never-ending one for ``ms``.
    no_config: makes ``import config`` fail with ImportError.

Before every test the clock, the fake registries, the fake ``ntptime`` and the
fake ``config`` are reset.
"""

import asyncio
import os
import sys
import time
import traceback
import types

import pytest

_TESTS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_TESTS)
sys.path[0:0] = [
    os.path.join(_TESTS, "fakes"),
    os.path.join(_ROOT, "src"),
    os.path.join(_ROOT, "src", "lib"),
]

import vtime  # noqa: E402  (needs the sys.path above)
import machine  # noqa: E402
import micropython  # noqa: E402
import network  # noqa: E402
import ntptime  # noqa: E402

vtime.install(time, asyncio)

_U_ALIASES = {
    "utime": "time",
    "ustruct": "struct",
    "uerrno": "errno",
    "uos": "os",
    "usys": "sys",
    "ubinascii": "binascii",
    "uasyncio": "asyncio",
    "usocket": "socket",
    "uselect": "select",
    "ucollections": "collections",
    "uarray": "array",
    "ujson": "json",
    "uio": "io",
    "ure": "re",
    "uhashlib": "hashlib",
}
for _alias, _name in _U_ALIASES.items():
    sys.modules[_alias] = __import__(_name)


def _print_exception(exc, file=None):
    """MicroPython's ``sys.print_exception``: print the traceback."""
    traceback.print_exception(type(exc), exc, exc.__traceback__,
                              file=sys.stdout if file is None else file)


sys.print_exception = _print_exception

FAKE_CONFIG = {
    "WIFI_SSID": "test-ssid",
    "WIFI_PASSWORD": "test-password",
    "THINGSPEAK_WRITE_KEY": "TEST-WRITE-KEY",
    "THINGSPEAK_CHANNEL_ID": "1234567",
}


def _install_fake_config():
    mod = types.ModuleType("config")
    mod.__doc__ = "Fake config for the host tests (no real secrets)."
    for name, value in FAKE_CONFIG.items():
        setattr(mod, name, value)
    sys.modules["config"] = mod
    return mod


_install_fake_config()


class _RefuseRealConfig:
    """Import hook: a real ``config`` module is never imported by a test.

    The fake lives in ``sys.modules``, so this hook only runs if a test
    removed it. It refuses before any file is found or read.
    """

    @staticmethod
    def find_spec(name, path=None, target=None):
        if name == "config":
            raise ImportError("tests never import a real config.py; use the fake config")
        return None


sys.meta_path.insert(0, _RefuseRealConfig)


def _no_network(*args, **kwargs):
    raise RuntimeError("host tests never use the real network: install a fake "
                       "(net_fakes) with monkeypatch")


@pytest.fixture(autouse=True)
def _reset_harness(monkeypatch):
    """Fresh clock, fakes and config before every test; no real network."""
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setattr(asyncio, "open_connection", _no_network)
    vtime.CLOCK.reset(0)
    machine._reset()
    network._reset()
    micropython._reset()
    ntptime._reset()
    _install_fake_config()
    yield
    asyncio.set_event_loop(None)


@pytest.fixture
def fake_clock():
    """The shared virtual clock."""
    return vtime.CLOCK


@pytest.fixture
def vrun():
    """Run coroutines in virtual time (see ``vtime.vrun``)."""
    return vtime.vrun


@pytest.fixture
def no_config():
    """Make ``import config`` raise ImportError for this test."""
    sys.modules["config"] = None
    yield
    _install_fake_config()
