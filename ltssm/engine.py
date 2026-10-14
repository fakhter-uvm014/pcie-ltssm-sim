"""Discrete-event scheduler.

Author: Farzana Akhter

The whole simulation is a priority queue of ``(t_ns, seq, callback, args)``.
``run()`` pops the earliest entry, advances the global clock to its time and
calls the callback, which may schedule more entries. Nothing ever sleeps; a
12 ms timeout costs one queue entry, not 12 ms of wall time.

``seq`` is a monotonically increasing number. It breaks ties between events
at the same time in the order they were scheduled, which keeps every run
deterministic.
"""

from __future__ import annotations

import heapq
from typing import Any, Callable, List, Tuple


class Engine:
    """Minimal discrete-event engine with integer nanosecond time."""

    def __init__(self) -> None:
        self.now: int = 0
        self._queue: List[Tuple[int, int, Callable[..., Any], tuple]] = []
        self._seq = 0
        self.events_processed = 0

    def schedule(self, delay_ns: int, callback: Callable[..., Any], *args: Any) -> None:
        """Run ``callback(*args)`` ``delay_ns`` nanoseconds from now."""
        if delay_ns < 0:
            raise ValueError("cannot schedule in the past")
        self._seq += 1
        heapq.heappush(self._queue, (self.now + int(delay_ns), self._seq, callback, args))

    def schedule_at(self, t_ns: int, callback: Callable[..., Any], *args: Any) -> None:
        """Run ``callback(*args)`` at absolute time ``t_ns`` (not earlier than now)."""
        self.schedule(max(0, int(t_ns) - self.now), callback, *args)

    def run(self, until_ns: int) -> None:
        """Process events in time order until the queue is empty or time passes ``until_ns``."""
        queue = self._queue
        while queue and queue[0][0] <= until_ns:
            t_ns, _, callback, args = heapq.heappop(queue)
            self.now = t_ns
            self.events_processed += 1
            callback(*args)
        self.now = max(self.now, until_ns)

    @property
    def pending(self) -> int:
        return len(self._queue)
