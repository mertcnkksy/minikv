"""Tests for the append-only file: appending, fsync policies, replay and repair."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from minikv import Database
from minikv.errors import PersistenceError
from minikv.persistence import AppendOnlyFile, FsyncPolicy, load_aof
from minikv.protocol import encode_command


class FakeMonotonic:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def fsync_calls(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    real_fsync = os.fsync

    def counting_fsync(fd: int) -> None:
        calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", counting_fsync)
    return calls


def test_append_writes_resp(tmp_path: Path) -> None:
    aof = AppendOnlyFile(tmp_path / "sub" / "a.aof")
    aof.open()
    aof.append([["SET", "k", "v"], ["DEL", "x"]])
    aof.close()
    data = (tmp_path / "sub" / "a.aof").read_bytes()
    assert data == encode_command(["SET", "k", "v"]) + encode_command(["DEL", "x"])
    assert aof.size == len(data) and not aof.is_open


def test_append_requires_open_file(tmp_path: Path) -> None:
    with pytest.raises(PersistenceError, match="not open"):
        AppendOnlyFile(tmp_path / "a.aof").append([["PING"]])


def test_fsync_always(tmp_path: Path, fsync_calls: list[int]) -> None:
    aof = AppendOnlyFile(tmp_path / "a.aof", FsyncPolicy.ALWAYS)
    aof.open()
    for i in range(3):
        aof.append([["SET", "k", str(i)]])
    assert len(fsync_calls) == 3
    aof.close()


def test_fsync_everysec(tmp_path: Path, fsync_calls: list[int]) -> None:
    clock = FakeMonotonic()
    aof = AppendOnlyFile(tmp_path / "a.aof", FsyncPolicy.EVERYSEC, monotonic=clock)
    aof.open()
    aof.append([["SET", "a", "1"]])
    aof.append([["SET", "b", "2"]])
    assert fsync_calls == []
    clock.now += 1.0
    aof.append([["SET", "c", "3"]])
    assert len(fsync_calls) == 1
    clock.now += 0.5
    aof.append([["SET", "d", "4"]])
    aof.sync_if_due()
    assert len(fsync_calls) == 1
    clock.now += 0.5
    aof.sync_if_due()  # the periodic cron flushes the pending write
    assert len(fsync_calls) == 2
    clock.now += 5
    aof.sync_if_due()  # nothing new to sync
    assert len(fsync_calls) == 2
    aof.close()


def test_fsync_no_only_on_close(tmp_path: Path, fsync_calls: list[int]) -> None:
    clock = FakeMonotonic()
    aof = AppendOnlyFile(tmp_path / "a.aof", FsyncPolicy.NO, monotonic=clock)
    aof.open()
    aof.append([["SET", "a", "1"]])
    clock.now += 10
    aof.sync_if_due()
    assert fsync_calls == []
    aof.close()
    assert len(fsync_calls) == 1


def test_load_replays_commands(tmp_path: Path) -> None:
    path = tmp_path / "a.aof"
    path.write_bytes(
        encode_command(["SET", "a", "1"])
        + encode_command(["SET", "b", "2"])
        + encode_command(["DEL", "a"])
    )
    db = Database()
    assert load_aof(path, db.execute) == 3
    assert db.execute("KEYS", "*") == ["b"]


def test_truncated_tail_is_repaired(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "a.aof"
    good = encode_command(["SET", "a", "1"])
    path.write_bytes(good + encode_command(["SET", "b", "2"])[:-3])
    db = Database()
    assert load_aof(path, db.execute) == 1
    assert path.read_bytes() == good
    assert "incomplete command" in caplog.text
    assert db.execute("KEYS", "*") == ["a"]


def test_truncated_tail_can_be_rejected(tmp_path: Path) -> None:
    path = tmp_path / "a.aof"
    original = encode_command(["SET", "a", "1"]) + b"*2\r\n$3\r\nDEL"
    path.write_bytes(original)
    with pytest.raises(PersistenceError, match="incomplete command"):
        load_aof(path, Database().execute, allow_truncated=False)
    assert path.read_bytes() == original


def test_corruption_is_reported_with_offset(tmp_path: Path) -> None:
    path = tmp_path / "a.aof"
    good = encode_command(["SET", "a", "1"])
    path.write_bytes(good + b"*1\r\n:oops\r\n")
    with pytest.raises(PersistenceError, match=f"corrupted at byte {len(good)}"):
        load_aof(path, Database().execute)


def test_invalid_command_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "a.aof"
    path.write_bytes(encode_command(["NOSUCHCOMMAND"]))
    with pytest.raises(PersistenceError, match="invalid command"):
        load_aof(path, Database().execute)


def test_rewrite_handover(tmp_path: Path) -> None:
    path = tmp_path / "a.aof"
    aof = AppendOnlyFile(path)
    aof.open()
    aof.append([["SET", "k", "1"], ["SET", "k", "2"], ["SET", "k", "3"]])
    aof.begin_rewrite()
    aof.append([["SET", "late", "x"]])  # arrives during the rewrite
    new_file = tmp_path / "new.aof"
    new_file.write_bytes(encode_command(["SET", "k", "3"]))
    aof.complete_rewrite(new_file)
    aof.append([["SET", "after", "y"]])  # goes to the new file
    aof.close()

    db = Database()
    assert load_aof(path, db.execute) == 3
    assert db.execute("KEYS", "*") == ["after", "k", "late"]
    assert not new_file.exists()


def test_complete_rewrite_requires_begin(tmp_path: Path) -> None:
    aof = AppendOnlyFile(tmp_path / "a.aof")
    with pytest.raises(PersistenceError, match="no append-only file rewrite"):
        aof.complete_rewrite(tmp_path / "x")


def test_disk_errors_become_persistence_errors(tmp_path: Path) -> None:
    import errno

    class FullDisk:
        def write(self, data: bytes) -> int:
            raise OSError(errno.ENOSPC, "No space left on device")

        def flush(self) -> None: ...

        def close(self) -> None: ...

    aof = AppendOnlyFile(tmp_path / "a.aof")
    aof.open()
    assert aof._file is not None  # noqa: SLF001
    aof._file.close()  # noqa: SLF001
    aof._file = FullDisk()  # type: ignore[assignment]  # noqa: SLF001
    with pytest.raises(PersistenceError, match="No space left on device"):
        aof.append([["SET", "k", "v"]])
