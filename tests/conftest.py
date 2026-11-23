"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from minikv import Database
from minikv.storage import InMemoryStorage


@pytest.fixture
def storage() -> InMemoryStorage:
    """A fresh, empty in-memory storage backend."""
    return InMemoryStorage()


@pytest.fixture
def db() -> Database:
    """A fresh database with the built-in commands."""
    return Database()
