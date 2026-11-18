"""Fake network streams, ``open_connection`` and ``getaddrinfo`` for host tests.

``FakeStream`` acts as both reader and writer, as MicroPython's asyncio
``Stream`` does. As on MicroPython, ``close()`` does nothing and
``wait_closed()`` closes the stream.

The harness refuses the real ``socket.getaddrinfo``,
``socket.create_connection`` and ``asyncio.open_connection`` in every test. A stream is closed after its first
connection, so to script a reconnect, pass ``FakeOpenConnection`` a factory
(e.g. ``lambda: FakeStream([...])``) rather than one stream.

Install the fakes with pytest's ``monkeypatch``::

    stream = FakeStream([b"HTTP/1.0 202 Accepted\\r\\n\\r\\n", b'{"success": true}'])
    monkeypatch.setattr(asyncio, "open_connection", FakeOpenConnection(stream))
    monkeypatch.setattr(socket, "getaddrinfo", FakeGetaddrinfo("184.106.153.149"))
"""

import asyncio

from vtime import CLOCK

# These fakes wait with CPython's float ``asyncio.sleep`` internally. Module
# code must not copy that: on the Pico use ``asyncio.sleep_ms(int)``.


class FakeStream:
    """Scripted reader/writer.

    Args:
        chunks (list): what ``read()`` delivers, in order. Each item is
            ``bytes``, ``(delay_ms, bytes)`` (the chunk arrives after that much
            virtual time) or an exception instance (raised by that read).
            After the last chunk ``read()`` returns ``b""`` (EOF).
        never_answers (bool): ``read()`` waits forever (until cancelled).

    Attributes:
        written (bytearray): everything passed to ``write()``.
        drained (int): how many times ``drain()`` was awaited.
        close_called (bool): ``close()`` was called.
        closed (bool): ``wait_closed()`` was awaited.
    """

    def __init__(self, chunks=(), never_answers=False):
        self._chunks = list(chunks)
        self.never_answers = never_answers
        self.written = bytearray()
        self.drained = 0
        self.close_called = False
        self.closed = False

    def write(self, data):
        if self.closed:
            raise OSError(9, "EBADF (stream closed)")
        self.written += data

    async def drain(self):
        self.drained += 1

    async def read(self, n=-1):
        if self.never_answers:
            await asyncio.get_running_loop().create_future()
        if not self._chunks:
            return b""
        item = self._chunks[0]
        if isinstance(item, BaseException):
            self._chunks.pop(0)
            raise item
        if isinstance(item, tuple):
            delay_ms, data = item
            await asyncio.sleep(delay_ms / 1000)
            self._chunks[0] = item = data
        if n is None or n < 0 or n >= len(item):
            self._chunks.pop(0)
            return bytes(item)
        self._chunks[0] = item[n:]
        return bytes(item[:n])

    async def readline(self):
        line = b""
        while not line.endswith(b"\n"):
            part = await self.read(1)
            if not part:
                break
            line += part
        return line

    def close(self):
        self.close_called = True

    async def wait_closed(self):
        self.closed = True


class FakeOpenConnection:
    """Fake ``asyncio.open_connection(host, port)``.

    Args:
        *results: one per connection attempt, in order: a ``FakeStream``, a
            callable that returns a new ``FakeStream``, or an exception
            instance to raise. The last result repeats.
        delay_ms (int): virtual time each attempt takes.

    Attributes:
        calls (list[tuple]): ``(host, port)`` of every attempt.
        streams (list[FakeStream]): the streams handed out.
    """

    def __init__(self, *results, delay_ms=0):
        if not results:
            raise ValueError("give at least one result")
        self._results = list(results)
        self.delay_ms = delay_ms
        self.calls = []
        self.streams = []

    async def __call__(self, host, port, *args, **kwargs):
        self.calls.append((host, port))
        if self.delay_ms:
            await asyncio.sleep(self.delay_ms / 1000)
        result = self._results.pop(0) if len(self._results) > 1 else self._results[0]
        if isinstance(result, BaseException):
            raise result
        stream = result() if callable(result) and not isinstance(result, FakeStream) else result
        self.streams.append(stream)
        return stream, stream


class FakeGetaddrinfo:
    """Fake blocking ``socket.getaddrinfo``.

    Args:
        *results: per call, in order: an IP string or an exception instance.
            The last result repeats.
        delay_ms (int): how long each call blocks (advances the virtual clock).

    Attributes:
        calls (list[tuple]): ``(host, port)`` of every call.
    """

    def __init__(self, *results, delay_ms=0):
        if not results:
            raise ValueError("give at least one result")
        self._results = list(results)
        self.delay_ms = delay_ms
        self.calls = []

    def __call__(self, host, port, af=0, type=0, proto=0, flags=0):
        self.calls.append((host, port))
        if self.delay_ms:
            CLOCK.advance_ms(self.delay_ms)
        result = self._results.pop(0) if len(self._results) > 1 else self._results[0]
        if isinstance(result, BaseException):
            raise result
        return [(2, 1, 0, "", (result, port))]
