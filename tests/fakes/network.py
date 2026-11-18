"""Fake ``network`` module (Pico W station interface) for the host tests.

``WLAN(STA_IF)`` follows a scripted ``status()`` sequence. It records every
call in ``calls``, but never stores the password in them and never prints it.

Scripting:
    ``WLAN.script_next([1, 1, 3])`` sets the sequence for WLANs created
    afterwards; ``wlan.script_status(...)`` changes one instance. Each
    ``status()`` call takes the next value; after the last one the status
    stays at that value.
    ``wlan.set_status(x)`` sets the current status at once (e.g. a drop).
"""

STA_IF = 0
AP_IF = 1

STAT_IDLE = 0
STAT_CONNECTING = 1
STAT_GOT_IP = 3
STAT_CONNECT_FAIL = -1
STAT_NO_AP_FOUND = -2
STAT_WRONG_PASSWORD = -3

_default_script = []
_instances = []


class WLAN:
    """Fake ``network.WLAN``.

    Attributes:
        calls (list[tuple]): recorded calls, e.g. ``("connect", ssid)``.
    """

    def __init__(self, interface=STA_IF):
        self.interface = interface
        self.calls = [("init", interface)]
        self._active = False
        self._status = STAT_IDLE
        self._script = list(_default_script)
        self._ssid = None
        self._password = None
        self._config = {}
        _instances.append(self)

    # --- scripting ----------------------------------------------------------

    @staticmethod
    def script_next(seq):
        """Set the status sequence for WLANs created from now on."""
        _default_script[:] = list(seq)

    def script_status(self, seq):
        """Set the status sequence for this instance."""
        self._script = list(seq)

    def set_status(self, status):
        """Set the current status at once and drop any pending script."""
        self._script = []
        self._status = status

    @staticmethod
    def last():
        """The WLAN created most recently (None if none)."""
        return _instances[-1] if _instances else None

    def password_was(self, password):
        """True if ``connect()`` was given exactly ``password``."""
        return self._password == password

    # --- the network.WLAN API -----------------------------------------------

    def active(self, is_active=None):
        if is_active is None:
            return self._active
        self._active = bool(is_active)
        self.calls.append(("active", self._active))
        return None

    def connect(self, ssid=None, key=None, **kwargs):
        self._ssid = ssid
        self._password = key
        self.calls.append(("connect", ssid))
        if not self._script:
            self._status = STAT_CONNECTING

    def disconnect(self):
        self.calls.append(("disconnect",))
        self._script = []
        self._status = STAT_IDLE

    def status(self, param=None):
        if param is not None:
            return 0
        if self._script:
            self._status = self._script.pop(0)
        return self._status

    def isconnected(self):
        return self._active and self._status == STAT_GOT_IP

    def ifconfig(self, config=None):
        if config is not None:
            self.calls.append(("ifconfig", tuple(config)))
            return None
        return ("192.168.43.20", "255.255.255.0", "192.168.43.1", "192.168.43.1")

    def config(self, *args, **kwargs):
        if kwargs:
            self._config.update(kwargs)
            self.calls.append(("config", tuple(sorted(kwargs))))
            return None
        if args:
            return self._config.get(args[0])
        return None

    def __repr__(self):
        return "<fake WLAN status=%d active=%s>" % (self._status, self._active)


def hostname(name=None):
    """Fake ``network.hostname``."""
    return "pico" if name is None else None


def country(code=None):
    """Fake ``network.country``."""
    return "CZ" if code is None else None


def _reset():
    """Forget every WLAN and the default script (conftest, before each test)."""
    _default_script.clear()
    _instances.clear()
