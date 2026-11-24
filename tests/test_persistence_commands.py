"""Tests for SAVE, BGSAVE, BGREWRITEAOF and LASTSAVE."""

from __future__ import annotations

from pathlib import Path

import pytest

from minikv import Database
from minikv.clock import ManualClock
from minikv.errors import CommandError
from minikv.persistence import PersistenceConfig, PersistenceManager
from minikv.responses import OK, SimpleString


@pytest.mark.parametrize("command", ["SAVE", "BGSAVE", "BGREWRITEAOF", "LASTSAVE"])
def test_commands_require_persistence(db: Database, command: str) -> None:
    with pytest.raises(CommandError, match="persistence is not configured"):
        db.execute(command)


def _start(tmp_path: Path, clock: ManualClock, **config: object) -> tuple[Database, PersistenceManager]:
    db = Database(clock=clock)
    manager = PersistenceManager(db, PersistenceConfig(directory=tmp_path, save_rules=(), **config))  # type: ignore[arg-type]
    manager.load()
    manager.start()
    return db, manager


def test_save_and_lastsave_commands(tmp_path: Path, clock: ManualClock) -> None:
    db, manager = _start(tmp_path, clock)
    first = db.execute("LASTSAVE")
    db.execute("SET", "k", "v")
    clock.advance(seconds=5)
    assert db.execute("SAVE") == OK
    assert db.execute("LASTSAVE") == first + 5  # type: ignore[operator]
    assert (tmp_path / "dump.mkv").exists()
    manager.close()


def test_bgsave_command(tmp_path: Path, clock: ManualClock) -> None:
    db, manager = _start(tmp_path, clock)
    assert db.execute("BGSAVE") == SimpleString("Background saving started")
    manager.wait()
    assert (tmp_path / "dump.mkv").exists()
    manager.close()


def test_bgrewriteaof_command(tmp_path: Path, clock: ManualClock) -> None:
    db, manager = _start(tmp_path, clock, aof_enabled=True)
    db.execute("SET", "k", "1")
    db.execute("SET", "k", "2")
    assert db.execute("BGREWRITEAOF") == SimpleString(
        "Background append only file rewriting started"
    )
    manager.wait()
    assert (tmp_path / "appendonly.aof").read_bytes().count(b"SET") == 1
    manager.close()


def test_bgrewriteaof_without_aof(tmp_path: Path, clock: ManualClock) -> None:
    db, manager = _start(tmp_path, clock)
    with pytest.raises(CommandError, match="append only file is not enabled"):
        db.execute("BGREWRITEAOF")
    manager.close()


def test_persistence_commands_are_not_logged(tmp_path: Path, clock: ManualClock) -> None:
    db, manager = _start(tmp_path, clock, aof_enabled=True)
    db.execute("SAVE")
    db.execute("LASTSAVE")
    manager.close()
    assert (tmp_path / "appendonly.aof").read_bytes() == b""
