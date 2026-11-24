"""Tests for PersistenceManager: loading, saving, rewriting and automatic duties."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

import minikv.persistence.manager as manager_module
from minikv import Database
from minikv.clock import ManualClock
from minikv.errors import CommandError, PersistenceError
from minikv.persistence import PersistenceConfig, PersistenceManager, read_snapshot
from minikv.protocol import encode_command

Opener = Callable[..., tuple[Database, PersistenceManager]]


@pytest.fixture
def open_db(tmp_path: Path, clock: ManualClock) -> Iterator[Opener]:
    """Open a fresh database on ``tmp_path``, loading what earlier runs saved."""
    managers: list[PersistenceManager] = []

    def opener(**config: Any) -> tuple[Database, PersistenceManager]:
        config.setdefault("directory", tmp_path)
        config.setdefault("save_rules", ())
        db = Database(clock=clock)
        manager = PersistenceManager(db, PersistenceConfig(**config))
        manager.load()
        manager.start()
        managers.append(manager)
        return db, manager

    yield opener
    for manager in managers:
        manager.close(final_save=False)


def test_aof_survives_restart(open_db: Opener) -> None:
    db, manager = open_db(aof_enabled=True)
    db.execute("SET", "a", "1")
    db.execute("SET", "b", "2")
    db.execute("DEL", "a")
    manager.close()

    db2, _ = open_db(aof_enabled=True)
    assert db2.execute("KEYS", "*") == ["b"]


def test_snapshot_on_shutdown_survives_restart(open_db: Opener, tmp_path: Path) -> None:
    db, manager = open_db(save_rules=((3600, 1),))
    db.execute("SET", "k", "v")
    manager.close()
    assert (tmp_path / "dump.mkv").exists()

    db2, manager2 = open_db(save_rules=((3600, 1),))
    assert db2.execute("GET", "k") == "v"
    assert manager2.changes_since_save == 0  # loading does not count as changes


def test_no_shutdown_snapshot_without_rules(open_db: Opener, tmp_path: Path) -> None:
    db, manager = open_db()
    db.execute("SET", "k", "v")
    manager.close()
    assert list(tmp_path.iterdir()) == []


def test_ttl_is_preserved_across_restarts(open_db: Opener, clock: ManualClock) -> None:
    db, manager = open_db(aof_enabled=True)
    db.execute("SET", "session", "x", "EX", "10")
    manager.close()

    clock.advance(seconds=4)
    db2, manager2 = open_db(aof_enabled=True)
    assert db2.execute("TTL", "session") == 6
    manager2.close()

    clock.advance(seconds=6)
    db3, _ = open_db(aof_enabled=True)
    assert db3.execute("GET", "session") is None


def test_expired_keys_are_skipped_when_loading_snapshot(
    open_db: Opener, clock: ManualClock
) -> None:
    db, manager = open_db()
    db.execute("SET", "short", "x", "PX", "100")
    db.execute("SET", "long", "y", "EX", "100")
    manager.save()
    manager.close()

    clock.advance(seconds=1)
    db2, _ = open_db()
    assert db2.execute("KEYS", "*") == ["long"]


def test_aof_takes_priority_over_snapshot(open_db: Opener) -> None:
    db, manager = open_db(aof_enabled=True)
    db.execute("SET", "k", "from-aof")
    manager.close()

    db_snap, manager_snap = open_db()
    db_snap.execute("SET", "k", "from-snapshot")
    manager_snap.save()
    manager_snap.close()

    db2, manager2 = open_db(aof_enabled=True)
    assert db2.execute("GET", "k") == "from-aof"
    manager2.close()


def test_enabling_aof_keeps_snapshot_data(open_db: Opener, tmp_path: Path) -> None:
    db, manager = open_db()
    db.execute("SET", "old", "data")
    manager.save()
    manager.close()

    db2, manager2 = open_db(aof_enabled=True)  # AOF switched on later
    assert db2.execute("GET", "old") == "data"
    db2.execute("SET", "new", "data")
    manager2.close()
    (tmp_path / "dump.mkv").unlink()

    db3, _ = open_db(aof_enabled=True)
    assert db3.execute("KEYS", "*") == ["new", "old"]


@pytest.mark.parametrize("aof_enabled", [True, False])
def test_missing_data_directory_is_created(open_db: Opener, tmp_path: Path, aof_enabled: bool) -> None:
    data_dir = tmp_path / "does" / "not" / "exist"
    db, manager = open_db(directory=data_dir, aof_enabled=aof_enabled, save_rules=((3600, 1),))
    db.execute("SET", "k", "v")
    manager.close()
    db2, _ = open_db(directory=data_dir, aof_enabled=aof_enabled, save_rules=((3600, 1),))
    assert db2.execute("GET", "k") == "v"


def test_corrupted_files_refuse_to_load(tmp_path: Path) -> None:
    (tmp_path / "dump.mkv").write_bytes(b"garbage" * 10)
    manager = PersistenceManager(Database(), PersistenceConfig(directory=tmp_path))
    with pytest.raises(PersistenceError):
        manager.load()


def test_save_and_lastsave(open_db: Opener, clock: ManualClock, tmp_path: Path) -> None:
    db, manager = open_db()
    started = manager.last_save_time()
    db.execute("SET", "k", "v")
    assert manager.changes_since_save == 1
    clock.advance(seconds=30)
    manager.save()
    assert manager.last_save_time() == started + 30
    assert manager.changes_since_save == 0
    assert list(read_snapshot(tmp_path / "dump.mkv")) == [("k", "v", None)]


def test_background_save(open_db: Opener, tmp_path: Path) -> None:
    db, manager = open_db()
    db.execute("SET", "k", "v")
    manager.background_save()
    manager.wait()
    assert not manager.save_in_progress
    assert list(read_snapshot(tmp_path / "dump.mkv")) == [("k", "v", None)]


@pytest.fixture
def slow_snapshot(monkeypatch: pytest.MonkeyPatch) -> threading.Event:
    """Make snapshot writes block until the returned event is set."""
    release = threading.Event()
    real = manager_module.write_snapshot

    def slow(path: Path, items: Any) -> int:
        release.wait(5)
        return real(path, items)

    monkeypatch.setattr(manager_module, "write_snapshot", slow)
    return release


def test_background_save_is_a_consistent_point_in_time(
    open_db: Opener, tmp_path: Path, slow_snapshot: threading.Event
) -> None:
    db, manager = open_db()
    db.execute("SET", "k", "before")
    manager.background_save()
    db.execute("SET", "k", "after")  # must not leak into the snapshot
    db.execute("SET", "extra", "x")
    with pytest.raises(CommandError, match="Background save already in progress"):
        manager.background_save()
    with pytest.raises(CommandError, match="Background save already in progress"):
        manager.save()
    slow_snapshot.set()
    manager.wait()
    assert list(read_snapshot(tmp_path / "dump.mkv")) == [("k", "before", None)]
    assert manager.changes_since_save == 2


def test_failed_save_reports_error(open_db: Opener, tmp_path: Path) -> None:
    db, manager = open_db()
    (tmp_path / "dump.mkv").mkdir()  # a directory where the file should go
    db.execute("SET", "k", "v")
    with pytest.raises(PersistenceError, match="cannot write snapshot"):
        manager.save()
    assert manager.changes_since_save == 1


# --- automatic duties ----------------------------------------------------------


def test_save_rules_trigger_background_save(
    open_db: Opener, clock: ManualClock, tmp_path: Path
) -> None:
    db, manager = open_db(save_rules=((10, 2),))
    db.execute("SET", "a", "1")
    clock.advance(seconds=10)
    manager.cron()
    manager.wait()
    assert not (tmp_path / "dump.mkv").exists()  # only one change so far

    db.execute("SET", "b", "2")
    manager.cron()
    manager.wait()
    assert (tmp_path / "dump.mkv").exists()
    assert manager.changes_since_save == 0

    db.execute("SET", "c", "3")
    db.execute("SET", "d", "4")
    manager.cron()
    manager.wait()
    assert manager.changes_since_save == 2  # 10 s have not passed again yet


def test_failed_auto_save_waits_before_retrying(
    open_db: Opener, clock: ManualClock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, manager = open_db(save_rules=((1, 1),))
    attempts: list[int] = []

    def failing(path: Path, items: Any) -> int:
        attempts.append(1)
        raise OSError("disk unavailable")

    monkeypatch.setattr(manager_module, "write_snapshot", failing)
    db.execute("SET", "k", "v")
    clock.advance(seconds=1)
    manager.cron()
    manager.wait()
    manager.cron()
    manager.wait()
    assert len(attempts) == 1
    clock.advance(seconds=5)
    manager.cron()
    manager.wait()
    assert len(attempts) == 2


@pytest.fixture
def slow_rewrite(monkeypatch: pytest.MonkeyPatch) -> threading.Event:
    """Make AOF rewrites block until the returned event is set."""
    release = threading.Event()
    real = PersistenceManager._write_rewrite_file

    def slow(self: PersistenceManager, path: Path, items: Any) -> None:
        release.wait(5)
        real(self, path, items)

    monkeypatch.setattr(PersistenceManager, "_write_rewrite_file", slow)
    return release


def test_background_rewrite_compacts_and_keeps_concurrent_writes(
    open_db: Opener, tmp_path: Path, slow_rewrite: threading.Event
) -> None:
    db, manager = open_db(aof_enabled=True)
    for i in range(200):
        db.execute("SET", "counter", str(i))
    db.execute("SET", "gone", "x")
    db.execute("DEL", "gone")
    size_before = (tmp_path / "appendonly.aof").stat().st_size

    manager.background_rewrite_aof()
    db.execute("SET", "during", "rewrite")  # buffered and handed over
    with pytest.raises(CommandError, match="already in progress"):
        manager.background_rewrite_aof()
    slow_rewrite.set()
    manager.wait()
    db.execute("SET", "after", "rewrite")
    manager.close()

    data = (tmp_path / "appendonly.aof").read_bytes()
    assert len(data) < size_before / 10
    assert data.startswith(encode_command(["SET", "counter", "199"]))
    db2, _ = open_db(aof_enabled=True)
    assert db2.execute("KEYS", "*") == ["after", "counter", "during"]
    assert db2.execute("GET", "counter") == "199"


def test_failed_rewrite_keeps_old_log(
    open_db: Opener, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, manager = open_db(aof_enabled=True)
    db.execute("SET", "k", "v")

    def broken(self: PersistenceManager, path: Path, items: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(PersistenceManager, "_write_rewrite_file", broken)
    manager.background_rewrite_aof()
    manager.wait()
    db.execute("SET", "k2", "v2")
    manager.close()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["appendonly.aof"]
    db2, _ = open_db(aof_enabled=True)
    assert db2.execute("KEYS", "*") == ["k", "k2"]


def test_rewrite_requires_aof(open_db: Opener) -> None:
    _, manager = open_db()
    with pytest.raises(CommandError, match="not enabled"):
        manager.background_rewrite_aof()


def test_aof_grows_then_auto_rewrites(open_db: Opener, tmp_path: Path) -> None:
    db, manager = open_db(aof_enabled=True, aof_rewrite_min_size=1_000, aof_rewrite_percentage=100)
    for i in range(100):
        db.execute("SET", "k", "v" * 20 + str(i))
    grown = (tmp_path / "appendonly.aof").stat().st_size
    manager.cron()
    manager.wait()
    assert (tmp_path / "appendonly.aof").stat().st_size < grown / 10


# --- lifecycle and configuration ----------------------------------------------


def test_lifecycle_guards(tmp_path: Path) -> None:
    db = Database()
    manager = PersistenceManager(db, PersistenceConfig(directory=tmp_path))
    manager.close()  # closing before start is a no-op
    manager.cron()  # so is cron
    manager.start()
    assert db.persistence is manager
    with pytest.raises(RuntimeError, match="already started"):
        manager.start()
    manager.close(final_save=False)
    assert db.persistence is None
    assert list(tmp_path.iterdir()) == []


def test_config_paths_and_validation(tmp_path: Path) -> None:
    config = PersistenceConfig(directory=str(tmp_path), aof_fsync="always")  # type: ignore[arg-type]
    assert config.snapshot_path == tmp_path / "dump.mkv"
    assert config.aof_path == tmp_path / "appendonly.aof"
    assert config.aof_fsync == "always"
    for bad in (
        {"save_rules": ((0, 1),)},
        {"save_rules": ((10, 0),)},
        {"snapshot_filename": "../escape"},
        {"aof_filename": ""},
        {"aof_fsync": "sometimes"},
        {"aof_rewrite_min_size": -1},
    ):
        with pytest.raises(ValueError):
            PersistenceConfig(**bad)  # type: ignore[arg-type]
