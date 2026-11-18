"""Fake ``machine`` module for the host tests (rp2 port behaviour).

Provides ``Pin``, ``PWM``, ``I2C``, ``mem32`` and a few helpers, with test
hooks to inspect and drive them. ``tests/conftest.py`` calls :func:`_reset`
before every test.

``time_pulse_us`` raises: the firmware code must not busy-wait on a pulse.
"""


class Pin:
    """Fake GPIO pin.

    Args:
        id (int): GPIO number.
        mode (int): ``Pin.IN`` or ``Pin.OUT`` (-1: unchanged).
        pull (int): ``Pin.PULL_UP``, ``Pin.PULL_DOWN`` or -1 (none).
        value (int | None): initial output level.

    There is no ``drive`` keyword: like the rp2 port, passing it raises
    ``TypeError``.

    Test helpers:
        ``Pin.get(id)``: the instance a module created for that GPIO.
        ``Pin.preset(id, level)``: the level a later ``Pin(id, Pin.IN, ...)``
        reads (default 1 with ``PULL_UP``, else 0).
        ``history``: every output level written, starting with the
        constructor's ``value``.
        ``set_input(level)``: change an input level; on a matching edge the
        registered IRQ handler is called at once.
        ``irq_hard``: the ``hard`` flag of the last ``irq()`` call.
    """

    IN = 0
    OUT = 1
    OPEN_DRAIN = 2
    PULL_UP = 1
    PULL_DOWN = 2
    IRQ_FALLING = 4
    IRQ_RISING = 8

    _instances = {}
    _presets = {}

    def __init__(self, id, mode=-1, pull=-1, *, value=None):
        self.id = id
        self.mode = mode
        self.pull = pull
        self.history = []
        self.irq_hard = None
        self.irq_trigger = 0
        self._handler = None
        if mode == Pin.IN:
            default = 1 if pull == Pin.PULL_UP else 0
            self._level = Pin._presets.get(id, default)
        else:
            self._level = 0
        if value is not None:
            self._level = 1 if value else 0
            self.history.append(self._level)
        Pin._instances[id] = self

    def value(self, v=None):
        """Read the level (no argument) or write an output level (0/1)."""
        if v is None:
            return self._level
        level = 1 if v else 0
        self.history.append(level)
        if self.mode != Pin.IN:
            self._level = level
        return None

    def __call__(self, v=None):
        return self.value(v)

    def toggle(self):
        """Invert the output level."""
        self.value(0 if self._level else 1)

    def init(self, mode=-1, pull=-1, *, value=None):
        """Re-configure the pin (mode, pull, initial output level)."""
        if mode != -1:
            self.mode = mode
        if pull != -1:
            self.pull = pull
        if value is not None:
            self.value(value)

    def on(self):
        """Drive the pin HIGH."""
        self.value(1)

    def off(self):
        """Drive the pin LOW."""
        self.value(0)

    def irq(self, handler=None, trigger=IRQ_FALLING | IRQ_RISING, hard=False):
        """Register (or with ``handler=None`` remove) an edge handler."""
        self._handler = handler
        self.irq_trigger = trigger
        self.irq_hard = hard
        return self

    # --- test helpers -------------------------------------------------------

    def set_input(self, level):
        """Set the input level; call the IRQ handler at once on a matching edge."""
        level = 1 if level else 0
        old = self._level
        self._level = level
        if self._handler is None or old == level:
            return
        if level and self.irq_trigger & Pin.IRQ_RISING:
            self._handler(self)
        elif not level and self.irq_trigger & Pin.IRQ_FALLING:
            self._handler(self)

    @classmethod
    def get(cls, id):
        """Return the Pin instance last created for GPIO ``id``."""
        return cls._instances[id]

    @classmethod
    def preset(cls, id, level):
        """Set the level a later ``Pin(id, Pin.IN, ...)`` reads."""
        cls._presets[id] = 1 if level else 0


class PWM:
    """Fake PWM output.

    Args:
        pin (Pin): the output pin.
        freq (int | None): frequency in Hz.
        duty_u16 (int | None): duty 0-65535.

    Test helpers: ``PWM.get(id)``, ``duty_history``, ``deinited``.
    """

    _instances = {}

    def __init__(self, pin, freq=None, duty_u16=None):
        self.pin = pin
        self.id = pin.id if isinstance(pin, Pin) else pin
        self._freq = 0
        self._duty = 0
        self.duty_history = []
        self.deinited = False
        if freq is not None:
            self.freq(freq)
        if duty_u16 is not None:
            self.duty_u16(duty_u16)
        PWM._instances[self.id] = self

    def freq(self, value=None):
        """Get or set the frequency in Hz (an int, as on the rp2 port)."""
        if value is None:
            return self._freq
        if not isinstance(value, int):
            raise TypeError("freq needs an int")
        self._freq = value
        return None

    def duty_u16(self, value=None):
        """Get or set the duty cycle, 0-65535."""
        if value is None:
            return self._duty
        if not isinstance(value, int):
            raise TypeError("duty_u16 needs an int")
        if not 0 <= value <= 65535:
            raise ValueError("duty_u16 out of range")
        self._duty = value
        self.duty_history.append(value)
        return None

    def deinit(self):
        """Stop the PWM output."""
        self.deinited = True

    @classmethod
    def get(cls, id):
        """Return the PWM instance last created on GPIO ``id``."""
        return cls._instances[id]


class I2CDevice:
    """A scripted register device to attach to the fake I2C bus.

    Attributes:
        regs (bytearray): 256 bytes of register memory for the ``*_mem`` calls.
        received (list[bytes]): raw ``writeto``/``writevto`` payloads.
        read_data (bytearray): bytes returned by ``readfrom`` (zeros when empty).
    """

    def __init__(self, regs=None):
        self.regs = bytearray(256)
        if regs:
            for addr, val in dict(regs).items():
                self.regs[addr] = val
        self.received = []
        self.read_data = bytearray()

    def write(self, data):
        self.received.append(bytes(data))

    def read(self, n):
        out = bytes(self.read_data[:n])
        del self.read_data[:n]
        return out + bytes(n - len(out))

    def write_mem(self, memaddr, data):
        for i, b in enumerate(data):
            self.regs[(memaddr + i) & 0xFF] = b

    def read_mem(self, memaddr, n):
        return bytes(self.regs[(memaddr + i) & 0xFF] for i in range(n))


class I2C:
    """Fake I2C bus. Devices, failures and the log are kept per bus id.

    Args:
        id (int): bus number.
        scl (Pin | None), sda (Pin | None): the bus pins.
        freq (int): clock in Hz.

    Test helpers:
        ``attach(addr, device)`` / ``detach(addr)``: add or remove a device
        (an :class:`I2CDevice` or anything with the same methods).
        ``fail_after(n)``: raise ``OSError(5)`` on every transfer after ``n``
        more succeed (``None`` turns it off).
        ``log``: every successful transfer, as ``(op, addr, ...)`` tuples.
    A missing address raises ``OSError(5)``.
    """

    _buses = {}
    _instances = {}

    def __init__(self, id, scl=None, sda=None, freq=400_000):
        self.id = id
        self.scl = scl
        self.sda = sda
        self.freq = freq
        I2C._buses.setdefault(id, {"devices": {}, "log": [], "left": None})
        I2C._instances[id] = self

    @property
    def _bus(self):
        return I2C._buses[self.id]

    @property
    def log(self):
        return self._bus["log"]

    def attach(self, addr, device):
        """Attach ``device`` at 7-bit address ``addr``."""
        self._bus["devices"][addr] = device

    def detach(self, addr):
        """Remove the device at ``addr`` (it then raises OSError(5))."""
        self._bus["devices"].pop(addr, None)

    def fail_after(self, n):
        """Make every transfer after ``n`` more raise OSError(5)."""
        self._bus["left"] = n

    @classmethod
    def get(cls, id):
        """Return the I2C instance last created for bus ``id``."""
        return cls._instances[id]

    def _transfer(self, addr):
        bus = self._bus
        if bus["left"] is not None:
            if bus["left"] <= 0:
                raise OSError(5)
            bus["left"] -= 1
        dev = bus["devices"].get(addr)
        if dev is None:
            raise OSError(5)
        return dev

    def scan(self):
        """Return the sorted addresses of the attached devices."""
        return sorted(self._bus["devices"])

    def readfrom_mem(self, addr, memaddr, nbytes, *, addrsize=8):
        data = self._transfer(addr).read_mem(memaddr, nbytes)
        self.log.append(("readfrom_mem", addr, memaddr, nbytes))
        return data

    def readfrom_mem_into(self, addr, memaddr, buf, *, addrsize=8):
        data = self._transfer(addr).read_mem(memaddr, len(buf))
        buf[:] = data
        self.log.append(("readfrom_mem_into", addr, memaddr, len(buf)))

    def writeto_mem(self, addr, memaddr, buf, *, addrsize=8):
        self._transfer(addr).write_mem(memaddr, bytes(buf))
        self.log.append(("writeto_mem", addr, memaddr, bytes(buf)))

    def readfrom(self, addr, nbytes, stop=True):
        data = self._transfer(addr).read(nbytes)
        self.log.append(("readfrom", addr, nbytes))
        return data

    def writeto(self, addr, buf, stop=True):
        data = bytes(buf)
        self._transfer(addr).write(data)
        self.log.append(("writeto", addr, data))
        return len(data)

    def writevto(self, addr, vector, stop=True):
        data = b"".join(bytes(v) for v in vector)
        self._transfer(addr).write(data)
        self.log.append(("writevto", addr, data))
        return len(data)


class _Mem32:
    """Dict-like fake of ``machine.mem32``; ``writes`` records (addr, value)."""

    def __init__(self):
        self.values = {}
        self.writes = []

    def __getitem__(self, addr):
        return self.values.get(addr, 0)

    def __setitem__(self, addr, value):
        self.values[addr] = value
        self.writes.append((addr, value))


mem32 = _Mem32()


def time_pulse_us(pin, pulse_level, timeout_us=1000000):
    """Not allowed in the firmware code: it busy-waits for the whole pulse."""
    raise RuntimeError("machine.time_pulse_us busy-waits; use edge IRQs instead")


def disable_irq():
    """Fake: returns a state token for :func:`enable_irq`."""
    return 0


def enable_irq(state=0):
    """Fake: does nothing."""
    return None


def freq(hz=None):
    """Fake CPU frequency (125 MHz)."""
    return 125_000_000 if hz is None else None


def unique_id():
    """Fake 8-byte board id."""
    return b"\x00\x11\x22\x33\x44\x55\x66\x77"


def idle():
    """Fake: does nothing."""
    return None


def _reset():
    """Clear every registry (called by conftest before each test)."""
    Pin._instances.clear()
    Pin._presets.clear()
    PWM._instances.clear()
    I2C._buses.clear()
    I2C._instances.clear()
    mem32.values.clear()
    mem32.writes.clear()
