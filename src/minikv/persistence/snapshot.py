"""Point-in-time snapshots in a compact, checksummed binary format.

File layout (all integers big-endian)::

    magic        6 bytes   b"MINIKV"
    version      u16       FORMAT_VERSION
    records      repeated:
        opcode   u8        0x01 = key without expiry, 0x02 = key with expiry
        expiry   i64       absolute ms since the epoch (opcode 0x02 only)
        key      u32 length + bytes
        value    u32 length + bytes
    end          u8        0xFF
    count        u64       number of records
    checksum     u32       CRC-32 of every preceding byte

Keys and values are stored as UTF-8 with ``surrogateescape``, so binary
values written over the network survive a save/load cycle unchanged.
The checksum detects truncated or corrupted files before any data is used.
"""

from __future__ import annotations

import struct
import zlib
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import BinaryIO

from minikv.errors import PersistenceError
from minikv.persistence.fileutil import atomic_write

MAGIC = b"MINIKV"
FORMAT_VERSION = 1

OP_KEY = 0x01
OP_KEY_WITH_EXPIRY = 0x02
OP_EOF = 0xFF

_ENCODING = "utf-8"
_ERRORS = "surrogateescape"

type SnapshotItem = tuple[str, str, int | None]
"""``(key, value, expire_at_ms)`` as stored in a snapshot."""


class _ChecksumWriter:
    """Write to a file while maintaining a running CRC-32."""

    def __init__(self, handle: BinaryIO) -> None:
        self._handle = handle
        self.crc = 0

    def write(self, data: bytes) -> None:
        self.crc = zlib.crc32(data, self.crc)
        self._handle.write(data)


def _encode_string(text: str) -> bytes:
    data = text.encode(_ENCODING, _ERRORS)
    return struct.pack(">I", len(data)) + data


def write_snapshot(path: Path, items: Iterable[SnapshotItem]) -> int:
    """Atomically write ``items`` to a snapshot file.

    Args:
        path: Destination file; replaced only once the new file is complete.
        items: Entries to store.

    Returns:
        The number of entries written.
    """
    count = 0
    with atomic_write(path) as handle:
        out = _ChecksumWriter(handle)
        out.write(MAGIC + struct.pack(">H", FORMAT_VERSION))
        for key, value, expire_at in items:
            if expire_at is None:
                out.write(bytes([OP_KEY]))
            else:
                out.write(bytes([OP_KEY_WITH_EXPIRY]) + struct.pack(">q", expire_at))
            out.write(_encode_string(key))
            out.write(_encode_string(value))
            count += 1
        out.write(bytes([OP_EOF]) + struct.pack(">Q", count))
        handle.write(struct.pack(">I", out.crc))
    return count


class _Reader:
    """Sequential reader over an in-memory snapshot with bounds checking."""

    def __init__(self, data: bytes, path: Path) -> None:
        self._data = data
        self._pos = 0
        self._path = path

    def take(self, size: int) -> bytes:
        end = self._pos + size
        if end > len(self._data):
            raise PersistenceError(f"snapshot {self._path} is truncated")
        chunk = self._data[self._pos : end]
        self._pos = end
        return chunk

    def unpack(self, fmt: str) -> int:
        (value,) = struct.unpack(fmt, self.take(struct.calcsize(fmt)))
        return int(value)

    def string(self) -> str:
        return self.take(self.unpack(">I")).decode(_ENCODING, _ERRORS)

    @property
    def position(self) -> int:
        return self._pos


def read_snapshot(path: Path) -> Iterator[SnapshotItem]:
    """Read and validate a snapshot file.

    The whole file is verified (magic, version, checksum, record count)
    before any entry is yielded, so a corrupted file never yields partial data.

    Args:
        path: The snapshot to read.

    Yields:
        ``(key, value, expire_at_ms)`` tuples, including keys that may have
        expired since the snapshot was taken.

    Raises:
        PersistenceError: If the file is not a valid snapshot.
    """
    data = path.read_bytes()
    if len(data) < len(MAGIC) + 2 + 1 + 8 + 4:
        raise PersistenceError(f"snapshot {path} is truncated")
    if data[: len(MAGIC)] != MAGIC:
        raise PersistenceError(f"{path} is not a MiniKV snapshot")
    body, (stored_crc,) = data[:-4], struct.unpack(">I", data[-4:])
    if zlib.crc32(body) != stored_crc:
        raise PersistenceError(f"snapshot {path} is corrupted (checksum mismatch)")

    reader = _Reader(body, path)
    reader.take(len(MAGIC))
    version = reader.unpack(">H")
    if version != FORMAT_VERSION:
        raise PersistenceError(f"snapshot {path} uses unsupported format version {version}")

    items: list[SnapshotItem] = []
    while True:
        opcode = reader.unpack(">B")
        if opcode == OP_EOF:
            break
        if opcode == OP_KEY:
            expire_at = None
        elif opcode == OP_KEY_WITH_EXPIRY:
            expire_at = reader.unpack(">q")
        else:
            raise PersistenceError(f"snapshot {path} has unknown opcode 0x{opcode:02x}")
        key = reader.string()
        items.append((key, reader.string(), expire_at))

    count = reader.unpack(">Q")
    if count != len(items) or reader.position != len(body):
        raise PersistenceError(f"snapshot {path} is corrupted (record count mismatch)")
    yield from items
