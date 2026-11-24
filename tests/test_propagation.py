"""Tests for the dirty counter and write propagation to listeners."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from minikv import Database
from minikv.clock import ManualClock
from minikv.errors import PersistenceError
from minikv.storage import InMemoryStorage


@pytest.fixture
def logged(db: Database) -> list[list[str]]:
    """Commands propagated by ``db``, flattened."""
    records: list[list[str]] = []

    def listener(commands: Sequence[Sequence[str]]) -> None:
        records.extend(list(command) for command in commands)

    db.add_write_listener(listener)
    return records


# --- dirty counter ------------------------------------------------------------


def test_dirty_counts_only_real_modifications(storage: InMemoryStorage, clock: ManualClock) -> None:
    assert storage.dirty == 0
    storage.set("a", "1")
    storage.set("b", "2", clock.time_ms() + 100)
    assert storage.dirty == 2
    assert storage.delete("missing") is False
    assert storage.clear_expiry("a") is False
    assert storage.set_expiry("missing", 1) is False
    assert storage.dirty == 2
    storage.set_expiry("a", clock.time_ms() + 500)
    storage.clear_expiry("a")
    storage.delete("a")
    assert storage.dirty == 5


def test_expiry_does_not_count_as_modification(storage: InMemoryStorage, clock: ManualClock) -> None:
    storage.set("k", "v", clock.time_ms() + 10)
    before = storage.dirty
    clock.advance(milliseconds=10)
    assert storage.get("k") is None
    assert storage.remove_expired() == 0
    assert storage.dirty == before


def test_clear_counts_removed_keys(storage: InMemoryStorage) -> None:
    storage.clear()
    assert storage.dirty == 0
    storage.set("a", "1")
    storage.set("b", "2")
    storage.clear()
    assert storage.dirty == 4


def test_items_snapshot(storage: InMemoryStorage, clock: ManualClock) -> None:
    deadline = clock.time_ms() + 1_000
    storage.set("a", "1")
    storage.set("b", "2", deadline)
    storage.set("gone", "x", clock.time_ms() + 5)
    clock.advance(milliseconds=5)
    assert sorted(storage.items()) == [("a", "1", None), ("b", "2", deadline)]


# --- propagation --------------------------------------------------------------


def test_reads_and_no_ops_are_not_propagated(db: Database, logged: list[list[str]]) -> None:
    db.execute("SET", "k", "v")
    logged.clear()
    for command in (
        ("GET", "k"), ("EXISTS", "k"), ("KEYS", "*"), ("TTL", "k"), ("PING",), ("DBSIZE",),
        ("DEL", "missing"), ("PERSIST", "k"), ("EXPIRE", "missing", "10"),
        ("SET", "k", "other", "NX"), ("SET", "new", "x", "XX"), ("ECHO", "x"),
    ):
        db.execute(*command)
    assert logged == []


def test_set_is_logged_without_conditions(db: Database, logged: list[list[str]]) -> None:
    db.execute("SET", "k", "v", "NX")
    db.execute("SET", "k", "w", "XX")
    assert logged == [["SET", "k", "v"], ["SET", "k", "w"]]


@pytest.mark.parametrize(
    ("options", "offset_ms"),
    [(("EX", "10"), 10_000), (("PX", "1500"), 1_500)],
)
def test_relative_set_expiry_is_logged_as_absolute(
    db: Database, clock: ManualClock, logged: list[list[str]], options: tuple[str, ...],
    offset_ms: int,
) -> None:
    db.execute("SET", "k", "v", *options)
    assert logged == [["SET", "k", "v", "PXAT", str(clock.time_ms() + offset_ms)]]


def test_exat_is_logged_in_milliseconds(db: Database, clock: ManualClock, logged: list[list[str]]) -> None:
    target = clock.time_ms() // 1000 + 60
    db.execute("SET", "k", "v", "EXAT", str(target))
    assert logged == [["SET", "k", "v", "PXAT", str(target * 1000)]]


def test_keepttl_is_preserved(db: Database, logged: list[list[str]]) -> None:
    db.execute("SET", "k", "v", "KEEPTTL")
    assert logged == [["SET", "k", "v", "KEEPTTL"]]


@pytest.mark.parametrize("command", ["EXPIRE", "PEXPIRE", "EXPIREAT", "PEXPIREAT"])
def test_expire_family_is_logged_as_pexpireat(
    db: Database, clock: ManualClock, logged: list[list[str]], command: str
) -> None:
    db.execute("SET", "k", "v")
    logged.clear()
    amount = {
        "EXPIRE": "30",
        "PEXPIRE": "30000",
        "EXPIREAT": str(clock.time_ms() // 1000 + 30),
        "PEXPIREAT": str(clock.time_ms() + 30_000),
    }[command]
    db.execute(command, "k", amount)
    expected = db.execute("PEXPIRETIME", "k")
    assert logged == [["PEXPIREAT", "k", str(expected)]]


def test_expire_in_the_past_is_logged_as_del(db: Database, logged: list[list[str]]) -> None:
    db.execute("SET", "k", "v")
    logged.clear()
    db.execute("EXPIRE", "k", "-1")
    assert logged == [["DEL", "k"]]


def test_other_writes_are_logged_verbatim(db: Database, logged: list[list[str]]) -> None:
    db.execute("SET", "a", "1")
    db.execute("SET", "b", "2", "EX", "100")
    logged.clear()
    db.execute("del", "a", "missing")
    db.execute("PERSIST", "b")
    db.execute("FLUSHDB")
    assert logged == [["del", "a", "missing"], ["PERSIST", "b"], ["FLUSHDB"]]


def test_failed_commands_are_not_logged(db: Database, logged: list[list[str]]) -> None:
    with pytest.raises(Exception):
        db.execute("SET", "k", "v", "EX", "0")
    with pytest.raises(Exception):
        db.execute("BOGUS")
    assert logged == []


def test_remove_listener(db: Database) -> None:
    calls: list[object] = []
    listener = calls.append
    db.add_write_listener(listener)
    db.execute("SET", "a", "1")
    db.remove_write_listener(listener)
    db.remove_write_listener(listener)  # unknown listeners are ignored
    db.execute("SET", "b", "2")
    assert len(calls) == 1


def test_listener_errors_reach_the_caller(db: Database) -> None:
    def broken(commands: Sequence[Sequence[str]]) -> None:
        raise PersistenceError("disk full")

    db.add_write_listener(broken)
    with pytest.raises(PersistenceError, match="disk full"):
        db.execute("SET", "k", "v")
    assert db.execute("GET", "k") == "v"  # applied in memory (documented limitation)
