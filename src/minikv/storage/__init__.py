"""Storage backends for MiniKV."""

from minikv.storage.base import StorageBackend
from minikv.storage.memory import InMemoryStorage

__all__ = ["InMemoryStorage", "StorageBackend"]
