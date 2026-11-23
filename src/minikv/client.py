"""A small, blocking RESP2 client for MiniKV (and Redis) servers.

Example:
    >>> from minikv.client import MiniKVClient
    >>> with MiniKVClient(port=6380) as client:          # doctest: +SKIP
    ...     client.execute("SET", "greeting", "hello")
    ...     client.execute("GET", "greeting")
    SimpleString(value='OK')
    'hello'
"""

from __future__ import annotations

import socket
from types import TracebackType

from minikv.constants import DEFAULT_HOST, DEFAULT_PORT
from minikv.errors import ReplyError
from minikv.protocol import ReplyParser, encode_command
from minikv.responses import Response


class MiniKVClient:
    """Send commands to a server over TCP and decode replies.

    The connection is opened lazily on the first command. If it is lost,
    the failing call raises :class:`ConnectionError` and the next call opens
    a fresh connection. Commands are never retried automatically, because
    resending a write that may already have been applied is unsafe.

    Args:
        host: Server host.
        port: Server port.
        timeout: Socket timeout in seconds, or ``None`` to block forever.
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        *,
        timeout: float | None = 5.0,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._parser = ReplyParser()

    @property
    def connected(self) -> bool:
        """Whether a socket is currently open."""
        return self._sock is not None

    def connect(self) -> None:
        """Open the connection if it is not open yet.

        Raises:
            ConnectionError: If the server cannot be reached.
        """
        if self._sock is not None:
            return
        try:
            self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as exc:
            reason = exc.strerror or str(exc)
            raise ConnectionError(
                f"Could not connect to MiniKV at {self.host}:{self.port}: {reason}"
            ) from exc
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._parser = ReplyParser()

    def close(self) -> None:
        """Close the connection (idempotent)."""
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def execute(self, *command: str) -> Response:
        """Send one command and return its decoded reply.

        Args:
            *command: Command name and arguments.

        Returns:
            The reply value (see :mod:`minikv.responses`).

        Raises:
            ReplyError: If the server replied with an error.
            ConnectionError: If the connection failed or was closed.
            ValueError: If no command is given.
        """
        if not command:
            raise ValueError("a command is required")
        self.connect()
        assert self._sock is not None
        try:
            self._sock.sendall(encode_command(command))
            reply = self._read_reply()
        except OSError as exc:
            self.close()
            if isinstance(exc, ConnectionError):
                raise
            raise ConnectionError(f"connection to {self.host}:{self.port} failed: {exc}") from exc
        if command[0].upper() == "QUIT":
            self.close()  # the server closes its side; reconnect on next command
        if isinstance(reply, ReplyError):
            raise reply
        return reply

    def _read_reply(self) -> Response | ReplyError:
        assert self._sock is not None
        while True:
            complete, reply = self._parser.next_reply()
            if complete:
                return reply
            chunk = self._sock.recv(64 * 1024)
            if not chunk:
                raise ConnectionError("connection closed by server")
            self._parser.feed(chunk)

    def __enter__(self) -> MiniKVClient:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
