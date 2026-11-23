"""Abstract storage interface.

Command handlers only interact with data through :class:`StorageBackend`.
Backends own expiry bookkeeping: expired keys must be invisible to every
read method, whether or not they have been physically removed yet. This
keeps command handlers simple and lets a future persistent backend reuse
the same contract.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from minikv.clock import Clock


class StorageBackend(ABC):
    """Interface that every MiniKV storage backend must implement.

    Implementations must be safe to call from multiple threads. Expiry
    timestamps are absolute times in milliseconds since the Unix epoch, as
    reported by :attr:`clock`.
    """

    @property
    @abstractmethod
    def clock(self) -> Clock:
        """The clock used to decide whether keys have expired."""

    @abstractmethod
    def get(self, key: str) -> str | None:
        """Return the value stored at ``key``, or ``None`` if absent or expired."""

    @abstractmethod
    def set(
        self,
        key: str,
        value: str,
        expire_at: int | None = None,
        *,
        keep_ttl: bool = False,
    ) -> None:
        """Store ``value`` at ``key``, overwriting any existing value.

        Args:
            key: The key.
            value: The value.
            expire_at: Absolute expiry time in milliseconds, or ``None`` for a
                key without expiry. Ignored when ``keep_ttl`` is true.
            keep_ttl: Preserve the key's existing expiry instead of replacing it.

        Raises:
            ValueError: If both ``expire_at`` and ``keep_ttl`` are given.
        """

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Remove ``key``.

        Returns:
            ``True`` if a live key was removed, ``False`` otherwise.
        """

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Return ``True`` if ``key`` is present and not expired."""

    @abstractmethod
    def keys(self) -> list[str]:
        """Return a snapshot list of all live keys."""

    @abstractmethod
    def clear(self) -> None:
        """Remove every key and every expiry."""

    @abstractmethod
    def __len__(self) -> int:
        """Return the number of live keys."""

    @abstractmethod
    def get_expiry(self, key: str) -> int | None:
        """Return the absolute expiry time of ``key`` in milliseconds.

        Returns:
            The expiry timestamp, or ``None`` if the key has no expiry or
            does not exist. Use :meth:`exists` to tell those cases apart.
        """

    @abstractmethod
    def set_expiry(self, key: str, expire_at: int) -> bool:
        """Set the absolute expiry time of an existing key.

        Returns:
            ``True`` if the key exists and the expiry was set, ``False`` otherwise.
        """

    @abstractmethod
    def clear_expiry(self, key: str) -> bool:
        """Make ``key`` persistent by removing its expiry.

        Returns:
            ``True`` if an expiry was removed, ``False`` if the key does not
            exist or had no expiry.
        """

    @abstractmethod
    def remove_expired(self) -> int:
        """Physically delete every key whose expiry time has passed.

        Returns:
            The number of keys removed.
        """
