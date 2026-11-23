"""Tests for expiry handling in the in-memory storage backend."""

from __future__ import annotations

import pytest

from minikv.clock import ManualClock
from minikv.storage import InMemoryStorage


def _in(clock: ManualClock, ms: int) -> int:
    """Absolute timestamp ``ms`` milliseconds from now."""
    return clock.time_ms() + ms


def test_default_clock_is_system_clock() -> None:
    from minikv.clock import SystemClock

    assert isinstance(InMemoryStorage().clock, SystemClock)


def test_key_disappears_after_expiry(storage: InMemoryStorage, clock: ManualClock) -> None:
    storage.set("k", "v", _in(clock, 1_000))
    clock.advance(milliseconds=999)
    assert storage.get("k") == "v"
    clock.advance(milliseconds=1)
    assert storage.get("k") is None
    assert not storage.exists("k")


def test_expired_key_is_invisible_to_all_reads(
    storage: InMemoryStorage, clock: ManualClock
) -> None:
    storage.set("gone", "v", _in(clock, 10))
    storage.set("stays", "v")
    clock.advance(milliseconds=10)
    assert storage.keys() == ["stays"]
    assert len(storage) == 1
    assert storage.get_expiry("gone") is None
    assert storage.delete("gone") is False
    assert storage.set_expiry("gone", _in(clock, 1_000)) is False
    assert storage.clear_expiry("gone") is False


def test_set_without_expiry_clears_existing_ttl(
    storage: InMemoryStorage, clock: ManualClock
) -> None:
    storage.set("k", "v", _in(clock, 1_000))
    storage.set("k", "v2")
    assert storage.get_expiry("k") is None
    clock.advance(seconds=10)
    assert storage.get("k") == "v2"


def test_keep_ttl_preserves_expiry(storage: InMemoryStorage, clock: ManualClock) -> None:
    deadline = _in(clock, 1_000)
    storage.set("k", "v", deadline)
    storage.set("k", "v2", keep_ttl=True)
    assert storage.get("k") == "v2"
    assert storage.get_expiry("k") == deadline


def test_keep_ttl_on_expired_key_creates_persistent_key(
    storage: InMemoryStorage, clock: ManualClock
) -> None:
    storage.set("k", "v", _in(clock, 10))
    clock.advance(milliseconds=10)
    storage.set("k", "new", keep_ttl=True)
    assert storage.get_expiry("k") is None
    assert storage.get("k") == "new"


def test_expire_at_and_keep_ttl_are_exclusive(storage: InMemoryStorage) -> None:
    with pytest.raises(ValueError):
        storage.set("k", "v", 123, keep_ttl=True)


@pytest.mark.parametrize("bad", [1.5, "100", True])
def test_expire_at_must_be_int(storage: InMemoryStorage, bad: object) -> None:
    with pytest.raises(TypeError):
        storage.set("k", "v", bad)  # type: ignore[arg-type]
    storage.set("k", "v")
    with pytest.raises(TypeError):
        storage.set_expiry("k", bad)  # type: ignore[arg-type]


def test_set_and_clear_expiry(storage: InMemoryStorage, clock: ManualClock) -> None:
    assert storage.set_expiry("missing", _in(clock, 100)) is False
    storage.set("k", "v")
    assert storage.clear_expiry("k") is False
    assert storage.set_expiry("k", _in(clock, 100)) is True
    assert storage.get_expiry("k") == _in(clock, 100)
    assert storage.clear_expiry("k") is True
    clock.advance(seconds=1)
    assert storage.get("k") == "v"


def test_delete_removes_expiry(storage: InMemoryStorage, clock: ManualClock) -> None:
    storage.set("k", "v", _in(clock, 100))
    storage.delete("k")
    storage.set("k", "v")
    clock.advance(seconds=1)
    assert storage.get("k") == "v"


def test_remove_expired_reclaims_unread_keys(
    storage: InMemoryStorage, clock: ManualClock
) -> None:
    for i in range(5):
        storage.set(f"short:{i}", "v", _in(clock, 100))
    storage.set("long", "v", _in(clock, 10_000))
    storage.set("forever", "v")

    assert storage.remove_expired() == 0
    clock.advance(milliseconds=100)
    assert storage.remove_expired() == 5
    assert storage.remove_expired() == 0
    assert sorted(storage.keys()) == ["forever", "long"]


def test_remove_expired_ignores_stale_heap_entries(
    storage: InMemoryStorage, clock: ManualClock
) -> None:
    storage.set("k", "v", _in(clock, 100))
    storage.set_expiry("k", _in(clock, 5_000))   # old heap entry becomes stale
    clock.advance(milliseconds=100)
    assert storage.remove_expired() == 0
    assert storage.get("k") == "v"


def test_heap_is_compacted(storage: InMemoryStorage, clock: ManualClock) -> None:
    storage.set("k", "v")
    for i in range(1_000):
        storage.set_expiry("k", _in(clock, 10_000 + i))
    assert len(storage._heap) < 200  # noqa: SLF001 - verifying an internal invariant


def test_clear_removes_expiry_metadata(storage: InMemoryStorage, clock: ManualClock) -> None:
    storage.set("k", "v", _in(clock, 100))
    storage.clear()
    storage.set("k", "v")
    clock.advance(seconds=1)
    assert storage.get("k") == "v"
    assert storage.remove_expired() == 0
