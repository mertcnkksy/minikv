"""Server-level persistence: restarts, background duties and real processes."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from minikv import Database
from minikv.client import MiniKVClient
from minikv.clock import ManualClock
from minikv.persistence import PersistenceConfig, PersistenceManager
from minikv.server import MiniKVServer
from tests.helpers import (
    ServerThread,
    free_port,
    kill_if_running,
    spawn_minikv,
    stop_process,
    wait_for,
)

Factory = Callable[..., ServerThread]


def _manager(tmp_path: Path, **config: object) -> PersistenceManager:
    config.setdefault("save_rules", ())
    return PersistenceManager(Database(), PersistenceConfig(directory=tmp_path, **config))  # type: ignore[arg-type]


@pytest.mark.parametrize("mode", ["aof", "snapshot"])
def test_data_survives_server_restart(server_factory: Factory, tmp_path: Path, mode: str) -> None:
    options = {"aof_enabled": True} if mode == "aof" else {"save_rules": ((3600, 1),)}
    first = server_factory(persistence=_manager(tmp_path, **options))
    with MiniKVClient(port=first.port) as client:
        client.execute("SET", "user:1", "Ada", "EX", "3600")
        client.execute("SET", "user:2", "Grace")
        client.execute("DEL", "user:2")
    first.stop()

    second = server_factory(persistence=_manager(tmp_path, **options))
    with MiniKVClient(port=second.port) as client:
        assert client.execute("KEYS", "*") == ["user:1"]
        assert client.execute("GET", "user:1") == "Ada"
        ttl = client.execute("TTL", "user:1")
        assert isinstance(ttl, int) and 3590 <= ttl <= 3600


def test_server_cron_runs_automatic_saves(server_factory: Factory, tmp_path: Path) -> None:
    clock = ManualClock()
    db = Database(clock=clock)
    manager = PersistenceManager(
        db, PersistenceConfig(directory=tmp_path, save_rules=((1, 1),))
    )
    running = server_factory(db, persistence=manager, expiry_interval=0.01)
    with MiniKVClient(port=running.port) as client:
        client.execute("SET", "k", "v")
    clock.advance(seconds=1)
    assert wait_for(lambda: manager.changes_since_save == 0)
    assert (tmp_path / "dump.mkv").exists()


def test_persistence_commands_over_the_network(server_factory: Factory, tmp_path: Path) -> None:
    running = server_factory(persistence=_manager(tmp_path, aof_enabled=True))
    with MiniKVClient(port=running.port) as client:
        client.execute("SET", "k", "v")
        assert str(client.execute("SAVE")) == "OK"
        assert isinstance(client.execute("LASTSAVE"), int)
        assert str(client.execute("BGREWRITEAOF")).startswith("Background append only file")
    assert (tmp_path / "dump.mkv").exists()


def test_server_rejects_mismatched_persistence(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="different database"):
        MiniKVServer(Database(), persistence=_manager(tmp_path))
    manager = _manager(tmp_path)
    assert MiniKVServer(persistence=manager).database is manager.database


def _start_process(port: int, data_dir: Path, *extra: str) -> subprocess.Popen[str]:
    return spawn_minikv("serve", "--port", str(port), "--dir", str(data_dir), *extra)


def _wait_ready(port: int) -> MiniKVClient:
    client = MiniKVClient(port=port, timeout=1)

    def ready() -> bool:
        try:
            client.execute("PING")
            return True
        except ConnectionError:
            return False

    assert wait_for(ready, timeout=10), "server did not start"
    return client


def _stop(process: subprocess.Popen[str]) -> str:
    """Stop gracefully (SIGTERM on Unix, CTRL_BREAK_EVENT on Windows)."""
    output = stop_process(process, "SIGTERM")
    assert process.returncode == 0, output
    return output


@pytest.mark.parametrize(
    "options",
    [["--appendonly", "--save", ""], ["--save", "3600 1"]],
    ids=["aof", "snapshot"],
)
def test_real_process_restart_keeps_data(tmp_path: Path, options: list[str]) -> None:
    port = free_port()
    data_dir = tmp_path / "data"  # does not exist yet: the server must create it
    process = _start_process(port, data_dir, *options)
    try:
        client = _wait_ready(port)
        client.execute("SET", "greeting", "merhaba dünya")
        client.execute("SET", "temp", "x", "EX", "3600")
        client.close()
        output = _stop(process)

        process = _start_process(port, data_dir, *options)
        client = _wait_ready(port)
        assert client.execute("GET", "greeting") == "merhaba dünya"
        assert client.execute("EXISTS", "temp") == 1
        client.close()
        output = _stop(process)
        assert "loaded" in output
    finally:
        kill_if_running(process)


def test_corrupted_data_prevents_startup(tmp_path: Path) -> None:
    (tmp_path / "dump.mkv").write_bytes(b"not a snapshot at all, sorry")
    process = _start_process(free_port(), tmp_path)
    output, _ = process.communicate(timeout=15)
    assert process.returncode == 1
    assert "could not start server" in output
    assert (tmp_path / "dump.mkv").read_bytes().startswith(b"not a snapshot")  # untouched
