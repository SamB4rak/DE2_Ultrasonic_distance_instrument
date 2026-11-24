"""Shared state of the instrument and the queue of logged events.

One ``State`` object is shared by all tasks. Each attribute has exactly one
task that writes it; the others only read it. The ``EventQueue`` holds the
logged events until they are uploaded: the ranging task adds them, the logger
task reads, drops and expires them. Neither needs a lock, because asyncio
never interrupts a method halfway.

New code for this project.
"""

import time

import settings

# Events older than this are removed: ticks_diff wraps after about 6.2 days,
# so older events would get wrong time stamps and break the time order.
_MAX_AGE_MS = 4 * 24 * 3600 * 1000      # 4 days = 345 600 000 ms


class EventQueue:
    """Bounded, time-ordered queue of event tuples, oldest first.

    Each event is the tuple from ``logic.EventDetector.update()``:
    ``(t_ms, kind, mode, d, d1, d2, zone, closest, duration_s)``.

    Args:
        maxlen (int): the most events kept; when full, the oldest goes.

    Attributes:
        dropped (int): events removed without being uploaded (overflow or
            expiry), counted since the start.
    """

    def __init__(self, maxlen):
        self._q = []
        self._maxlen = maxlen
        self.dropped = 0

    def add(self, ev):
        """Insert an event in time order.

        It goes after every event with the same or an earlier ``t_ms``
        (compared with ``time.ticks_diff``), so events with equal times keep
        their arrival order. Usually that is the end; a LEAVE, which carries
        an earlier time stamp, may go a few places earlier. When the queue is
        then over its length, the oldest event is dropped and counted.

        Args:
            ev (tuple): an event tuple; ``ev[0]`` is its ``ticks_ms`` stamp.
        """
        q = self._q
        t = ev[0]
        i = len(q)
        while i > 0 and time.ticks_diff(q[i - 1][0], t) > 0:
            i -= 1
        q.insert(i, ev)
        if len(q) > self._maxlen:
            q.pop(0)
            self.dropped += 1

    def peek(self):
        """All queued events, oldest first.

        Returns:
            list[tuple]: the queue's own list. The caller must not modify it.
        """
        return self._q

    def drop(self, n):
        """Remove the n oldest events (all of them if fewer are queued).

        Args:
            n (int): how many to remove. The logger passes the number it
                uploaded, less the events dropped meanwhile (overflow or
                expiry), which were the oldest of its batch.
        """
        if n > 0:
            del self._q[:n]

    def expire(self, now_ms):
        """Remove events older than 4 days and count them in ``dropped``.

        Args:
            now_ms (int): the current ``time.ticks_ms()``.
        """
        q = self._q
        while q and time.ticks_diff(now_ms, q[0][0]) > _MAX_AGE_MS:
            q.pop(0)
            self.dropped += 1

    def __len__(self):
        return len(self._q)


class State:
    """The single shared object. Each attribute has exactly one writer task.

    Attributes:
        mode (str): ``'RUN'`` or ``'OFF'``. Writer: mode_task.
        d1 (float | None): left distance in m (clamped, filtered), None if
            there is no value. Writer: ranging_task.
        d2 (float | None): right distance in m. Writer: ranging_task.
        d (float | None): the nearer valid distance in m. Writer: ranging_task.
        zone (str): one of ``logic.ZONES``. Writer: ranging_task.
        last_event (str | None): the newest event's name, e.g. ``'ENTER'``.
            Writer: ranging_task.
        events (EventQueue): ``add()`` by ranging_task; ``peek()``,
            ``drop()`` and ``expire()`` by logger_task.
        wifi (str): ``'up'``, ``'connecting'`` or ``'down'``. Writer:
            logger_task.
        synced (bool): the clock is set from NTP. Writer: logger_task.
        cloud (str): the last upload's result, ``'ok'``, ``'rej'`` or
            ``'--'``. Writer: logger_task.
        faults (set[str]): a subset of ``{'S1', 'S2', 'OLED'}``. ranging_task
            adds and removes S1 and S2, display_task OLED.
    """

    def __init__(self):
        self.mode = "RUN"
        self.d1 = None
        self.d2 = None
        self.d = None
        self.zone = "clear"
        self.last_event = None
        self.events = EventQueue(settings.EVENT_QUEUE_LEN)
        self.wifi = "down"
        self.synced = False
        self.cloud = "--"
        self.faults = set()
