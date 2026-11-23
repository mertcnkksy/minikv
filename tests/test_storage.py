"""Tests for the in-memory storage backend."""

from __future__ import annotations

import threading

import pytest

from minikv.storage import InMemoryStorage, StorageBackend


def test_is_a_storage_backend(storage: InMemoryStorage) -> None:
    assert isinstance(storage, StorageBackend)


def test_set_and_get(storage: InMemoryStorage) -> None:
    storage.set("name", "Ada")
    assert storage.get("name") == "Ada"


def test_get_missing_returns_none(storage: InMemoryStorage) -> None:
    assert storage.get("missing") is None


def test_set_overwrites(storage: InMemoryStorage) -> None:
    storage.set("k", "1")
    storage.set("k", "2")
    assert storage.get("k") == "2"
    assert len(storage) == 1


def test_empty_string_value_is_stored(storage: InMemoryStorage) -> None:
    storage.set("k", "")
    assert storage.get("k") == ""
    assert storage.exists("k")


def test_delete(storage: InMemoryStorage) -> None:
    storage.set("k", "v")
    assert storage.delete("k") is True
    assert storage.delete("k") is False
    assert storage.get("k") is None


def test_exists(storage: InMemoryStorage) -> None:
    assert storage.exists("k") is False
    storage.set("k", "v")
    assert storage.exists("k") is True


def test_keys_returns_snapshot(storage: InMemoryStorage) -> None:
    storage.set("a", "1")
    storage.set("b", "2")
    keys = storage.keys()
    storage.set("c", "3")
    assert sorted(keys) == ["a", "b"]


def test_clear_and_len(storage: InMemoryStorage) -> None:
    storage.set("a", "1")
    storage.set("b", "2")
    assert len(storage) == 2
    storage.clear()
    assert len(storage) == 0


@pytest.mark.parametrize(
    ("key", "value"),
    [(1, "v"), (None, "v"), ("k", 1), ("k", None), ("k", b"bytes")],
)
def test_set_rejects_non_string(storage: InMemoryStorage, key: object, value: object) -> None:
    with pytest.raises(TypeError):
        storage.set(key, value)  # type: ignore[arg-type]


def test_concurrent_writes_are_all_recorded(storage: InMemoryStorage) -> None:
    def writer(worker: int) -> None:
        for i in range(500):
            storage.set(f"w{worker}:{i}", str(i))

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(storage) == 8 * 500
