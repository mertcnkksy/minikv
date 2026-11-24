"""Tests for the binary snapshot format and atomic writes."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from minikv.errors import PersistenceError
from minikv.persistence import read_snapshot, write_snapshot
from minikv.persistence.fileutil import atomic_write
from minikv.persistence.snapshot import FORMAT_VERSION, MAGIC

ITEMS = [
    ("plain", "value", None),
    ("ttl", "expires", 1_800_000_000_000),
    ("empty", "", None),
    ("unicode:çay", "İstanbul ☕ şekerli", None),
    ("binary", bytes(range(256)).decode("utf-8", "surrogateescape"), -5),
    ("big", "x" * 100_000, None),
]


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "dump.mkv"
    assert write_snapshot(path, ITEMS) == len(ITEMS)
    assert list(read_snapshot(path)) == ITEMS


def test_empty_dataset(tmp_path: Path) -> None:
    path = tmp_path / "dump.mkv"
    assert write_snapshot(path, []) == 0
    assert list(read_snapshot(path)) == []
    assert path.read_bytes().startswith(MAGIC + struct.pack(">H", FORMAT_VERSION))


def test_write_creates_directory_and_leaves_no_temp_files(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "data" / "dump.mkv"
    write_snapshot(path, ITEMS)
    write_snapshot(path, ITEMS[:1])
    assert [p.name for p in path.parent.iterdir()] == ["dump.mkv"]
    assert list(read_snapshot(path)) == ITEMS[:1]


def test_failed_write_keeps_previous_file(tmp_path: Path) -> None:
    path = tmp_path / "dump.mkv"
    write_snapshot(path, ITEMS[:2])

    def exploding() -> object:
        yield ("ok", "1", None)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        write_snapshot(path, exploding())  # type: ignore[arg-type]
    assert list(read_snapshot(path)) == ITEMS[:2]
    assert [p.name for p in tmp_path.iterdir()] == ["dump.mkv"]


def test_atomic_write_success(tmp_path: Path) -> None:
    path = tmp_path / "file.bin"
    with atomic_write(path) as handle:
        handle.write(b"hello")
    assert path.read_bytes() == b"hello"


def _corrupt(path: Path, offset: int) -> None:
    data = bytearray(path.read_bytes())
    data[offset] ^= 0xFF
    path.write_bytes(bytes(data))


def test_checksum_detects_corruption(tmp_path: Path) -> None:
    path = tmp_path / "dump.mkv"
    write_snapshot(path, ITEMS)
    _corrupt(path, 20)
    with pytest.raises(PersistenceError, match="checksum mismatch"):
        list(read_snapshot(path))


def test_truncated_file(tmp_path: Path) -> None:
    path = tmp_path / "dump.mkv"
    write_snapshot(path, ITEMS)
    path.write_bytes(path.read_bytes()[:-10])
    with pytest.raises(PersistenceError):
        list(read_snapshot(path))
    path.write_bytes(b"MINI")
    with pytest.raises(PersistenceError, match="truncated"):
        list(read_snapshot(path))


def test_wrong_magic(tmp_path: Path) -> None:
    path = tmp_path / "dump.mkv"
    path.write_bytes(b"REDIS0011" + b"\x00" * 20)
    with pytest.raises(PersistenceError, match="not a MiniKV snapshot"):
        list(read_snapshot(path))


def _with_valid_checksum(body: bytes) -> bytes:
    import zlib

    return body + struct.pack(">I", zlib.crc32(body))


def test_unsupported_version(tmp_path: Path) -> None:
    path = tmp_path / "dump.mkv"
    body = MAGIC + struct.pack(">H", 99) + b"\xff" + struct.pack(">Q", 0)
    path.write_bytes(_with_valid_checksum(body))
    with pytest.raises(PersistenceError, match="unsupported format version 99"):
        list(read_snapshot(path))


def test_unknown_opcode_and_count_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "dump.mkv"
    header = MAGIC + struct.pack(">H", FORMAT_VERSION)
    path.write_bytes(_with_valid_checksum(header + b"\x07" + b"\x00" * 12))
    with pytest.raises(PersistenceError, match="unknown opcode 0x07"):
        list(read_snapshot(path))
    path.write_bytes(_with_valid_checksum(header + b"\xff" + struct.pack(">Q", 3)))
    with pytest.raises(PersistenceError, match="record count mismatch"):
        list(read_snapshot(path))
