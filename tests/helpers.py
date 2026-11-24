"""Test helpers for running servers and talking raw RESP."""

from __future__ import annotations

import asyncio
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from minikv import Database
from minikv.persistence import PersistenceManager
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
    persistence: PersistenceManager | None = None
    server: MiniKVServer = field(init=False)
    _loop: asyncio.AbstractEventLoop = field(init=False)
    _thread: threading.Thread = field(init=False)

    def __post_init__(self) -> None:
        self.server = MiniKVServer(self.database, self.config, self.persistence)
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
        if self._loop.is_closed():
            return
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


# --- server processes ----------------------------------------------------------

WINDOWS = sys.platform == "win32"
SRC_DIR = Path(__file__).resolve().parents[1] / "src"


def spawn_minikv(*args: str, **kwargs: Any) -> subprocess.Popen[str]:
    """Start ``python -m minikv <args>`` so it can later be stopped gracefully."""
    return spawn_python("-m", "minikv", *args, **kwargs)


def spawn_python(*args: str, **kwargs: Any) -> subprocess.Popen[str]:
    """Start ``python <args>`` so it can later be stopped gracefully.

    On Windows the child gets its own process group
    (``CREATE_NEW_PROCESS_GROUP``), which is required to target it with
    ``CTRL_BREAK_EVENT`` without also interrupting the test runner.
    """
    env = {**os.environ, "PYTHONPATH": str(SRC_DIR), **kwargs.pop("env", {})}
    if WINDOWS:
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(
        [sys.executable, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        **kwargs,
    )


def request_graceful_shutdown(process: subprocess.Popen[str], signal_name: str = "SIGTERM") -> None:
    """Ask a MiniKV server process to shut down gracefully.

    * **Unix:** deliver ``signal_name`` (``"SIGINT"`` or ``"SIGTERM"``) as is.
    * **Windows:** neither can be delivered gracefully to another process:
      ``send_signal(SIGINT)`` raises ``ValueError`` and ``SIGTERM`` calls
      ``TerminateProcess``, which kills the process without running any
      handler. The Windows mechanism for a graceful stop is a
      ``CTRL_BREAK_EVENT`` sent to the child's process group (see
      :func:`spawn_minikv`); the server receives it as ``SIGBREAK``.
      ``CTRL_C_EVENT`` is not used because it cannot be limited to one
      process group and would also interrupt the test runner.
    """
    if signal_name not in ("SIGINT", "SIGTERM"):
        raise ValueError(f"unsupported shutdown signal: {signal_name}")
    if WINDOWS:
        process.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        process.send_signal(getattr(signal, signal_name))


def stop_process(process: subprocess.Popen[str], signal_name: str = "SIGTERM") -> str:
    """Request a graceful shutdown and return the process output."""
    request_graceful_shutdown(process, signal_name)
    output, _ = process.communicate(timeout=20)
    return output


def kill_if_running(process: subprocess.Popen[str]) -> None:
    """Forcefully clean up a process left running by a failed test."""
    if process.poll() is None:
        process.kill()
        process.communicate()
