from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Callable, Dict


class RateLimiter:
    """Weighted sliding-window quota shared by the workers of one queue.

    Waiting uses monotonic time and can be interrupted without spending quota.
    A request larger than the entire window is rejected instead of blocking
    forever; providers must split such a request or callers must lower its size.
    """

    def __init__(
        self,
        rpm: int = 60,
        window_seconds: float = 60.0,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rpm = max(1, int(rpm))
        self.window_seconds = max(0.1, window_seconds)
        self.events: Dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()
        self._condition = threading.Condition(self.lock)
        self._clock = clock

    def set_rpm(self, rpm: int) -> None:
        with self._condition:
            self.rpm = max(1, int(rpm))
            self._condition.notify_all()

    def acquire(
        self, key: str, cost: int = 1, cancel_event: threading.Event | None = None
    ) -> bool:
        """Return True when admitted, False when cancelled before admission."""
        cost = max(1, int(cost))
        while True:
            with self._condition:
                if cancel_event is not None and cancel_event.is_set():
                    return False
                if cost > self.rpm:
                    raise ValueError(
                        f"This request needs {cost} quota units, above the configured "
                        f"limit of {self.rpm} per window. Reduce images per request."
                    )
                now = self._clock()
                window = self.events[key]
                # drop old events
                while window and now - window[0] >= self.window_seconds:
                    window.popleft()
                if len(window) + cost <= self.rpm:
                    for _ in range(cost):
                        window.append(now)
                    return True
                # Wait for enough units, not just the oldest single unit.
                needed = len(window) + cost - self.rpm
                wait_for = self.window_seconds - (now - window[needed - 1])
                # Event.set() does not notify this condition. A short bounded
                # wait allows cancellation as well as live quota changes.
                self._condition.wait(timeout=max(0.001, min(wait_for, 0.1)))
