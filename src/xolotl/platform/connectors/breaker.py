"""The circuit breaker each connector keeps per cell (spec §8).

``CLOSED`` turns ``OPEN`` after ``FAILURE_THRESHOLD`` failures within
``FAILURE_WINDOW_S`` seconds; ``OPEN`` turns ``HALF_OPEN`` ``OPEN_FOR_S`` seconds
after it opened; ``HALF_OPEN`` admits one trial, whose success closes the breaker and
whose failure reopens it for another ``OPEN_FOR_S`` seconds. The state lives in
process memory and is computed from the clock on every read.
"""

import threading
import time
from collections import deque
from collections.abc import Callable

from xolotl.platform.enums import CircuitState

FAILURE_THRESHOLD = 5
FAILURE_WINDOW_S = 60.0
OPEN_FOR_S = 30.0


class CircuitBreaker:
    """A thread-safe breaker on a monotonic clock, injectable for tests."""

    def __init__(self, *, clock_fn: Callable[[], float] = time.monotonic) -> None:
        self._clock: Callable[[], float] = clock_fn
        self._lock: threading.Lock = threading.Lock()
        self._failures: deque[float] = deque()
        self._opened_at: float | None = None
        self._trial: bool = False

    def _state(self) -> CircuitState:
        if self._opened_at is None:
            return CircuitState.CLOSED
        if self._clock() - self._opened_at < OPEN_FOR_S:
            return CircuitState.OPEN
        return CircuitState.HALF_OPEN

    @property
    def state(self) -> CircuitState:
        """The breaker's state now.

        Returns:
            ``CLOSED``, ``OPEN``, or ``HALF_OPEN`` once ``OPEN_FOR_S`` has elapsed.

        """
        with self._lock:
            return self._state()

    def allow(self) -> bool:
        """Say whether a call may go ahead, claiming the trial when half open.

        Returns:
            True when closed, and for the one trial of a half-open breaker; False
            when open, and while a half-open breaker's trial has not reported.

        """
        with self._lock:
            state = self._state()
            if state is CircuitState.CLOSED:
                return True
            if state is CircuitState.OPEN or self._trial:
                return False
            self._trial = True
            return True

    def record_success(self) -> None:
        """Close the breaker, forget its failures and end any trial."""
        with self._lock:
            self._failures.clear()
            self._opened_at = None
            self._trial = False

    def record_failure(self) -> None:
        """Count a failure; open the breaker at the threshold or on a failed trial."""
        with self._lock:
            now = self._clock()
            state = self._state()
            if state is CircuitState.OPEN:
                # A run that began before the breaker opened; it is open already.
                return
            if state is CircuitState.HALF_OPEN:
                # A failed trial reopens the breaker for a full further period.
                self._opened_at = now
                self._trial = False
                return
            while self._failures and now - self._failures[0] > FAILURE_WINDOW_S:
                _ = self._failures.popleft()
            self._failures.append(now)
            if len(self._failures) >= FAILURE_THRESHOLD:
                self._failures.clear()
                self._opened_at = now
