"""Test helpers for running servers and talking raw RESP."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from minikv import Database
from minikv.server import MiniKVServer, ServerConfig


def free_port() -> int:
    """Return a TCP port that is currently free on localhost."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@dataclass
class ServerThread:
    """A :class:`MiniKVServer` running on its own event loop in a background thread."""

    database: Database | None = None
    config: ServerConfig = field(default_factory=lambda: ServerConfig(port=0))
    server: MiniKVServer = field(init=False)
    _loop: asyncio.AbstractEventLoop = field(init=False)
    _thread: threading.Thread = field(init=False)

    def __post_init__(self) -> None:
        self.server = MiniKVServer(self.database, self.config)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)

    def start(self) -> ServerThread:
        self._thread.start()
        self.call(self.server.start())
        return self

    def call(self, coroutine: Any, timeout: float = 5) -> Any:
        """Run a coroutine on the server's loop and wait for its result."""
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop).result(timeout)

    def stop(self) -> None:
        self.call(self.server.stop())
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(5)
        self._loop.close()

    @property
    def port(self) -> int:
        return self.server.address[1]

    @property
    def db(self) -> Database:
        return self.server.database


def connect(port: int) -> socket.socket:
    """Open a raw TCP connection with a short timeout."""
    sock = socket.create_connection(("127.0.0.1", port), timeout=3)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return sock


def recv_exactly(sock: socket.socket, size: int) -> bytes:
    """Read until ``size`` bytes have arrived (or the peer closes)."""
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            break
        data += chunk
    return bytes(data)


def recv_until_closed(sock: socket.socket) -> bytes:
    """Read everything until the peer closes the connection."""
    data = bytearray()
    while chunk := sock.recv(65536):
        data += chunk
    return bytes(data)


def wait_for(predicate: Any, timeout: float = 3.0) -> bool:
    """Poll ``predicate`` until it is true or ``timeout`` elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())
