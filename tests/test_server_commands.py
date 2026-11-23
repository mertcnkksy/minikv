"""Tests for ECHO, DBSIZE, FLUSHDB and HELLO."""

from __future__ import annotations

import pytest

from minikv import Database, __version__
from minikv.clock import ManualClock
from minikv.errors import CommandError, CommandSyntaxError, UnsupportedProtocolError
from minikv.responses import OK


def test_echo(db: Database) -> None:
    assert db.execute("ECHO", "hello world") == "hello world"


def test_dbsize_counts_live_keys(db: Database, clock: ManualClock) -> None:
    assert db.execute("DBSIZE") == 0
    db.execute("SET", "a", "1")
    db.execute("SET", "b", "2", "PX", "10")
    assert db.execute("DBSIZE") == 2
    clock.advance(milliseconds=10)
    assert db.execute("DBSIZE") == 1


@pytest.mark.parametrize("mode", [(), ("ASYNC",), ("sync",)])
def test_flushdb(db: Database, mode: tuple[str, ...]) -> None:
    db.execute("SET", "a", "1")
    db.execute("SET", "b", "2", "EX", "10")
    assert db.execute("FLUSHDB", *mode) == OK
    assert db.execute("DBSIZE") == 0
    assert db.execute("TTL", "b") == -2


@pytest.mark.parametrize("args", [("LATER",), ("SYNC", "SYNC")])
def test_flushdb_syntax_errors(db: Database, args: tuple[str, ...]) -> None:
    db.execute("SET", "a", "1")
    with pytest.raises(CommandSyntaxError):
        db.execute("FLUSHDB", *args)
    assert db.execute("DBSIZE") == 1


@pytest.mark.parametrize("args", [(), ("2",)])
def test_hello_resp2(db: Database, args: tuple[str, ...]) -> None:
    reply = db.execute("HELLO", *args)
    assert isinstance(reply, list)
    info = dict(zip(reply[::2], reply[1::2], strict=True))
    assert info["server"] == "minikv"
    assert info["version"] == __version__
    assert info["proto"] == 2


def test_hello_resp3_is_refused_with_noproto(db: Database) -> None:
    with pytest.raises(UnsupportedProtocolError) as info:
        db.execute("HELLO", "3")
    assert str(info.value) == "NOPROTO unsupported protocol version"


def test_hello_invalid_arguments(db: Database) -> None:
    with pytest.raises(CommandError, match="Protocol version is not an integer"):
        db.execute("HELLO", "two")
    with pytest.raises(CommandSyntaxError):
        db.execute("HELLO", "2", "AUTH")
