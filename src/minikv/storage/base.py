"""Abstract storage interface.

Command handlers only interact with data through :class:`StorageBackend`.
This boundary is what allows later phases to add expiry metadata (TTL) or
durability (snapshots, append-only files) without rewriting commands.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class StorageBackend(ABC):
    """Interface that every MiniKV storage backend must implement.

    Implementations must be safe to call from multiple threads.
    """

    @abstractmethod
    def get(self, key: str) -> str | None:
        """Return the value stored at ``key``, or ``None`` if it is absent."""

    @abstractmethod
    def set(self, key: str, value: str) -> None:
        """Store ``value`` at ``key``, overwriting any existing value."""

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Remove ``key``.

        Returns:
            ``True`` if the key existed and was removed, ``False`` otherwise.
        """

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Return ``True`` if ``key`` is present."""

    @abstractmethod
    def keys(self) -> list[str]:
        """Return a snapshot list of all keys currently stored."""

    @abstractmethod
    def clear(self) -> None:
        """Remove every key."""

    @abstractmethod
    def __len__(self) -> int:
        """Return the number of stored keys."""
