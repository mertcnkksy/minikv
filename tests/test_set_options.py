"""Tests for SET's conditional and expiry options."""

from __future__ import annotations

import pytest

from minikv import Database
from minikv.clock import ManualClock
from minikv.errors import CommandSyntaxError, InvalidExpireTimeError, InvalidIntegerError
from minikv.responses import OK
from minikv.utils import INT64_MAX


def test_set_ex(db: Database, clock: ManualClock) -> None:
    assert db.execute("SET", "k", "v", "EX", "10") == OK
    assert db.execute("PTTL", "k") == 10_000
    clock.advance(seconds=10)
    assert db.execute("GET", "k") is None


def test_set_px(db: Database, clock: ManualClock) -> None:
    db.execute("SET", "k", "v", "PX", "1500")
    clock.advance(milliseconds=1_499)
    assert db.execute("GET", "k") == "v"
    clock.advance(milliseconds=1)
    assert db.execute("GET", "k") is None


def test_set_exat_and_pxat(db: Database, clock: ManualClock) -> None:
    now = clock.time_ms()
    db.execute("SET", "a", "v", "EXAT", str(now // 1000 + 100))
    db.execute("SET", "b", "v", "PXAT", str(now + 2_500))
    assert db.execute("EXPIRETIME", "a") == now // 1000 + 100
    assert db.execute("PEXPIRETIME", "b") == now + 2_500


def test_set_exat_in_the_past_expires_immediately(db: Database) -> None:
    assert db.execute("SET", "k", "v", "EXAT", "1") == OK
    assert db.execute("GET", "k") is None


def test_options_are_case_insensitive(db: Database) -> None:
    assert db.execute("set", "k", "v", "ex", "5", "nx") == OK
    assert db.execute("TTL", "k") == 5


def test_plain_set_removes_ttl(db: Database) -> None:
    db.execute("SET", "k", "v", "EX", "10")
    db.execute("SET", "k", "v2")
    assert db.execute("TTL", "k") == -1


def test_keepttl(db: Database, clock: ManualClock) -> None:
    db.execute("SET", "k", "v", "EX", "10")
    clock.advance(seconds=4)
    db.execute("SET", "k", "v2", "KEEPTTL")
    assert db.execute("GET", "k") == "v2"
    assert db.execute("TTL", "k") == 6


def test_nx(db: Database) -> None:
    assert db.execute("SET", "k", "1", "NX") == OK
    assert db.execute("SET", "k", "2", "NX") is None
    assert db.execute("GET", "k") == "1"


def test_nx_succeeds_on_expired_key(db: Database, clock: ManualClock) -> None:
    db.execute("SET", "lock", "a", "EX", "1")
    clock.advance(seconds=1)
    assert db.execute("SET", "lock", "b", "NX") == OK


def test_xx(db: Database) -> None:
    assert db.execute("SET", "k", "1", "XX") is None
    assert db.execute("EXISTS", "k") == 0
    db.execute("SET", "k", "1")
    assert db.execute("SET", "k", "2", "XX") == OK
    assert db.execute("GET", "k") == "2"


def test_failed_condition_does_not_touch_ttl(db: Database) -> None:
    db.execute("SET", "k", "v", "EX", "10")
    assert db.execute("SET", "k", "v2", "NX", "EX", "100") is None
    assert db.execute("TTL", "k") == 10


@pytest.mark.parametrize(
    "options",
    [
        ("NX", "XX"),
        ("NX", "NX"),
        ("EX", "10", "PX", "100"),
        ("EX", "10", "KEEPTTL"),
        ("KEEPTTL", "EX", "10"),
        ("KEEPTTL", "KEEPTTL"),
        ("EX",),
        ("BOGUS",),
        ("GET",),
    ],
)
def test_invalid_option_combinations(db: Database, options: tuple[str, ...]) -> None:
    with pytest.raises(CommandSyntaxError):
        db.execute("SET", "k", "v", *options)


@pytest.mark.parametrize("amount", ["0", "-5"])
def test_non_positive_expiry_is_rejected(db: Database, amount: str) -> None:
    with pytest.raises(InvalidExpireTimeError, match="'set' command"):
        db.execute("SET", "k", "v", "EX", amount)
    assert db.execute("EXISTS", "k") == 0


@pytest.mark.parametrize("amount", ["ten", "1.5", "+3"])
def test_non_integer_expiry_is_rejected(db: Database, amount: str) -> None:
    with pytest.raises(InvalidIntegerError):
        db.execute("SET", "k", "v", "PX", amount)


def test_overflowing_expiry_is_rejected(db: Database) -> None:
    with pytest.raises(InvalidExpireTimeError):
        db.execute("SET", "k", "v", "EX", str(INT64_MAX // 1000 + 1))
    with pytest.raises(InvalidExpireTimeError):
        db.execute("SET", "k", "v", "PX", str(INT64_MAX))
