"""UUIDv7 identifiers (RFC 9562).

Layout: 48-bit Unix milliseconds | 4-bit version | 12-bit counter | 2-bit variant |
62 random bits. The counter is seeded randomly each millisecond and incremented
for ids generated within the same millisecond, so ids are strictly increasing
within a process, which keeps B-tree inserts append-only.

To stay increasing, the embedded millisecond can run ahead of the wall clock: a
counter overflow borrows the next millisecond, and a clock that steps backwards
keeps the last one used. The embedded time is therefore "not earlier than
creation", not the moment of creation.
"""

import secrets
import threading
import time
from datetime import UTC, datetime
from uuid import UUID

_COUNTER_BITS = 12
_COUNTER_MAX = (1 << _COUNTER_BITS) - 1


class _Uuid7Generator:
    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        self._last_ms: int = 0
        self._counter: int = 0

    def __call__(self) -> UUID:
        with self._lock:
            ms = time.time_ns() // 1_000_000
            if ms > self._last_ms:
                self._last_ms = ms
                # Leave headroom above the seed so the counter rarely overflows.
                self._counter = secrets.randbits(_COUNTER_BITS - 1)
            else:
                self._counter += 1
                if self._counter > _COUNTER_MAX:
                    self._last_ms += 1
                    self._counter = secrets.randbits(_COUNTER_BITS - 1)
            ms, counter = self._last_ms, self._counter
        rand_b = secrets.randbits(62)
        value = (ms << 80) | (0x7 << 76) | (counter << 64) | (0b10 << 62) | rand_b
        return UUID(int=value)


uuid7 = _Uuid7Generator()


def uuid7_time(value: UUID) -> datetime:
    """Return the timestamp embedded in a UUIDv7.

    Returns:
        The UTC datetime encoded in the id's 48-bit millisecond prefix: not earlier
        than the id's creation (to the millisecond), and possibly a little later.

    """
    return datetime.fromtimestamp((value.int >> 80) / 1000, tz=UTC)
