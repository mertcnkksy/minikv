"""Tests for EXPIRE-family, TTL-family and PERSIST commands."""

from __future__ import annotations

import pytest

from minikv import Database
from minikv.clock import ManualClock
from minikv.errors import (
    CommandError,
    InvalidExpireTimeError,
    InvalidIntegerError,
    WrongArityError,
)
from minikv.utils import INT64_MAX

# --- EXPIRE / PEXPIRE ---------------------------------------------------------


def test_expire_sets_ttl_and_key_expires(db: Database, clock: ManualClock) -> None:
    db.execute("SET", "k", "v")
    assert db.execute("EXPIRE", "k", "10") == 1
    assert db.execute("TTL", "k") == 10
    clock.advance(seconds=10)
    assert db.execute("GET", "k") is None
    assert db.execute("TTL", "k") == -2


def test_pexpire(db: Database, clock: ManualClock) -> None:
    db.execute("SET", "k", "v")
    assert db.execute("PEXPIRE", "k", "250") == 1
    assert db.execute("PTTL", "k") == 250
    clock.advance(milliseconds=250)
    assert db.execute("EXISTS", "k") == 0


def test_expire_missing_key_returns_zero(db: Database) -> None:
    assert db.execute("EXPIRE", "nope", "10") == 0
    assert db.execute("EXISTS", "nope") == 0


@pytest.mark.parametrize("amount", ["0", "-1"])
def test_non_positive_expire_deletes_key(db: Database, amount: str) -> None:
    db.execute("SET", "k", "v")
    assert db.execute("EXPIRE", "k", amount) == 1
    assert db.execute("EXISTS", "k") == 0


def test_expire_overwrites_previous_ttl(db: Database) -> None:
    db.execute("SET", "k", "v", "EX", "100")
    db.execute("EXPIRE", "k", "5")
    assert db.execute("TTL", "k") == 5


def test_expire_rejects_non_integer(db: Database) -> None:
    db.execute("SET", "k", "v")
    with pytest.raises(InvalidIntegerError):
        db.execute("EXPIRE", "k", "soon")


def test_expire_rejects_overflow(db: Database) -> None:
    db.execute("SET", "k", "v")
    with pytest.raises(InvalidExpireTimeError, match="'expire' command"):
        db.execute("EXPIRE", "k", str(INT64_MAX))
    with pytest.raises(InvalidExpireTimeError, match="'pexpire' command"):
        db.execute("PEXPIRE", "k", str(INT64_MAX))


def test_expire_arity(db: Database) -> None:
    with pytest.raises(WrongArityError):
        db.execute("EXPIRE", "k")


# --- EXPIREAT / PEXPIREAT -----------------------------------------------------


def test_expireat(db: Database, clock: ManualClock) -> None:
    db.execute("SET", "k", "v")
    target = clock.time_ms() // 1000 + 30
    assert db.execute("EXPIREAT", "k", str(target)) == 1
    assert db.execute("EXPIRETIME", "k") == target


def test_pexpireat(db: Database, clock: ManualClock) -> None:
    db.execute("SET", "k", "v")
    target = clock.time_ms() + 1_234
    assert db.execute("PEXPIREAT", "k", str(target)) == 1
    assert db.execute("PEXPIRETIME", "k") == target
    assert db.execute("PTTL", "k") == 1_234


def test_expireat_in_the_past_deletes_key(db: Database) -> None:
    db.execute("SET", "k", "v")
    assert db.execute("EXPIREAT", "k", "1") == 1
    assert db.execute("EXISTS", "k") == 0


# --- NX / XX / GT / LT --------------------------------------------------------


def test_expire_nx(db: Database) -> None:
    db.execute("SET", "k", "v")
    assert db.execute("EXPIRE", "k", "10", "NX") == 1
    assert db.execute("EXPIRE", "k", "20", "NX") == 0
    assert db.execute("TTL", "k") == 10


def test_expire_xx(db: Database) -> None:
    db.execute("SET", "k", "v")
    assert db.execute("EXPIRE", "k", "10", "XX") == 0
    assert db.execute("TTL", "k") == -1
    db.execute("EXPIRE", "k", "10")
    assert db.execute("EXPIRE", "k", "20", "xx") == 1
    assert db.execute("TTL", "k") == 20


def test_expire_gt(db: Database) -> None:
    db.execute("SET", "k", "v")
    assert db.execute("EXPIRE", "k", "10", "GT") == 0  # no TTL counts as infinite
    db.execute("EXPIRE", "k", "10")
    assert db.execute("EXPIRE", "k", "5", "GT") == 0
    assert db.execute("EXPIRE", "k", "10", "GT") == 0
    assert db.execute("EXPIRE", "k", "20", "GT") == 1
    assert db.execute("TTL", "k") == 20


def test_expire_lt(db: Database) -> None:
    db.execute("SET", "k", "v")
    assert db.execute("EXPIRE", "k", "100", "LT") == 1  # infinite > 100
    assert db.execute("EXPIRE", "k", "200", "LT") == 0
    assert db.execute("EXPIRE", "k", "50", "LT") == 1
    assert db.execute("TTL", "k") == 50


def test_expire_xx_gt_combination(db: Database) -> None:
    db.execute("SET", "k", "v", "EX", "10")
    assert db.execute("EXPIRE", "k", "20", "XX", "GT") == 1
    assert db.execute("TTL", "k") == 20


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        (("NX", "XX"), "NX and XX, GT or LT"),
        (("NX", "GT"), "NX and XX, GT or LT"),
        (("GT", "LT"), "GT and LT options"),
        (("SOON",), "Unsupported option SOON"),
    ],
)
def test_invalid_expire_flags(db: Database, flags: tuple[str, ...], message: str) -> None:
    db.execute("SET", "k", "v")
    with pytest.raises(CommandError, match=message):
        db.execute("EXPIRE", "k", "10", *flags)
    assert db.execute("TTL", "k") == -1


# --- TTL / PTTL / EXPIRETIME --------------------------------------------------


def test_ttl_codes(db: Database) -> None:
    assert db.execute("TTL", "missing") == -2
    assert db.execute("PTTL", "missing") == -2
    assert db.execute("EXPIRETIME", "missing") == -2
    assert db.execute("PEXPIRETIME", "missing") == -2
    db.execute("SET", "k", "v")
    assert db.execute("TTL", "k") == -1
    assert db.execute("PTTL", "k") == -1
    assert db.execute("EXPIRETIME", "k") == -1
    assert db.execute("PEXPIRETIME", "k") == -1


def test_ttl_rounds_to_nearest_second(db: Database, clock: ManualClock) -> None:
    db.execute("SET", "k", "v", "PX", "10000")
    clock.advance(milliseconds=4_400)  # 5.6 s left
    assert db.execute("TTL", "k") == 6
    clock.advance(milliseconds=200)    # 5.4 s left
    assert db.execute("TTL", "k") == 5
    assert db.execute("PTTL", "k") == 5_400


# --- PERSIST ------------------------------------------------------------------


def test_persist(db: Database, clock: ManualClock) -> None:
    assert db.execute("PERSIST", "missing") == 0
    db.execute("SET", "k", "v")
    assert db.execute("PERSIST", "k") == 0
    db.execute("EXPIRE", "k", "1")
    assert db.execute("PERSIST", "k") == 1
    clock.advance(seconds=5)
    assert db.execute("GET", "k") == "v"
    assert db.execute("TTL", "k") == -1


# --- Interaction with core commands ------------------------------------------


def test_expired_keys_vanish_from_keys_exists_and_del(db: Database, clock: ManualClock) -> None:
    db.execute("SET", "temp", "v", "EX", "1")
    db.execute("SET", "perm", "v")
    clock.advance(seconds=1)
    assert db.execute("KEYS", "*") == ["perm"]
    assert db.execute("EXISTS", "temp", "perm") == 1
    assert db.execute("DEL", "temp") == 0


def test_engine_actively_purges_expired_keys(db: Database, clock: ManualClock) -> None:
    for i in range(10):
        db.execute("SET", f"k{i}", "v", "PX", "100")
    clock.advance(milliseconds=100)
    db.execute("PING")  # any command triggers the active expiry cycle
    assert len(db.storage._data) == 0  # noqa: SLF001 - memory really reclaimed


def test_database_rejects_mismatched_clock(clock: ManualClock) -> None:
    from minikv.storage import InMemoryStorage

    with pytest.raises(ValueError):
        Database(storage=InMemoryStorage(clock=clock), clock=ManualClock())
    database = Database(storage=InMemoryStorage(clock=clock), clock=clock)
    assert database.clock is clock
