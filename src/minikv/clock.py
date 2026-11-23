"""Time sources for MiniKV.

All expiry logic reads time through a :class:`Clock` instead of calling
:mod:`time` directly. Production code uses :class:`SystemClock`; tests use
:class:`ManualClock` to move time forward instantly and deterministically.

Times are integer milliseconds since the Unix epoch, matching how Redis
stores expiry timestamps and avoiding floating-point rounding issues.
"""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """A source of the current wall-clock time."""

    def time_ms(self) -> int:
        """Return the current time in milliseconds since the Unix epoch."""
        ...


class SystemClock:
    """A :class:`Clock` backed by the operating system's wall clock."""

    def time_ms(self) -> int:
        """Return the current system time in milliseconds since the epoch."""
        return time.time_ns() // 1_000_000


class ManualClock:
    """A :class:`Clock` whose time only changes when told to.

    Args:
        start_ms: Initial time in milliseconds since the epoch.

    Example:
        >>> clock = ManualClock(start_ms=1_000)
        >>> clock.advance(seconds=2)
        >>> clock.time_ms()
        3000
    """

    DEFAULT_START_MS = 1_700_000_000_000

    def __init__(self, start_ms: int = DEFAULT_START_MS) -> None:
        self._now_ms = start_ms

    def time_ms(self) -> int:
        """Return the current manual time in milliseconds."""
        return self._now_ms

    def advance(self, *, seconds: float = 0, milliseconds: int = 0) -> None:
        """Move time forward.

        Args:
            seconds: Seconds to advance (may be fractional).
            milliseconds: Additional milliseconds to advance.

        Raises:
            ValueError: If the total step is negative.
        """
        step = round(seconds * 1000) + milliseconds
        if step < 0:
            raise ValueError("a clock cannot move backwards")
        self._now_ms += step

    def set(self, time_ms: int) -> None:
        """Jump to an absolute time in milliseconds (may move backwards)."""
        self._now_ms = time_ms
