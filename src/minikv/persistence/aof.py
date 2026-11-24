"""The append-only file (AOF): a log of every write, in RESP format.

Each command that modified the dataset is appended as a RESP array, exactly
as a client would send it, so the file is both human-inspectable and
replayable with the normal request parser. Commands are logged in a
time-independent form (absolute expiry times), so replaying the log at any
later moment reproduces the same dataset.

Durability is controlled by the fsync policy, mirroring Redis' ``appendfsync``:

* ``always`` - ``fsync`` after every write. Safest, slowest.
* ``everysec`` - ``fsync`` at most once per second. At most ~1 s of writes
  can be lost on power failure. The default.
* ``no`` - leave flushing to the operating system.

In every mode data reaches the OS before the client gets its reply, so a
crash of the MiniKV process alone never loses acknowledged writes.
"""

from __future__ import annotations

import contextlib
import enum
import logging
import os
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import BinaryIO

from minikv.errors import MiniKVError, PersistenceError, ProtocolError
from minikv.persistence.fileutil import fsync_directory
from minikv.protocol import RequestParser, encode_command

logger = logging.getLogger("minikv.persistence")

_READ_CHUNK = 1024 * 1024


class FsyncPolicy(enum.StrEnum):
    """When the append-only file is flushed to disk with ``fsync``."""

    ALWAYS = "always"
    EVERYSEC = "everysec"
    NO = "no"


class AppendOnlyFile:
    """Append commands to a log file, honouring an fsync policy.

    Supports the hand-over needed by a background rewrite: while a compacted
    copy is being written elsewhere, new writes are both appended here and
    buffered, and :meth:`complete_rewrite` adds the buffer to the new file
    before atomically swapping it in.

    Args:
        path: The log file.
        fsync_policy: Durability policy.
        monotonic: Time source for the ``everysec`` policy (injectable for tests).
    """

    def __init__(
        self,
        path: Path,
        fsync_policy: FsyncPolicy = FsyncPolicy.EVERYSEC,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.path = path
        self.fsync_policy = FsyncPolicy(fsync_policy)
        self._monotonic = monotonic
        self._file: BinaryIO | None = None
        self._lock = threading.Lock()
        self._rewrite_buffer: list[bytes] | None = None
        self._last_fsync = 0.0
        self._unsynced = False

    # -- lifecycle --------------------------------------------------------------

    def open(self) -> None:
        """Open the file for appending, creating it (and its directory) if needed."""
        with self._lock:
            if self._file is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._file = open(self.path, "ab")
                self._last_fsync = self._monotonic()

    def close(self) -> None:
        """Flush, fsync and close the file (idempotent)."""
        with self._lock:
            if self._file is not None:
                self._fsync_locked()
                self._file.close()
                self._file = None

    @property
    def is_open(self) -> bool:
        """Whether the file is open for appending."""
        return self._file is not None

    @property
    def size(self) -> int:
        """Current size of the log in bytes."""
        with self._lock:
            if self._file is not None:
                return self._file.tell()
        return self.path.stat().st_size if self.path.exists() else 0

    # -- writing ----------------------------------------------------------------

    def append(self, commands: Sequence[Sequence[str]]) -> None:
        """Append commands and apply the fsync policy.

        Raises:
            PersistenceError: If the file is closed or the write fails.
        """
        data = b"".join(encode_command(command) for command in commands)
        with self._lock:
            if self._file is None:
                raise PersistenceError("append-only file is not open")
            try:
                self._file.write(data)
                self._file.flush()
                self._unsynced = True
                if self._rewrite_buffer is not None:
                    self._rewrite_buffer.append(data)
                if self.fsync_policy is FsyncPolicy.ALWAYS:
                    self._fsync_locked()
                elif self.fsync_policy is FsyncPolicy.EVERYSEC:
                    self._fsync_if_due_locked()
            except OSError as exc:
                raise PersistenceError(f"cannot write append-only file: {exc}") from exc

    def sync_if_due(self) -> None:
        """Apply the ``everysec`` policy; called periodically by the server."""
        if self.fsync_policy is not FsyncPolicy.EVERYSEC:
            return
        with self._lock:
            if self._file is not None:
                self._fsync_if_due_locked()

    def _fsync_if_due_locked(self) -> None:
        if self._unsynced and self._monotonic() - self._last_fsync >= 1.0:
            self._fsync_locked()

    def _fsync_locked(self) -> None:
        if self._file is None or not self._unsynced:
            return
        self._file.flush()
        os.fsync(self._file.fileno())
        self._last_fsync = self._monotonic()
        self._unsynced = False

    # -- rewrite hand-over ------------------------------------------------------

    def begin_rewrite(self) -> None:
        """Start buffering new writes for an in-progress rewrite."""
        with self._lock:
            self._rewrite_buffer = []

    def abort_rewrite(self) -> None:
        """Discard the rewrite buffer after a failed rewrite."""
        with self._lock:
            self._rewrite_buffer = None

    def complete_rewrite(self, new_file: Path) -> None:
        """Append buffered writes to ``new_file`` and atomically replace the log.

        Args:
            new_file: The compacted log, in the same directory as :attr:`path`.

        Raises:
            PersistenceError: If no rewrite is in progress.
            OSError: If the swap fails; the old log stays in place.
        """
        with self._lock:
            if self._rewrite_buffer is None:
                raise PersistenceError("no append-only file rewrite in progress")
            with open(new_file, "ab") as handle:
                handle.write(b"".join(self._rewrite_buffer))
                handle.flush()
                os.fsync(handle.fileno())
            was_open = self._file is not None
            if self._file is not None:
                self._file.close()
                self._file = None
            os.replace(new_file, self.path)
            fsync_directory(self.path.parent)
            self._rewrite_buffer = None
            if was_open:
                self._file = open(self.path, "ab")
                self._unsynced = False
                self._last_fsync = self._monotonic()


def write_commands(handle: BinaryIO, commands: Iterable[Sequence[str]]) -> None:
    """Write RESP-encoded commands to an open binary file."""
    for command in commands:
        handle.write(encode_command(command))


def load_aof(
    path: Path,
    execute: Callable[..., object],
    *,
    allow_truncated: bool = True,
) -> int:
    """Replay an append-only file.

    Args:
        path: The log to replay.
        execute: Callable that executes one command, e.g. ``Database.execute``.
        allow_truncated: If the last command is incomplete (for example after
            a crash mid-write), drop it and truncate the file so later appends
            start on a clean boundary. If false, raise instead.

    Returns:
        The number of commands replayed.

    Raises:
        PersistenceError: If the file is corrupted, truncated (when not
            allowed), or contains a command that fails.
    """
    parser = RequestParser()
    replayed = 0
    fed = 0
    valid_offset = 0
    with open(path, "rb") as handle:
        while chunk := handle.read(_READ_CHUNK):
            parser.feed(chunk)
            fed += len(chunk)
            try:
                while (command := parser.next_command()) is not None:
                    try:
                        execute(*command)
                    except MiniKVError as exc:
                        raise PersistenceError(
                            f"append-only file {path} has an invalid command at byte "
                            f"{valid_offset}: {exc}"
                        ) from exc
                    replayed += 1
                    valid_offset = fed - parser.buffered
            except ProtocolError as exc:
                raise PersistenceError(
                    f"append-only file {path} is corrupted at byte {valid_offset}: {exc.detail}"
                ) from exc

    if parser.has_partial:
        lost = fed - valid_offset
        if not allow_truncated:
            raise PersistenceError(
                f"append-only file {path} ends with an incomplete command ({lost} bytes)"
            )
        logger.warning(
            "append-only file %s ends with an incomplete command; truncating %d byte(s)",
            path,
            lost,
        )
        with open(path, "r+b") as handle:
            handle.truncate(valid_offset)
            handle.flush()
            with contextlib.suppress(OSError):
                os.fsync(handle.fileno())
    return replayed
