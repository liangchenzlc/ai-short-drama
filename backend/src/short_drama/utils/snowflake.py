import threading
import time
from collections.abc import Callable
from functools import lru_cache

_initialization_lock = threading.Lock()


class ClockMovedBackwards(RuntimeError):
    """Generation stops on clock rollback rather than risking duplicate IDs."""


class SnowflakeGenerator:
    """63-bit Snowflake: 41 timestamp + 10 worker + 12 sequence bits.

    Each live process must have a distinct worker_id, including uvicorn workers.
    Reuse a worker only after its previous process has stopped and the wall clock
    has advanced past all timestamps it used. Epoch is 2024-01-01T00:00:00Z.
    """

    EPOCH_MS = 1704067200000

    def __init__(self, worker_id: int, clock: Callable[[], int] | None = None):
        if type(worker_id) is not int or not 0 <= worker_id <= 1023:
            raise ValueError("worker_id must be an integer between 0 and 1023")
        self.worker_id = worker_id
        self._clock = clock or (lambda: time.time_ns() // 1_000_000)
        self._last_ms = -1
        self._sequence = 0
        self._lock = threading.Lock()

    def next_id(self) -> int:
        with self._lock:
            now = self._clock()
            if now < self._last_ms:
                raise ClockMovedBackwards("Clock moved backwards; ID generation refused")
            if now == self._last_ms:
                if self._sequence == 4095:
                    deadline = time.monotonic() + 1
                    while now <= self._last_ms:
                        if time.monotonic() >= deadline:
                            raise ClockMovedBackwards(
                                "Clock did not advance after sequence overflow"
                            )
                        time.sleep(0.0001)
                        now = self._clock()
                        if now < self._last_ms:
                            raise ClockMovedBackwards(
                                "Clock moved backwards during sequence overflow"
                            )
                    self._sequence = 0
                else:
                    self._sequence += 1
            else:
                self._sequence = 0
            elapsed = now - self.EPOCH_MS
            if not 0 <= elapsed < 2**41:
                raise ValueError("Clock is outside the Snowflake epoch range")
            self._last_ms = now
            return (elapsed << 22) | (self.worker_id << 12) | self._sequence


@lru_cache(maxsize=1)
def _generator() -> SnowflakeGenerator:
    from short_drama.core.config import Settings

    return SnowflakeGenerator(Settings().snowflake_worker_id)


def next_id() -> int:
    """Process-wide source for primary keys and generation batch IDs."""
    # lru_cache alone can execute its wrapped function twice on concurrent misses.
    with _initialization_lock:
        generator = _generator()
    return generator.next_id()
