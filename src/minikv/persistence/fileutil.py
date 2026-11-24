"""Crash-safe file helpers."""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO


def fsync_directory(directory: Path) -> None:
    """Flush a directory entry to disk so a rename survives a power loss.

    This is a no-op on platforms that cannot open directories (Windows).
    """
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:  # pragma: no cover - e.g. Windows
        return
    try:
        os.fsync(fd)
    except OSError:  # pragma: no cover - some filesystems refuse
        pass
    finally:
        os.close(fd)


def temp_path_for(path: Path) -> Path:
    """Return a unique temporary path in the same directory as ``path``.

    The directory is created if it does not exist yet.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(fd)
    return Path(name)


@contextlib.contextmanager
def atomic_write(path: Path) -> Iterator[BinaryIO]:
    """Write a file atomically: readers see either the old or the new file.

    Data is written to a temporary file in the same directory, flushed and
    ``fsync``-ed, then moved over ``path`` with :func:`os.replace`. If the
    block raises, the temporary file is removed and ``path`` is untouched.

    Example:
        >>> with atomic_write(Path("dump.mkv")) as f:      # doctest: +SKIP
        ...     f.write(b"data")
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = temp_path_for(path)
    try:
        with open(temp, "wb") as handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            temp.unlink()
        raise
    fsync_directory(path.parent)
