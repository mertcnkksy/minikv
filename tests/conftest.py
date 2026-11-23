"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from minikv import Database
from minikv.clock import ManualClock
from minikv.storage import InMemoryStorage


@pytest.fixture
def clock() -> ManualClock:
    """A manually controlled clock starting at a fixed timestamp."""
    return ManualClock()


@pytest.fixture
def storage(clock: ManualClock) -> InMemoryStorage:
    """A fresh, empty in-memory storage backend driven by ``clock``."""
    return InMemoryStorage(clock=clock)


@pytest.fixture
def db(clock: ManualClock) -> Database:
    """A fresh database with the built-in commands, driven by ``clock``."""
    return Database(clock=clock)
