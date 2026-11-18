"""Fake ``framebuf`` module for the host tests (MONO_VLSB only).

Every drawing call writes real MONO_VLSB bits into the caller's buffer: byte
``(y // 8) * stride + x``, bit ``y % 8``. ``text()`` also records
``(s, x, y)`` in ``.texts``; ``fill()`` clears ``.texts``, because each frame
starts with a fill. ``fill_rect()`` and the other calls don't touch
``.texts``, so text that was drawn over stays listed until the next
``fill()``. The glyphs are placeholders, not MicroPython's 8x8 font:
each non-space character fills its 8x8 cell with a pattern derived from its
character code, so tests can check where text landed, not how it looks.
"""

MONO_VLSB = 0
MONO_HLSB = 3
MONO_HMSB = 4


class FrameBuffer:
    """Fake ``framebuf.FrameBuffer``.

    Args:
        buf (bytearray): at least ``stride * ceil(height / 8)`` bytes.
        width (int), height (int): size in pixels.
        format (int): must be ``MONO_VLSB``.
        stride (int | None): bytes per page row (default ``width``).
    """

    def __init__(self, buf, width, height, format, stride=None):
        if format != MONO_VLSB:
            raise ValueError("the fake framebuf supports MONO_VLSB only")
        self.buf = buf
        self.width = width
        self.height = height
        self.stride = width if stride is None else stride
        if len(buf) < self.stride * ((height + 7) // 8):
            raise ValueError("buffer too small")
        self.texts = []

    def pixel(self, x, y, c=None):
        if not (0 <= x < self.width and 0 <= y < self.height):
            return None
        i = (y >> 3) * self.stride + x
        bit = 1 << (y & 7)
        if c is None:
            return 1 if self.buf[i] & bit else 0
        if c:
            self.buf[i] |= bit
        else:
            self.buf[i] &= ~bit & 0xFF
        return None

    def fill(self, c):
        val = 0xFF if c else 0x00
        for i in range(self.stride * ((self.height + 7) // 8)):
            self.buf[i] = val
        self.texts = []

    def fill_rect(self, x, y, w, h, c):
        for yy in range(max(y, 0), min(y + h, self.height)):
            for xx in range(max(x, 0), min(x + w, self.width)):
                self.pixel(xx, yy, c)

    def hline(self, x, y, w, c):
        self.fill_rect(x, y, w, 1, c)

    def vline(self, x, y, h, c):
        self.fill_rect(x, y, 1, h, c)

    def rect(self, x, y, w, h, c, f=False):
        if f:
            self.fill_rect(x, y, w, h, c)
            return
        self.hline(x, y, w, c)
        self.hline(x, y + h - 1, w, c)
        self.vline(x, y, h, c)
        self.vline(x + w - 1, y, h, c)

    def line(self, x1, y1, x2, y2, c):
        dx, dy = abs(x2 - x1), -abs(y2 - y1)
        sx = 1 if x1 < x2 else -1
        sy = 1 if y1 < y2 else -1
        err = dx + dy
        while True:
            self.pixel(x1, y1, c)
            if x1 == x2 and y1 == y2:
                return
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x1 += sx
            if e2 <= dx:
                err += dx
                y1 += sy

    def text(self, s, x, y, c=1):
        s = str(s)
        self.texts.append((s, x, y))
        for n, ch in enumerate(s):
            if ch == " ":
                continue
            code = ord(ch)
            for col in range(7):
                pattern = ((code * (col + 3)) & 0x7F) | 0x01
                for row in range(8):
                    if pattern & (1 << row):
                        self.pixel(x + 8 * n + col, y + row, c)
