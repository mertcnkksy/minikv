"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest

from minikv import Database
from minikv.clock import ManualClock
from minikv.storage import InMemoryStorage

from tests.helpers import ServerThread


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


@pytest.fixture
def server_factory() -> Iterator[Callable[..., ServerThread]]:
    """Start servers on ephemeral ports; all are stopped after the test."""
    started: list[ServerThread] = []

    def start(
        database: Database | None = None, *, persistence: Any = None, **config: Any
    ) -> ServerThread:
        from minikv.server import ServerConfig

        config.setdefault("port", 0)
        running = ServerThread(database, ServerConfig(**config), persistence).start()
        started.append(running)
        return running

    yield start
    for running in started:
        running.stop()


@pytest.fixture
def server(server_factory: Callable[..., ServerThread]) -> ServerThread:
    """A running server with a fresh database on an ephemeral port."""
    return server_factory()
