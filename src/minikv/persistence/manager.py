"""Coordinate snapshots and the append-only file for one database.

Lifecycle::

    manager = PersistenceManager(db, PersistenceConfig(directory=Path("data")))
    manager.load()      # restore data from disk
    manager.start()     # begin logging writes and accept SAVE/BGSAVE/...
    ...                 # call manager.cron() periodically (the server does)
    manager.close()     # final fsync and, if save rules exist, a last snapshot

Background work runs in threads. A background job first takes a consistent
copy of the dataset while holding the database lock (cheap: references to
immutable strings), then writes it without blocking command execution.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from minikv.engine import Database
from minikv.errors import CommandError, PersistenceError
from minikv.persistence.aof import AppendOnlyFile, FsyncPolicy, load_aof, write_commands
from minikv.persistence.fileutil import temp_path_for
from minikv.persistence.snapshot import SnapshotItem, read_snapshot, write_snapshot

logger = logging.getLogger("minikv.persistence")

DEFAULT_SAVE_RULES: tuple[tuple[int, int], ...] = ((3600, 1), (300, 100), (60, 10_000))
"""Redis' default ``save`` points: (seconds, minimum changes)."""

_SAVE_RETRY_DELAY_MS = 5_000
"""Pause before an automatic save retries after a failure."""


@dataclass(frozen=True, slots=True)
class PersistenceConfig:
    """Persistence settings.

    Attributes:
        directory: Folder holding the snapshot and the append-only file.
        snapshot_filename: Snapshot file name.
        save_rules: Automatic snapshot rules as ``(seconds, changes)`` pairs:
            save when at least ``changes`` writes happened and ``seconds``
            passed since the last save. Empty disables automatic and shutdown
            snapshots (``SAVE``/``BGSAVE`` still work).
        aof_enabled: Whether to log every write to the append-only file.
        aof_filename: Append-only file name.
        aof_fsync: Durability policy for the append-only file.
        aof_load_truncated: Accept (and repair) a log whose last command was
            cut off by a crash.
        aof_rewrite_percentage: Automatically rewrite the log when it grew by
            this percentage since the last rewrite; ``0`` disables.
        aof_rewrite_min_size: Never auto-rewrite logs smaller than this (bytes).
    """

    directory: Path = field(default_factory=lambda: Path("."))
    snapshot_filename: str = "dump.mkv"
    save_rules: tuple[tuple[int, int], ...] = DEFAULT_SAVE_RULES
    aof_enabled: bool = False
    aof_filename: str = "appendonly.aof"
    aof_fsync: FsyncPolicy = FsyncPolicy.EVERYSEC
    aof_load_truncated: bool = True
    aof_rewrite_percentage: int = 100
    aof_rewrite_min_size: int = 64 * 1024 * 1024

    def __post_init__(self) -> None:
        object.__setattr__(self, "directory", Path(self.directory))
        object.__setattr__(self, "aof_fsync", FsyncPolicy(self.aof_fsync))
        for seconds, changes in self.save_rules:
            if seconds < 1 or changes < 1:
                raise ValueError("save rules need positive seconds and changes")
        for name in (self.snapshot_filename, self.aof_filename):
            if not name or Path(name).name != name:
                raise ValueError(f"invalid file name: {name!r}")
        if self.aof_rewrite_percentage < 0 or self.aof_rewrite_min_size < 0:
            raise ValueError("AOF rewrite thresholds must not be negative")

    @property
    def snapshot_path(self) -> Path:
        """Full path of the snapshot file."""
        return self.directory / self.snapshot_filename

    @property
    def aof_path(self) -> Path:
        """Full path of the append-only file."""
        return self.directory / self.aof_filename


class PersistenceManager:
    """Persist a :class:`~minikv.Database` with snapshots and/or an append-only file.

    Args:
        database: The database to persist.
        config: Persistence settings.
    """

    def __init__(self, database: Database, config: PersistenceConfig | None = None) -> None:
        self.database = database
        self.config = config if config is not None else PersistenceConfig()
        self._aof: AppendOnlyFile | None = None
        self._state_lock = threading.Lock()
        self._save_thread: threading.Thread | None = None
        self._rewrite_thread: threading.Thread | None = None
        now_ms = self._now_ms()
        self._last_save_ms = now_ms
        self._last_save_attempt_ms = now_ms
        self._last_save_ok = True
        self._dirty_at_last_save = database.storage.dirty
        self._aof_base_size = 0
        self._started = False

    # -- lifecycle --------------------------------------------------------------

    def load(self) -> str:
        """Restore data from disk into the (empty) database.

        The append-only file wins when it is enabled and exists, because it
        is the more complete record. Otherwise the snapshot is loaded.

        Returns:
            ``"aof"``, ``"snapshot"`` or ``"none"``, describing the source used.

        Raises:
            PersistenceError: If a file is corrupted.
        """
        source = "none"
        if self.config.aof_enabled and self.config.aof_path.exists():
            count = load_aof(
                self.config.aof_path,
                self.database.execute,
                allow_truncated=self.config.aof_load_truncated,
            )
            logger.info("loaded %d command(s) from %s", count, self.config.aof_path)
            source = "aof"
        elif self.config.snapshot_path.exists():
            loaded, skipped = self._load_snapshot()
            logger.info(
                "loaded %d key(s) from %s (%d already expired)",
                loaded,
                self.config.snapshot_path,
                skipped,
            )
            source = "snapshot"

        with self._state_lock:
            self._dirty_at_last_save = self.database.storage.dirty
        return source

    def start(self, *, group_commit: bool = False) -> None:
        """Begin logging writes and enable the persistence commands.

        When the append-only file is enabled but missing, it is first
        created from the current dataset, so data loaded from a snapshot is
        not lost on the next restart.

        Args:
            group_commit: Defer AOF flushing and the ``always`` fsync until
                :meth:`commit`. Only for callers that commit before
                acknowledging writes, such as the server.
        """
        if self._started:
            raise RuntimeError("persistence is already started")
        self.config.directory.mkdir(parents=True, exist_ok=True)
        if self.config.aof_enabled:
            self._aof = AppendOnlyFile(
                self.config.aof_path, self.config.aof_fsync, deferred=group_commit
            )
            if not self.config.aof_path.exists():
                self._write_base_aof()
            self._aof.open()
            self._aof_base_size = self._aof.size
            self.database.add_write_listener(self._aof.append)
        self.database.persistence = self
        self._started = True

    def close(self, *, final_save: bool = True) -> None:
        """Wait for background jobs, take a final snapshot if configured, close the log.

        Args:
            final_save: Take a final snapshot when save rules are configured.
                Pass ``False`` when shutting down a server that never started.
        """
        if not self._started:
            return
        self.wait()
        self.database.persistence = None
        if self._aof is not None:
            self.database.remove_write_listener(self._aof.append)
        if final_save and self.config.save_rules:
            try:
                self.save()
            except PersistenceError as exc:
                logger.error("final snapshot failed: %s", exc)
        if self._aof is not None:
            self._aof.close()
        self._started = False

    def wait(self, timeout: float | None = None) -> None:
        """Block until running background jobs have finished."""
        for thread in (self._save_thread, self._rewrite_thread):
            if thread is not None:
                thread.join(timeout)

    # -- status -----------------------------------------------------------------

    @property
    def aof(self) -> AppendOnlyFile | None:
        """The append-only file, when enabled and started."""
        return self._aof

    @property
    def needs_commit(self) -> bool:
        """Whether group-committed writes are waiting for :meth:`commit`."""
        return self._aof is not None and self._aof.needs_commit

    def commit(self) -> None:
        """Make group-committed writes durable (see :meth:`AppendOnlyFile.commit`).

        Raises:
            PersistenceError: If the append-only file cannot be synced.
        """
        if self._aof is not None:
            self._aof.commit()

    @property
    def save_in_progress(self) -> bool:
        """Whether a background snapshot is running."""
        return self._save_thread is not None and self._save_thread.is_alive()

    @property
    def rewrite_in_progress(self) -> bool:
        """Whether a background AOF rewrite is running."""
        return self._rewrite_thread is not None and self._rewrite_thread.is_alive()

    @property
    def changes_since_save(self) -> int:
        """Number of modifications since the last successful snapshot."""
        with self._state_lock:
            return self.database.storage.dirty - self._dirty_at_last_save

    def last_save_time(self) -> int:
        """Unix time (seconds) of the last successful snapshot, or of startup."""
        with self._state_lock:
            return self._last_save_ms // 1000

    # -- snapshots --------------------------------------------------------------

    def save(self) -> None:
        """Write a snapshot synchronously (``SAVE``).

        Raises:
            CommandError: If a background snapshot is running.
            PersistenceError: If writing fails.
        """
        if self.save_in_progress:
            raise CommandError("Background save already in progress")
        items, dirty, started_ms = self._capture()
        self._write_snapshot(items, dirty, started_ms)

    def background_save(self) -> None:
        """Start writing a snapshot in a background thread (``BGSAVE``).

        Raises:
            CommandError: If a background snapshot is already running.
        """
        if self.save_in_progress:
            raise CommandError("Background save already in progress")
        items, dirty, started_ms = self._capture()
        self._save_thread = threading.Thread(
            target=self._background_save_worker,
            args=(items, dirty, started_ms),
            name="minikv-bgsave",
            daemon=True,
        )
        self._save_thread.start()

    def _capture(self) -> tuple[list[SnapshotItem], int, int]:
        """Copy the dataset and the dirty counter atomically."""
        with self.database.lock:
            return self.database.storage.items(), self.database.storage.dirty, self._now_ms()

    def _write_snapshot(self, items: list[SnapshotItem], dirty: int, started_ms: int) -> None:
        with self._state_lock:
            self._last_save_attempt_ms = started_ms
        try:
            count = write_snapshot(self.config.snapshot_path, items)
        except OSError as exc:
            with self._state_lock:
                self._last_save_ok = False
            raise PersistenceError(f"cannot write snapshot: {exc}") from exc
        with self._state_lock:
            self._last_save_ms = started_ms
            self._dirty_at_last_save = dirty
            self._last_save_ok = True
        logger.info("snapshot saved: %d key(s) to %s", count, self.config.snapshot_path)

    def _background_save_worker(
        self, items: list[SnapshotItem], dirty: int, started_ms: int
    ) -> None:
        try:
            self._write_snapshot(items, dirty, started_ms)
        except PersistenceError as exc:
            logger.error("background save failed: %s", exc)

    def _load_snapshot(self) -> tuple[int, int]:
        storage = self.database.storage
        now = self._now_ms()
        loaded = skipped = 0
        for key, value, expire_at in read_snapshot(self.config.snapshot_path):
            if expire_at is not None and expire_at <= now:
                skipped += 1
                continue
            storage.set(key, value, expire_at)
            loaded += 1
        return loaded, skipped

    # -- append-only file rewrite -----------------------------------------------

    def background_rewrite_aof(self) -> None:
        """Compact the append-only file in a background thread (``BGREWRITEAOF``).

        The current dataset is written as a minimal set of commands to a
        temporary file. Writes arriving meanwhile are buffered and appended
        to the new file before it atomically replaces the old one.

        Raises:
            CommandError: If the AOF is disabled or a rewrite is already running.
        """
        if self._aof is None:
            raise CommandError("append only file is not enabled")
        if self.rewrite_in_progress:
            raise CommandError("Background append only file rewriting already in progress")
        with self.database.lock:
            items = self.database.storage.items()
            self._aof.begin_rewrite()
        self._rewrite_thread = threading.Thread(
            target=self._rewrite_worker, args=(items,), name="minikv-aofrw", daemon=True
        )
        self._rewrite_thread.start()

    def _rewrite_worker(self, items: list[SnapshotItem]) -> None:
        assert self._aof is not None
        temp = temp_path_for(self.config.aof_path)
        try:
            self._write_rewrite_file(temp, items)
            self._aof.complete_rewrite(temp)
        except Exception as exc:
            self._aof.abort_rewrite()
            temp.unlink(missing_ok=True)
            logger.error("append only file rewrite failed: %s", exc)
            return
        self._aof_base_size = self._aof.size
        logger.info("append only file rewritten: %d key(s), %d bytes", len(items), self._aof_base_size)

    def _write_rewrite_file(self, path: Path, items: Sequence[SnapshotItem]) -> None:
        """Write the compacted log (a separate method so tests can slow it down)."""
        import os

        with open(path, "wb") as handle:
            write_commands(handle, _items_to_commands(items))
            handle.flush()
            os.fsync(handle.fileno())

    def _write_base_aof(self) -> None:
        """Create the append-only file from the current dataset."""
        temp = temp_path_for(self.config.aof_path)
        try:
            self._write_rewrite_file(temp, self.database.storage.items())
            temp.replace(self.config.aof_path)
        finally:
            temp.unlink(missing_ok=True)

    # -- periodic work ----------------------------------------------------------

    def cron(self) -> None:
        """Run periodic duties: AOF fsync, automatic snapshots and rewrites."""
        if not self._started:
            return
        if self._aof is not None:
            try:
                self._aof.sync_if_due()
            except OSError as exc:
                logger.error("append only file fsync failed: %s", exc)
        if self._should_auto_save():
            logger.info("automatic save triggered (%d changes)", self.changes_since_save)
            self.background_save()
        if self._should_auto_rewrite():
            logger.info("automatic append only file rewrite triggered")
            self.background_rewrite_aof()

    def _should_auto_save(self) -> bool:
        if not self.config.save_rules or self.save_in_progress:
            return False
        now = self._now_ms()
        with self._state_lock:
            if not self._last_save_ok and now - self._last_save_attempt_ms < _SAVE_RETRY_DELAY_MS:
                return False
            changes = self.database.storage.dirty - self._dirty_at_last_save
            elapsed_ms = now - self._last_save_ms
        return any(
            changes >= min_changes and elapsed_ms >= seconds * 1000
            for seconds, min_changes in self.config.save_rules
        )

    def _should_auto_rewrite(self) -> bool:
        if self._aof is None or self.rewrite_in_progress or not self.config.aof_rewrite_percentage:
            return False
        size = self._aof.size
        if size < self.config.aof_rewrite_min_size:
            return False
        base = max(self._aof_base_size, 1)
        return (size - base) * 100 // base >= self.config.aof_rewrite_percentage

    def _now_ms(self) -> int:
        return self.database.clock.time_ms()


def _items_to_commands(items: Sequence[SnapshotItem]) -> list[list[str]]:
    """Turn dataset entries into the minimal commands that recreate them."""
    commands: list[list[str]] = []
    for key, value, expire_at in items:
        command = ["SET", key, value]
        if expire_at is not None:
            command += ["PXAT", str(expire_at)]
        commands.append(command)
    return commands
