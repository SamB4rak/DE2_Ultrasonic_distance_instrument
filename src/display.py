"""Driver for the 1.3" OLED display (128 x 64 pixels, I2C).

The screen shows the mode and Wi-Fi state, both distances, a distance bar,
the newest log event, the upload queue, the clock and upload state, and the
sensor faults: 8 text rows of 16 characters in the 8 x 8 font.

Drawing and sending are separate steps:

* ``render()`` and ``message()`` draw into a framebuffer in RAM and never
  touch the I2C bus.
* ``flush()`` sends the framebuffer one 8-pixel page (128 bytes, about 3 ms
  at 400 kHz) per write and lets the other tasks run between pages, so the
  event loop is never held up for longer than one page. Pages that didn't
  change since the last successful send are skipped; they are compared byte
  by byte with a copy of that frame, so even swapped digits are noticed.
* ``show_now()`` sends the whole frame in one go (about 25 ms). It is meant
  only for the boot and fatal screens, before the event loop starts or after
  it has stopped.

A missing or failing display is harmless: ``ok`` turns False, drawing still
works, and ``flush()`` tries to start the display again at most once every
``settings.DISPLAY_RETRY_MS``.

The controller is an SH1106 (132-column memory, the visible 128 columns
start at column 2) or, as a fallback, an SSD1306
(``settings.OLED_CONTROLLER``).

Credits:
    Uses two vendored MIT-licensed drivers in ``lib/``: ``sh1106.py`` by
    Radomir Dopieralski, Robert Hammelrath and Tim Weber
    (https://github.com/robert-hh/SH1106) and ``ssd1306.py`` from
    micropython-lib (https://github.com/micropython/micropython-lib). The
    rest of this file is new code written for this project.
"""

import asyncio
import time

import framebuf

import board
import logic
import settings
import sh1106
import ssd1306

_PAGE_BYTES = 128      # bytes per page: one byte per column, 8 pixel rows
_BAR_Y = 24            # top pixel row of the bar (text row 3)
_BAR_INNER_W = 126     # bar width inside its 1-pixel outline, in pixels

_WIFI_TEXT = {"up": "up", "connecting": ".."}
_CLOUD_TEXT = ("ok", "rej")


# --- Driver adapters -----------------------------------------------------------
# The vendored drivers talk to the bus from their constructors and end their
# start-up with a full blocking frame. These two subclasses are the only code
# that relies on the drivers' internals (init_display, show, write_cmd,
# write_data, and the SH1106 driver's i2c and addr attributes):
# constructing them sends nothing, start() sends only the controller's
# set-up commands, and send_page() writes one page.

class _SH1106(sh1106.SH1106_I2C):
    """SH1106 adapter: no bus traffic until ``start()``."""

    def __init__(self, width, height, i2c, addr):
        super().__init__(width, height, i2c, addr=addr)
        self._wl = [b"@", None]   # data control byte + page, sent as one write

    def init_display(self):
        """Do nothing: the parent constructor calls this."""

    def show(self, full_update=False):
        """Do nothing: ``Display`` sends the pages itself."""

    def start(self):
        """Send the controller's set-up commands (display on, orientation).

        Raises:
            OSError: the display didn't answer.
        """
        sh1106.SH1106_I2C.init_display(self)

    def send_page(self, page, data):
        """Write one page: page address, column 2 (low, high nibble), data.

        Args:
            page (int): page number 0-7.
            data (memoryview): the page's 128 bytes.

        Raises:
            OSError: the display didn't answer.
        """
        self.write_cmd(0xB0 | page)
        self.write_cmd(0x02)
        self.write_cmd(0x10)
        # Like write_data(), but without building a new 129-byte buffer.
        self._wl[1] = data
        self.i2c.writevto(self.addr, self._wl)


class _SSD1306(ssd1306.SSD1306_I2C):
    """SSD1306 adapter: no bus traffic until ``start()``."""

    def init_display(self):
        """Do nothing: the parent constructor calls this."""

    def show(self):
        """Do nothing: ``Display`` sends the pages itself."""

    def start(self):
        """Send the controller's set-up command list (about 27 commands).

        Raises:
            OSError: the display didn't answer.
        """
        ssd1306.SSD1306_I2C.init_display(self)

    def send_page(self, page, data):
        """Write one page: column window 0-127, page window, then the data.

        Args:
            page (int): page number 0-7.
            data (memoryview): the page's 128 bytes.

        Raises:
            OSError: the display didn't answer.
        """
        self.write_cmd(0x21)
        self.write_cmd(0)
        self.write_cmd(127)
        self.write_cmd(0x22)
        self.write_cmd(page)
        self.write_cmd(page)
        self.write_data(data)


_ADAPTERS = {"sh1106": _SH1106, "ssd1306": _SSD1306}


def _bar_x(m):
    """Pixel column of a distance on the bar.

    Args:
        m (float): distance in metres.

    Returns:
        int: column 1-127 (0 is the outline); 0 m is column 1,
        ``BAR_FULL_SCALE_M`` and beyond is column 127.
    """
    full = settings.BAR_FULL_SCALE_M
    m = min(max(m, 0.0), full)
    return 1 + int(_BAR_INNER_W * m / full + 0.5)


def _same_page(a, b, start):
    """Tell whether one page is byte for byte the same in two frames.

    Args:
        a (bytearray), b (bytearray): two frames of the same size.
        start (int): index of the page's first byte.

    Returns:
        bool: True if all 128 bytes are equal.
    """
    for i in range(start, start + _PAGE_BYTES):
        if a[i] != b[i]:
            return False
    return True


class Display:
    """The OLED display: draws into RAM, sends one page per await.

    Args:
        i2c (machine.I2C): the bus the display is on.
        addr (int | None): 7-bit I2C address; None uses ``board.ADDR_OLED``.
        controller (str | None): ``'sh1106'`` or ``'ssd1306'``; None uses
            ``settings.OLED_CONTROLLER``.
        width (int): width in pixels.
        height (int): height in pixels (a multiple of 8).

    Raises:
        ValueError: unknown controller name (a programming error). A missing
            or failing display never raises: ``ok`` is False instead.
    """

    def __init__(self, i2c, addr=None, controller=None, width=128, height=64):
        if addr is None:
            addr = board.ADDR_OLED
        if controller is None:
            controller = settings.OLED_CONTROLLER
        adapter = _ADAPTERS.get(controller)
        if adapter is None:
            raise ValueError("unknown OLED controller: %r" % (controller,))
        self._drv = adapter(width, height, i2c, addr=addr)
        self._width = width
        self._pages = height // 8
        size = self._pages * width
        self._buf = bytearray(size)
        self._mv = memoryview(self._buf)
        self._fb = framebuf.FrameBuffer(self._buf, width, height,
                                        framebuf.MONO_VLSB)
        self._last = bytearray(size)   # the frame as last sent
        self._all = True               # the display's memory is unknown
        self._ok = False
        self._next_retry = time.ticks_ms()
        try:
            self._drv.start()
            self._ok = True
        except OSError:
            self._failed()

    @property
    def ok(self):
        """bool: True while the display answers on the bus."""
        return self._ok

    def _failed(self):
        """Mark the display missing; the next start is due after the wait."""
        self._ok = False
        self._all = True
        self._next_retry = time.ticks_add(time.ticks_ms(),
                                          int(settings.DISPLAY_RETRY_MS))

    def _send(self, page):
        """Send one page from the framebuffer and remember it as sent.

        Args:
            page (int): page number.

        Raises:
            OSError: the display didn't answer.
        """
        a = page * self._width
        b = a + self._width
        page_view = self._mv[a:b]
        self._drv.send_page(page, page_view)
        self._last[a:b] = page_view

    def _changed(self, page):
        """Tell whether a page must be sent.

        Args:
            page (int): page number.

        Returns:
            bool: True if the display's copy is unknown or differs.
        """
        return self._all or not _same_page(self._buf, self._last,
                                           page * self._width)

    def render(self, view):
        """Draw the whole screen into the framebuffer (no I2C traffic).

        Args:
            view: any object with the shared state's attributes ``mode``
                ('RUN' | 'OFF'), ``d1``, ``d2``, ``d`` (metres or None),
                ``wifi`` ('up' | 'connecting' | 'down'), ``faults`` (set of
                'S1', 'S2', 'OLED'), ``last_event`` (event name or None),
                ``events`` (sized queue), ``synced`` (bool) and ``cloud``
                ('ok' | 'rej' | '--'). Unknown values show as ``--``.
        """
        fb = self._fb
        fb.fill(0)
        faults = view.faults or ()
        mode = view.mode if view.mode in ("RUN", "OFF") else "--"
        wifi = _WIFI_TEXT.get(view.wifi, "--")
        fb.text("%-6sWiFi:%s" % (mode, wifi), 0, 0)
        fb.text(self._dist_text("L", view.d1, "S1" in faults), 0, 8)
        fb.text(self._dist_text("R", view.d2, "S2" in faults), 0, 16)
        self._draw_bar(view.d)
        if view.last_event:
            fb.text("Last %s" % view.last_event, 0, 32)
        try:
            queued = str(len(view.events))
        except TypeError:
            queued = "--"
        fb.text("Queue " + queued, 0, 40)
        clock = "ok" if view.synced is True else "--"
        cloud = view.cloud if view.cloud in _CLOUD_TEXT else "--"
        fb.text("Clock %s  Up %s" % (clock, cloud), 0, 48)
        codes = ""
        if "S1" in faults:
            codes += " L"
        if "S2" in faults:
            codes += " R"
        if codes:
            fb.text("FAULT" + codes, 0, 56)

    @staticmethod
    def _dist_text(label, d, faulty):
        """Text of one distance row.

        Args:
            label (str): 'L' or 'R'.
            d (float | None): distance in metres.
            faulty (bool): the sensor is in the fault set.

        Returns:
            str: e.g. 'L 0.42 m', 'L --' or 'L FAULT'.
        """
        if faulty:
            return label + " FAULT"
        return label + " " + logic.format_m(d)

    def _draw_bar(self, d):
        """Draw the distance bar in text row 3 (pixel rows 24-31).

        A 6-pixel-high outline over the full width, filled from the left by
        ``logic.bar_fraction(d)``, with full-height tick marks at
        ``ZONE_CLOSE_M`` and ``ZONE_FAR_M``; the ticks stick out above and
        below the outline, so they stay visible over the fill.

        Args:
            d (float | None): distance in metres.
        """
        fb = self._fb
        fb.rect(0, _BAR_Y + 1, self._width, 6, 1)
        fill_w = int(_BAR_INNER_W * logic.bar_fraction(d) + 0.5)
        if fill_w > 0:
            fb.fill_rect(1, _BAR_Y + 2, fill_w, 4, 1)
        fb.vline(_bar_x(settings.ZONE_CLOSE_M), _BAR_Y, 8, 1)
        fb.vline(_bar_x(settings.ZONE_FAR_M), _BAR_Y, 8, 1)

    def message(self, line1, line2=""):
        """Draw a boot or fatal screen into the framebuffer (no I2C traffic).

        Args:
            line1 (str): first line (text row 3), cut to 16 characters.
            line2 (str): second line (text row 5), cut to 16 characters.
        """
        self._fb.fill(0)
        self._fb.text(str(line1)[:16], 0, 24)
        if line2:
            self._fb.text(str(line2)[:16], 0, 40)

    async def flush(self):
        """Send the changed pages, one page per write, yielding between them.

        Each page takes about 3 ms of bus time at 400 kHz, and
        ``await asyncio.sleep_ms(0)`` follows every page (sent or compared
        and skipped), so the other tasks run in between. Unchanged pages are
        not sent. At 100 kHz one page takes about 12 ms.

        While ``ok`` is False it tries to start the display again, at most
        once every ``settings.DISPLAY_RETRY_MS``; after a successful start
        it sends all pages. An ``OSError`` sets ``ok`` False and returns.
        """
        if not self._ok:
            now = time.ticks_ms()
            if time.ticks_diff(now, self._next_retry) < 0:
                return
            self._next_retry = time.ticks_add(now,
                                              int(settings.DISPLAY_RETRY_MS))
            try:
                self._drv.start()
            except OSError:
                return
            self._ok = True
            self._all = True
        try:
            for page in range(self._pages):
                if self._changed(page):
                    self._send(page)
                await asyncio.sleep_ms(0)
            self._all = False
        except OSError:
            self._failed()

    def show_now(self):
        """Send the whole framebuffer at once, blocking (about 25 ms).

        Only for the boot and fatal screens, before the event loop starts
        or after it has stopped. Does nothing while ``ok`` is False. Never
        raises: a failure sets ``ok`` False.
        """
        if not self._ok:
            return
        try:
            for page in range(self._pages):
                self._send(page)
            self._all = False
        except Exception:  # never raises: it also runs on the fatal path
            self._failed()
