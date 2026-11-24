"""Dictionary-based, thread-safe in-memory storage with key expiry.

Expiry uses two complementary strategies, like Redis:

* **Lazy expiration** - every read checks the requested key and deletes it
  if its time has passed, so callers never observe an expired value.
* **Active expiration** - :meth:`InMemoryStorage.remove_expired` reclaims the
  memory of expired keys nobody reads. Expiry times are kept in a min-heap,
  so each call costs ``O(k log n)`` for ``k`` expired keys and is nearly free
  when nothing is due.

Heap entries are invalidated lazily: when a key's expiry changes, the old
entry stays in the heap and is skipped when popped. The heap is rebuilt
whenever stale entries clearly outnumber live ones.
"""

from __future__ import annotations

import heapq
import threading

from minikv.clock import Clock, SystemClock
from minikv.storage.base import StorageBackend

_HEAP_COMPACTION_SLACK = 64
"""Extra stale heap entries tolerated before the heap is rebuilt."""


def _require_str(name: str, value: object) -> None:
    """Raise :class:`TypeError` unless ``value`` is a string."""
    if not isinstance(value, str):
        raise TypeError(f"{name} must be str, got {type(value).__name__}")


def _require_int(name: str, value: object) -> None:
    """Raise :class:`TypeError` unless ``value`` is an ``int`` (not ``bool``)."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be int, got {type(value).__name__}")


class InMemoryStorage(StorageBackend):
    """Store keys and values in memory with optional per-key expiry.

    Args:
        clock: Time source used for expiry decisions. Defaults to
            :class:`~minikv.clock.SystemClock`.
    """

    def __init__(self, clock: Clock | None = None) -> None:
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._data: dict[str, str] = {}
        self._expires: dict[str, int] = {}
        self._heap: list[tuple[int, str]] = []
        self._dirty = 0
        self._lock = threading.RLock()

    # -- internal helpers (call with the lock held) ---------------------------

    def _is_due(self, key: str, now: int) -> bool:
        expire_at = self._expires.get(key)
        return expire_at is not None and expire_at <= now

    def _remove(self, key: str) -> None:
        self._data.pop(key, None)
        self._expires.pop(key, None)

    def _expire_if_due(self, key: str) -> None:
        """Lazily delete ``key`` if its expiry time has passed."""
        if self._is_due(key, self._clock.time_ms()):
            self._remove(key)

    def _track_expiry(self, key: str, expire_at: int) -> None:
        self._expires[key] = expire_at
        heapq.heappush(self._heap, (expire_at, key))
        if len(self._heap) > 2 * len(self._expires) + _HEAP_COMPACTION_SLACK:
            self._heap = [(when, name) for name, when in self._expires.items()]
            heapq.heapify(self._heap)

    # -- StorageBackend API ----------------------------------------------------

    @property
    def clock(self) -> Clock:
        return self._clock

    def get(self, key: str) -> str | None:
        _require_str("key", key)
        with self._lock:
            self._expire_if_due(key)
            return self._data.get(key)

    def set(
        self,
        key: str,
        value: str,
        expire_at: int | None = None,
        *,
        keep_ttl: bool = False,
    ) -> None:
        _require_str("key", key)
        _require_str("value", value)
        if expire_at is not None:
            _require_int("expire_at", expire_at)
            if keep_ttl:
                raise ValueError("expire_at and keep_ttl are mutually exclusive")
        with self._lock:
            self._expire_if_due(key)
            self._data[key] = value
            self._dirty += 1
            if keep_ttl:
                return
            if expire_at is None:
                self._expires.pop(key, None)
            else:
                self._track_expiry(key, expire_at)

    def delete(self, key: str) -> bool:
        _require_str("key", key)
        with self._lock:
            self._expire_if_due(key)
            existed = key in self._data
            if existed:
                self._remove(key)
                self._dirty += 1
            return existed

    def exists(self, key: str) -> bool:
        _require_str("key", key)
        with self._lock:
            self._expire_if_due(key)
            return key in self._data

    def keys(self) -> list[str]:
        with self._lock:
            self.remove_expired()
            return list(self._data)

    def clear(self) -> None:
        with self._lock:
            self._dirty += len(self._data)
            self._data.clear()
            self._expires.clear()
            self._heap.clear()

    def __len__(self) -> int:
        with self._lock:
            self.remove_expired()
            return len(self._data)

    def get_expiry(self, key: str) -> int | None:
        _require_str("key", key)
        with self._lock:
            self._expire_if_due(key)
            return self._expires.get(key)

    def set_expiry(self, key: str, expire_at: int) -> bool:
        _require_str("key", key)
        _require_int("expire_at", expire_at)
        with self._lock:
            self._expire_if_due(key)
            if key not in self._data:
                return False
            self._track_expiry(key, expire_at)
            self._dirty += 1
            return True

    def clear_expiry(self, key: str) -> bool:
        _require_str("key", key)
        with self._lock:
            self._expire_if_due(key)
            removed = self._expires.pop(key, None) is not None
            if removed:
                self._dirty += 1
            return removed

    @property
    def dirty(self) -> int:
        with self._lock:
            return self._dirty

    def items(self) -> list[tuple[str, str, int | None]]:
        with self._lock:
            self.remove_expired()
            return [(key, value, self._expires.get(key)) for key, value in self._data.items()]

    def remove_expired(self) -> int:
        with self._lock:
            now = self._clock.time_ms()
            removed = 0
            while self._heap and self._heap[0][0] <= now:
                expire_at, key = heapq.heappop(self._heap)
                if self._expires.get(key) == expire_at:
                    self._remove(key)
                    removed += 1
            return removed
