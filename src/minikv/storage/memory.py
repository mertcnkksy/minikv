"""Dictionary-based, thread-safe in-memory storage."""

from __future__ import annotations

import threading

from minikv.storage.base import StorageBackend


def _require_str(name: str, value: object) -> None:
    """Raise :class:`TypeError` unless ``value`` is a string."""
    if not isinstance(value, str):
        raise TypeError(f"{name} must be str, got {type(value).__name__}")


class InMemoryStorage(StorageBackend):
    """Store keys and values in a Python ``dict`` guarded by a re-entrant lock.

    The lock is unnecessary for the single-threaded CLI but keeps the backend
    correct once a concurrent network server is introduced.
    """

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> str | None:
        _require_str("key", key)
        with self._lock:
            return self._data.get(key)

    def set(self, key: str, value: str) -> None:
        _require_str("key", key)
        _require_str("value", value)
        with self._lock:
            self._data[key] = value

    def delete(self, key: str) -> bool:
        _require_str("key", key)
        with self._lock:
            return self._data.pop(key, None) is not None

    def exists(self, key: str) -> bool:
        _require_str("key", key)
        with self._lock:
            return key in self._data

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._data)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
