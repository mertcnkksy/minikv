"""Tests for group commit: deferred AOF syncing and reply ordering in the server."""

from __future__ import annotations

import asyncio
import os
import socket
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from minikv import Database
from minikv.errors import PersistenceError
from minikv.persistence import AppendOnlyFile, FsyncPolicy, PersistenceConfig, PersistenceManager, load_aof
from minikv.protocol import encode_command
from minikv.server.group_commit import GroupCommitter
from tests.helpers import ServerThread, connect, recv_exactly, recv_until_closed, wait_for

Factory = Callable[..., ServerThread]


@pytest.fixture
def fsyncs(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record every os.fsync call (still performing it)."""
    calls: list[int] = []
    real = os.fsync

    def counting(fd: int) -> None:
        calls.append(fd)
        real(fd)

    monkeypatch.setattr(os, "fsync", counting)
    return calls


# --- AppendOnlyFile in deferred mode -------------------------------------------


def test_deferred_append_waits_for_commit(tmp_path: Path, fsyncs: list[int]) -> None:
    path = tmp_path / "a.aof"
    aof = AppendOnlyFile(path, FsyncPolicy.ALWAYS, deferred=True)
    aof.open()
    assert not aof.needs_commit
    for i in range(50):
        aof.append([["SET", "k", str(i)]])
    assert path.stat().st_size == 0  # still in the process buffer
    assert fsyncs == [] and aof.needs_commit
    aof.commit()
    assert len(fsyncs) == 1  # one fsync for fifty writes
    assert path.stat().st_size == aof.size > 0
    assert not aof.needs_commit
    aof.commit()  # nothing pending: no extra fsync
    assert len(fsyncs) == 1
    aof.close()


@pytest.mark.parametrize("policy", [FsyncPolicy.EVERYSEC, FsyncPolicy.NO])
def test_deferred_commit_flushes_without_fsync(
    tmp_path: Path, fsyncs: list[int], policy: FsyncPolicy
) -> None:
    path = tmp_path / "a.aof"
    aof = AppendOnlyFile(path, policy, deferred=True, monotonic=lambda: 0.0)
    aof.open()
    aof.append([["SET", "k", "v"]])
    assert aof.needs_commit and path.stat().st_size == 0
    aof.commit()
    assert path.stat().st_size > 0 and not aof.needs_commit
    assert fsyncs == []  # the policy, not the commit, decides when to fsync
    aof.close()


def test_non_deferred_mode_is_unchanged(tmp_path: Path, fsyncs: list[int]) -> None:
    aof = AppendOnlyFile(tmp_path / "a.aof", FsyncPolicy.ALWAYS)
    aof.open()
    aof.append([["SET", "a", "1"]])
    aof.append([["SET", "b", "2"]])
    assert len(fsyncs) == 2 and not aof.needs_commit
    aof.close()


def test_close_flushes_deferred_writes(tmp_path: Path) -> None:
    path = tmp_path / "a.aof"
    aof = AppendOnlyFile(path, FsyncPolicy.ALWAYS, deferred=True)
    aof.open()
    aof.append([["SET", "k", "v"]])
    aof.close()
    db = Database()
    assert load_aof(path, db.execute) == 1


def test_commit_errors_become_persistence_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    aof = AppendOnlyFile(tmp_path / "a.aof", FsyncPolicy.ALWAYS, deferred=True)
    aof.open()
    aof.append([["SET", "k", "v"]])

    def failing(fd: int) -> None:
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(os, "fsync", failing)
    with pytest.raises(PersistenceError, match="cannot sync append-only file"):
        aof.commit()
    monkeypatch.undo()
    aof.commit()  # a later commit retries the pending fsync
    assert not aof.needs_commit
    aof.close()


def test_rewrite_handover_in_deferred_mode(tmp_path: Path) -> None:
    path = tmp_path / "a.aof"
    aof = AppendOnlyFile(path, FsyncPolicy.ALWAYS, deferred=True)
    aof.open()
    aof.append([["SET", "old", "1"]])
    aof.commit()
    aof.begin_rewrite()
    aof.append([["SET", "during", "2"]])  # buffered, not yet committed
    new_file = tmp_path / "new.aof"
    new_file.write_bytes(encode_command(["SET", "old", "1"]))
    aof.complete_rewrite(new_file)
    assert not aof.needs_commit  # the new file was fsynced as a whole
    aof.append([["SET", "after", "3"]])
    aof.close()
    db = Database()
    load_aof(path, db.execute)
    assert db.execute("KEYS", "*") == ["after", "during", "old"]


# --- GroupCommitter --------------------------------------------------------------


def test_committer_shares_one_commit_between_waiters() -> None:
    pending = {"value": True}
    commits: list[int] = []

    def commit() -> None:
        commits.append(1)
        pending["value"] = False

    committer = GroupCommitter(lambda: pending["value"], commit)

    async def scenario() -> None:
        await asyncio.gather(*(committer.wait_durable() for _ in range(25)))

    asyncio.run(scenario())
    assert commits == [1] and committer.commits == 1


def test_committer_returns_immediately_when_nothing_is_pending() -> None:
    committer = GroupCommitter(lambda: False, lambda: pytest.fail("must not commit"))
    asyncio.run(committer.wait_durable())
    assert committer.commits == 0


def test_committer_failure_reaches_every_waiter_and_recovers() -> None:
    state = {"fail": True}

    def commit() -> None:
        if state["fail"]:
            raise PersistenceError("disk gone")

    committer = GroupCommitter(lambda: True, commit)

    async def scenario() -> list[object]:
        return await asyncio.gather(
            *(committer.wait_durable() for _ in range(3)), return_exceptions=True
        )

    results = asyncio.run(scenario())
    assert all(isinstance(result, PersistenceError) for result in results)
    state["fail"] = False
    asyncio.run(committer.wait_durable())
    assert committer.commits == 1


# --- Server ------------------------------------------------------------------------


def _always_manager(tmp_path: Path) -> PersistenceManager:
    return PersistenceManager(
        Database(),
        PersistenceConfig(directory=tmp_path, save_rules=(), aof_enabled=True, aof_fsync="always"),
    )


def test_pipelined_writes_share_fsyncs(
    server_factory: Factory, tmp_path: Path, fsyncs: list[int]
) -> None:
    running = server_factory(persistence=_always_manager(tmp_path / "gc"))
    batch = b"".join(encode_command(["SET", f"k{i}", str(i)]) for i in range(100))
    fsyncs.clear()
    with connect(running.port) as sock:
        sock.sendall(batch)
        assert recv_exactly(sock, 5 * 100) == b"+OK\r\n" * 100
    assert 1 <= len(fsyncs) <= 3  # one per read the server performed, not one per write
    assert running.server.group_commits == len(fsyncs)


def test_without_group_commit_every_write_fsyncs(
    server_factory: Factory, tmp_path: Path, fsyncs: list[int]
) -> None:
    running = server_factory(persistence=_always_manager(tmp_path / "nogc"), group_commit=False)
    batch = b"".join(encode_command(["SET", f"k{i}", str(i)]) for i in range(100))
    fsyncs.clear()
    with connect(running.port) as sock:
        sock.sendall(batch)
        assert recv_exactly(sock, 5 * 100) == b"+OK\r\n" * 100
    assert len(fsyncs) == 100
    assert running.server.group_commits == 0


def test_reads_do_not_trigger_commits(server_factory: Factory, tmp_path: Path, fsyncs: list[int]) -> None:
    running = server_factory(persistence=_always_manager(tmp_path))
    fsyncs.clear()
    with connect(running.port) as sock:
        sock.sendall(b"PING\r\nGET missing\r\nKEYS *\r\n")
        assert recv_exactly(sock, 7 + 5 + 4) == b"+PONG\r\n$-1\r\n*0\r\n"
    assert fsyncs == [] and running.server.group_commits == 0


def test_reply_is_never_sent_before_fsync(
    server_factory: Factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    running = server_factory(persistence=_always_manager(tmp_path))
    # Block fsync only once the server is up (startup fsyncs the new AOF file).
    entered, release = threading.Event(), threading.Event()
    real = os.fsync

    def blocking(fd: int) -> None:
        entered.set()
        release.wait(10)
        real(fd)

    monkeypatch.setattr(os, "fsync", blocking)
    try:
        with connect(running.port) as sock:
            sock.sendall(encode_command(["SET", "k", "v"]))
            assert entered.wait(5), "fsync was never called"
            sock.settimeout(0.3)
            with pytest.raises(socket.timeout):
                sock.recv(16)  # no acknowledgement while the data is not on disk
            release.set()
            sock.settimeout(5)
            assert recv_exactly(sock, 5) == b"+OK\r\n"
    finally:
        release.set()


def test_failed_commit_closes_connection_without_acknowledging(
    server_factory: Factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    running = server_factory(persistence=_always_manager(tmp_path))
    real = os.fsync

    def failing(fd: int) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "fsync", failing)
    with connect(running.port) as sock:
        sock.sendall(encode_command(["SET", "k", "v"]))
        assert recv_until_closed(sock) == b""  # closed, never "+OK"
    assert "could not be made durable" in caplog.text

    monkeypatch.setattr(os, "fsync", real)
    with connect(running.port) as sock:  # the server keeps serving other clients
        sock.sendall(b"PING\r\n")
        assert recv_exactly(sock, 7) == b"+PONG\r\n"


def test_concurrent_clients_share_commits(
    server_factory: Factory, tmp_path: Path, fsyncs: list[int]
) -> None:
    running = server_factory(persistence=_always_manager(tmp_path))
    clients = [connect(running.port) for _ in range(20)]
    try:
        fsyncs.clear()
        barrier = threading.Barrier(len(clients))
        replies: list[bytes] = []
        lock = threading.Lock()

        def write(index: int, sock: socket.socket) -> None:
            barrier.wait()
            for j in range(20):
                sock.sendall(encode_command(["SET", f"c{index}:{j}", "v"]))
                reply = recv_exactly(sock, 5)
                with lock:
                    replies.append(reply)

        threads = [threading.Thread(target=write, args=pair) for pair in enumerate(clients)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
    finally:
        for sock in clients:
            sock.close()
    assert replies == [b"+OK\r\n"] * 400
    assert running.db.execute("DBSIZE") == 400
    # Observed: ~40-60 fsyncs for 400 writes. Requiring at least two writes per
    # fsync on average proves sharing across clients with a wide safety margin.
    assert 1 <= len(fsyncs) <= 200
    assert running.server.group_commits == len(fsyncs)


def test_group_committed_data_survives_restart(server_factory: Factory, tmp_path: Path) -> None:
    from minikv.client import MiniKVClient

    first = server_factory(persistence=_always_manager(tmp_path))
    with MiniKVClient(port=first.port) as client:
        client.pipeline([["SET", f"k{i}", str(i)] for i in range(500)])
        client.execute("EXPIRE", "k1", "3600")
        client.execute("DEL", "k2")
    first.stop()

    second = server_factory(persistence=_always_manager(tmp_path))
    assert wait_for(lambda: second.server.is_serving)
    assert second.db.execute("DBSIZE") == 499
    assert second.db.execute("GET", "k499") == "499"
    assert second.db.execute("TTL", "k1") > 0
