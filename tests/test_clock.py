"""Tests for clock implementations."""

from __future__ import annotations

import time

import pytest

from minikv.clock import Clock, ManualClock, SystemClock


def test_system_clock_tracks_wall_time() -> None:
    before = int(time.time() * 1000)
    now = SystemClock().time_ms()
    after = int(time.time() * 1000)
    assert before - 1 <= now <= after + 1


def test_clocks_satisfy_protocol() -> None:
    assert isinstance(SystemClock(), Clock)
    assert isinstance(ManualClock(), Clock)


def test_manual_clock_advance() -> None:
    clock = ManualClock(start_ms=1_000)
    clock.advance(seconds=1.5)
    clock.advance(milliseconds=250)
    assert clock.time_ms() == 2_750


def test_manual_clock_cannot_go_backwards_via_advance() -> None:
    with pytest.raises(ValueError):
        ManualClock().advance(milliseconds=-1)


def test_manual_clock_set_allows_jumps() -> None:
    clock = ManualClock(start_ms=5_000)
    clock.set(1_000)
    assert clock.time_ms() == 1_000
