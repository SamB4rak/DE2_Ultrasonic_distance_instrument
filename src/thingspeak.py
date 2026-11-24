"""ThingSpeak upload of the time-stamped distance events.

``make_updates()`` turns queued events into ThingSpeak bulk-update entries:
one entry per second, each with its own ``created_at`` time stamp.
``ThingSpeak.bulk_post()`` sends a list of them as one HTTP/1.0 request
through ``asyncio`` streams, so the other tasks keep running while it waits
for the network.

Plain HTTP on port 80 doesn't block the event loop, but the write key travels
unencrypted. Use a private channel only for this project (the log shows when
someone was near the device) and regenerate the key after the demo.
``bulk_post()`` takes only the list of updates, so an HTTPS version could
replace it without changing its callers.

``resolve()`` blocks: MicroPython has no asynchronous DNS. The logger calls it
only after a (re)connect, or after ``settings.HTTP_FAILS_BEFORE_RESOLVE``
failed uploads in a row, and the IP is cached in between.

The write key is passed in by ``main.py``. This module never prints or logs
it.

New code. The ThingSpeak channel and field use follow lab 7 of the course
(BPC-DE2, VUT FEKT); the blocking ``urequests`` call of the lab is replaced
by asynchronous streams and the bulk-update API.
"""

import asyncio
import gc
import json
import socket
import time

import settings
from logic import EVENT_NAMES, ZONE_CODES
from timesync import TimeSync

_MAX_RESPONSE = 4096     # bytes of the response kept (headers + body)
_READ_CHUNK = 512
_STATUS_MAX = 255        # ThingSpeak's limit for the status text

# Event codes (indexes into EVENT_NAMES).
_SAMPLE = 0
_ZONE = 1
_ENTER = 2
_LEAVE = 3
_MODE = 4
# Rank of each event code for field5: LEAVE > ENTER > MODE > ZONE > SAMPLE.
_RANK = (0, 1, 3, 4, 2)


class ThingSpeak:
    """A ThingSpeak channel written through the bulk-update API.

    Args:
        write_key (str): the channel's write API key. Never printed.
        channel_id (str | int): the channel number.
        host (str | None): None uses ``settings.THINGSPEAK_HOST``.
        port (int | None): None uses ``settings.THINGSPEAK_PORT`` (80).
        timeout_s (int | None): limit for one whole request in seconds;
            None uses ``settings.HTTP_TIMEOUT_S``.
    """

    def __init__(self, write_key, channel_id, host=None, port=None, timeout_s=None):
        self._key = write_key
        self._channel = channel_id
        self._host = settings.THINGSPEAK_HOST if host is None else host
        self._port = settings.THINGSPEAK_PORT if port is None else port
        self._timeout_s = settings.HTTP_TIMEOUT_S if timeout_s is None else timeout_s
        self._ip = None
        # For the bench script: the last response's status code and size.
        self._last_status = None
        self._last_len = 0

    def resolve(self):
        """Look up the server's IP address and cache it. Blocking.

        ``socket.getaddrinfo()`` can block for several seconds (about 7 s per
        DNS server that doesn't answer). Call it only right after a successful
        (re)connect, or after ``settings.HTTP_FAILS_BEFORE_RESOLVE`` failed
        uploads in a row.

        Returns:
            bool: True if an IP is now cached; False on any error, and then
            no IP is kept.
        """
        try:
            self._ip = socket.getaddrinfo(self._host, self._port)[0][-1][0]
            return True
        except Exception:     # OSError (no DNS), or an unexpected reply shape
            self._ip = None
            return False

    @property
    def resolved(self):
        """Whether an IP address is cached.

        Returns:
            bool: True while ``bulk_post()`` has an address to connect to.
        """
        return self._ip is not None

    async def bulk_post(self, updates):
        """Send updates in one bulk-update request, without blocking.

        One HTTP/1.0 ``POST /channels/<channel_id>/bulk_update.json`` with the
        JSON body ``{"write_api_key": <key>, "updates": updates}``. The whole
        exchange (connect, send, read until the server closes) is limited to
        ``timeout_s``, and the connection is always closed.

        Args:
            updates (list[dict]): entries from ``make_updates()``.

        Returns:
            bool | None: True if ThingSpeak answered 2xx (202 in practice)
            with ``"success": true``. False if it rejected the request (any
            4xx, e.g. 401 for a wrong key, or a 2xx without success). None on
            any other status, a network error, a timeout, a response without a
            status line, or when no IP is cached.
        """
        self._last_status = None
        self._last_len = 0
        if self._ip is None:
            return None
        body = json.dumps({"write_api_key": self._key, "updates": updates}).encode()
        head = ("POST /channels/{}/bulk_update.json HTTP/1.0\r\n"
                "Host: {}\r\n"
                "Content-Type: application/json\r\n"
                "Content-Length: {}\r\n"
                "\r\n").format(self._channel, self._host, len(body)).encode()
        try:
            response = await asyncio.wait_for_ms(
                self._exchange(head, body), int(self._timeout_s * 1000))
        except (asyncio.TimeoutError, OSError):
            # A timeout during the connect leaves that socket to the garbage
            # collector; collect now (a few ms, only after a failed upload),
            # so failed tries can't use up the sockets.
            gc.collect()
            return None
        status = _status_code(response)
        self._last_status = status
        return _result(status, response)

    async def _exchange(self, head, body):
        """Connect, send the request, read the response until EOF, close.

        Args:
            head (bytes): the request line and headers, with the blank line.
            body (bytes): the JSON body (written separately, so the request
                is never copied into one more large buffer).

        Returns:
            bytes: at most ``_MAX_RESPONSE`` bytes of the response.
        """
        reader, writer = await asyncio.open_connection(self._ip, self._port)
        try:
            writer.write(head)
            writer.write(body)
            await writer.drain()
            response = b""
            while True:
                # One read returns one network segment: loop until EOF.
                chunk = await reader.read(_READ_CHUNK)
                if not chunk:
                    break
                self._last_len += len(chunk)
                room = _MAX_RESPONSE - len(response)
                if room > 0:
                    response += chunk[:room]
            return response
        finally:
            writer.close()            # on MicroPython this alone does nothing
            try:
                await writer.wait_closed()
            except OSError:
                pass


def _status_code(response):
    """The status code of an HTTP response.

    Args:
        response (bytes): the start of the response.

    Returns:
        int | None: e.g. 202, or None without a parseable status line.
    """
    end = response.find(b"\r\n")
    line = response if end < 0 else response[:end]
    parts = line.split()
    if len(parts) < 2 or not parts[0].startswith(b"HTTP/"):
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def _result(status, response):
    """bulk_post()'s result for a status code and the response.

    Args:
        status (int | None): from ``_status_code()``.
        response (bytes): the whole kept response.

    Returns:
        bool | None: True, False or None as ``bulk_post()`` describes.
    """
    if status is None:
        return None
    if 200 <= status < 300:
        sep = response.find(b"\r\n\r\n")
        body = response[sep + 4:] if sep >= 0 else b""
        try:
            doc = json.loads(body.decode())
        except ValueError:    # not JSON (UnicodeError is a ValueError too)
            return False
        return isinstance(doc, dict) and doc.get("success") is True
    if 400 <= status < 500:
        return False
    return None


def _status_part(event):
    """One event's part of the status text.

    Args:
        event (tuple): an event tuple from ``logic.EventDetector``.

    Returns:
        str: e.g. ``'ZONE clear'``, ``'MODE OFF'``, ``'LEAVE 12 s 0.31 m'``.
    """
    kind = event[1]
    name = EVENT_NAMES[kind]
    if kind == _ZONE:
        return name + " " + str(event[6])
    if kind == _MODE:
        return name + " " + str(event[2])
    if kind == _LEAVE:
        text = name
        if event[8] is not None:
            text += " {} s".format(event[8])
        if event[7] is not None:
            text += " {:.2f} m".format(event[7])
        return text
    return name


def _update(second, events):
    """Merge the events of one second into one ThingSpeak update.

    Args:
        second (int): the Unix second of the update.
        events (list[tuple]): that second's events, in time order.

    Returns:
        dict: ``created_at``, the fields and ``status``.
    """
    latest = events[-1]
    update = {"created_at": TimeSync.stamp(second)}
    for name, value in (("field1", latest[3]), ("field2", latest[4]), ("field3", latest[5])):
        if value is not None:
            update[name] = "{:.2f}".format(value)
    if latest[6] in ZONE_CODES:     # always true for the detector's events
        update["field4"] = ZONE_CODES[latest[6]]
    best = events[0][1]
    leave = None
    for event in events:
        if _RANK[event[1]] > _RANK[best]:
            best = event[1]
        if event[1] == _LEAVE:
            leave = event
    update["field5"] = best
    if leave is not None:
        if leave[7] is not None:
            update["field6"] = "{:.2f}".format(leave[7])
        if leave[8] is not None:
            update["field7"] = int(leave[8])
    update["status"] = "; ".join([_status_part(e) for e in events])[:_STATUS_MAX]
    return update


def make_updates(events, clock, now_ms, after_s=None, max_updates=None):
    """Turn queued events into ThingSpeak updates. Pure: changes nothing.

    - Each event's second is ``clock.unix(t_ms)``, raised to ``after_s + 1``
      if it isn't later than ``after_s``, so every stamp is new to the
      channel (ThingSpeak silently drops an update whose stamp it already
      has).
    - Only settled seconds are used: those before
      ``clock.unix(t_now - (settings.LEAVE_HOLD_MS + 2000))``, because a LEAVE
      is reported up to ``LEAVE_HOLD_MS`` after its own stamp. A second that
      straddles that limit waits, whole, for the next batch.
    - Events of the same second merge into one update (ThingSpeak rejects a
      request with two equal stamps). Its distances and zone come from the
      latest event, ``field5`` from the highest-priority one (LEAVE > ENTER >
      MODE > ZONE > SAMPLE), ``field6``/``field7`` from a LEAVE, and
      ``status`` names every event.
    - A second is never split: the batch stops before the second that would
      exceed ``max_updates``.
    - If the input is ever out of time order, a second earlier than the
      previous one merges into it, so the stamps still increase.

    Args:
        events (list[tuple]): queued event tuples from
            ``logic.EventDetector``, ``(t_ms, kind, mode,
            d, d1, d2, zone, closest, duration_s)``, oldest first.
        clock (TimeSync): gives ``synced`` and ``unix()``.
        now_ms (int): the current ``time.ticks_ms()``.
        after_s (int | None): the newest second ThingSpeak has already
            accepted, or None.
        max_updates (int | None): None uses ``settings.UPLOAD_BATCH_MAX``.

    Returns:
        tuple: ``(updates, n_used, last_s)``: the update dicts; how many
        events from the front of ``events`` went into them; the second of the
        last update (None if there are none). Not synced: ``([], 0, None)``.
    """
    if not clock.synced:
        return [], 0, None
    if max_updates is None:
        max_updates = settings.UPLOAD_BATCH_MAX
    limit = clock.unix(time.ticks_add(now_ms, -(settings.LEAVE_HOLD_MS + 2000)))
    seconds = []      # [second, first index, end index] per update
    for i, event in enumerate(events):
        s = clock.unix(event[0])
        if after_s is not None and s <= after_s:
            s = after_s + 1
        if seconds and s < seconds[-1][0]:
            s = seconds[-1][0]
        if s >= limit:
            break
        if seconds and s == seconds[-1][0]:
            seconds[-1][2] = i + 1
        elif len(seconds) >= max_updates:
            break
        else:
            seconds.append([s, i, i + 1])
    updates = [_update(s, events[a:b]) for s, a, b in seconds]
    if not seconds:
        return [], 0, None
    return updates, seconds[-1][2], seconds[-1][0]
