"""The MiniKV TCP server.

Built on :mod:`asyncio`: one event loop serves every client, and commands
run one at a time on that loop, exactly like Redis' single-threaded model.
That makes each command atomic without locks in the command layer, while
I/O for thousands of idle connections costs almost nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import signal
from dataclasses import dataclass

from minikv import __version__
from minikv.constants import DEFAULT_HOST, DEFAULT_PORT
from minikv.engine import Database
from minikv.server.connection import ClientConnection

logger = logging.getLogger("minikv.server")

_MAX_CLIENTS_REPLY = b"-ERR max number of clients reached\r\n"


@dataclass(frozen=True, slots=True)
class ServerConfig:
    """Server settings.

    Attributes:
        host: Interface to bind to. Use ``"0.0.0.0"`` to accept remote clients.
        port: TCP port; ``0`` picks a free ephemeral port (useful in tests).
        max_clients: Maximum simultaneous connections.
        expiry_interval: Seconds between background expiry cycles; ``0``
            disables the cycle (keys still expire lazily).
        read_size: Maximum bytes read from a socket at once.
    """

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    max_clients: int = 10_000
    expiry_interval: float = 0.1
    read_size: int = 64 * 1024

    def __post_init__(self) -> None:
        if not 0 <= self.port <= 65_535:
            raise ValueError("port must be between 0 and 65535")
        if self.max_clients < 1:
            raise ValueError("max_clients must be at least 1")
        if self.expiry_interval < 0:
            raise ValueError("expiry_interval must not be negative")
        if self.read_size < 1:
            raise ValueError("read_size must be positive")


class MiniKVServer:
    """An asyncio server exposing a :class:`~minikv.Database` over RESP2.

    Example:
        >>> async def main() -> None:
        ...     async with MiniKVServer(config=ServerConfig(port=0)) as server:
        ...         print(server.address)
        ...         await server.serve_forever()

    Args:
        database: Database to serve. Defaults to a new, empty one.
        config: Server settings. Defaults to :class:`ServerConfig`.
    """

    def __init__(self, database: Database | None = None, config: ServerConfig | None = None) -> None:
        self.database = database if database is not None else Database()
        self.config = config if config is not None else ServerConfig()
        self._server: asyncio.Server | None = None
        self._expiry_task: asyncio.Task[None] | None = None
        self._connections: dict[ClientConnection, asyncio.Task[None]] = {}
        self._ids = itertools.count(1)

    # -- lifecycle ----------------------------------------------------------------

    async def start(self) -> None:
        """Bind the listening socket and start background tasks.

        Raises:
            RuntimeError: If the server is already running.
            OSError: If the address cannot be bound (e.g. port in use).
        """
        if self._server is not None:
            raise RuntimeError("server is already running")
        self._server = await asyncio.start_server(
            self._handle_client, self.config.host, self.config.port
        )
        if self.config.expiry_interval > 0:
            self._expiry_task = asyncio.create_task(self._expiry_cycle())
        host, port = self.address
        logger.info("MiniKV %s ready to accept connections on %s:%d", __version__, host, port)

    async def serve_forever(self) -> None:
        """Block until the server is stopped. Starts it first if necessary."""
        if self._server is None:
            await self.start()
        assert self._server is not None
        with contextlib.suppress(asyncio.CancelledError):
            await self._server.serve_forever()

    async def stop(self) -> None:
        """Stop accepting clients, close every connection and stop background tasks."""
        if self._server is None:
            return
        server, self._server = self._server, None
        server.close()
        if self._expiry_task is not None:
            self._expiry_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._expiry_task
            self._expiry_task = None
        connections = list(self._connections.items())
        for connection, _ in connections:
            await connection.close()
        if connections:
            await asyncio.wait([task for _, task in connections], timeout=5)
        await server.wait_closed()
        logger.info("MiniKV server stopped")

    async def __aenter__(self) -> MiniKVServer:
        await self.start()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.stop()

    # -- introspection ------------------------------------------------------------

    @property
    def is_serving(self) -> bool:
        """Whether the server is accepting connections."""
        return self._server is not None and self._server.is_serving()

    @property
    def address(self) -> tuple[str, int]:
        """The ``(host, port)`` actually bound, including an ephemeral port.

        Raises:
            RuntimeError: If the server is not running.
        """
        if self._server is None or not self._server.sockets:
            raise RuntimeError("server is not running")
        sockname = self._server.sockets[0].getsockname()
        return sockname[0], sockname[1]

    @property
    def client_count(self) -> int:
        """Number of currently connected clients."""
        return len(self._connections)

    # -- internals ----------------------------------------------------------------

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if len(self._connections) >= self.config.max_clients:
            logger.warning("rejecting client: max number of clients reached")
            writer.write(_MAX_CLIENTS_REPLY)
            with contextlib.suppress(ConnectionError, OSError):
                await writer.drain()
            writer.close()
            return

        connection = ClientConnection(
            self.database, reader, writer, next(self._ids), read_size=self.config.read_size
        )
        task = asyncio.current_task()
        assert task is not None
        self._connections[connection] = task
        try:
            await connection.run()
        finally:
            self._connections.pop(connection, None)

    async def _expiry_cycle(self) -> None:
        """Periodically purge expired keys, like Redis' ``serverCron``."""
        while True:
            await asyncio.sleep(self.config.expiry_interval)
            try:
                removed = self.database.storage.remove_expired()
            except Exception:  # pragma: no cover - defensive; keep the cycle alive
                logger.exception("active expiry cycle failed")
            else:
                if removed:
                    logger.debug("active expiry removed %d key(s)", removed)


def run_server(config: ServerConfig | None = None, database: Database | None = None) -> int:
    """Run a server in the foreground until SIGINT or SIGTERM.

    Args:
        config: Server settings.
        database: Database to serve.

    Returns:
        A process exit code: ``0`` after a clean shutdown, ``1`` if the
        server could not start.
    """

    async def _main() -> int:
        server = MiniKVServer(database, config)
        try:
            await server.start()
        except OSError as exc:
            logger.error("could not start server: %s", exc)
            return 1

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
                loop.add_signal_handler(sig, stop.set)
        try:
            await stop.wait()
            logger.info("shutdown signal received")
        finally:
            await server.stop()
        return 0

    try:
        return asyncio.run(_main())
    except KeyboardInterrupt:  # platforms without loop signal handlers (Windows)
        return 0
