"""Per-client connection handling."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from minikv.engine import Database
from minikv.errors import MiniKVError, ProtocolError
from minikv.protocol import RequestParser, encode_error, encode_response
from minikv.responses import OK
from minikv.server.group_commit import GroupCommitter

logger = logging.getLogger("minikv.server")


class ClientConnection:
    """Serve one TCP client until it disconnects, sends ``QUIT`` or errs.

    Requests are read in chunks and every complete command in a chunk is
    executed before replies are written back in a single ``write`` call.
    This gives pipelined clients the throughput benefit they expect.

    With a :class:`~minikv.server.group_commit.GroupCommitter`, replies are
    held back until the writes they acknowledge are durable. If that fails,
    the connection is closed without replying, so the client never receives
    an acknowledgement for data that may not be on disk.

    The class is the natural home for per-client state in later phases
    (selected database, transactions, subscriptions).

    Args:
        database: The shared database.
        reader: The stream reader for the socket.
        writer: The stream writer for the socket.
        client_id: A unique, increasing identifier used in logs.
        read_size: Maximum bytes read from the socket at once.
        committer: Group committer to wait on before sending replies.
    """

    def __init__(
        self,
        database: Database,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        client_id: int,
        *,
        read_size: int = 64 * 1024,
        committer: GroupCommitter | None = None,
    ) -> None:
        self.client_id = client_id
        self.peer: str = _format_peer(writer.get_extra_info("peername"))
        self._db = database
        self._reader = reader
        self._writer = writer
        self._read_size = read_size
        self._parser = RequestParser()
        self._committer = committer

    async def run(self) -> None:
        """Process requests until the connection ends. Never raises I/O errors."""
        logger.debug("client #%d connected from %s", self.client_id, self.peer)
        try:
            while True:
                data = await self._reader.read(self._read_size)
                if not data:
                    break
                replies, close_after = self._process(data)
                if replies and not await self._wait_durable():
                    break
                if replies:
                    self._writer.write(replies)
                    await self._writer.drain()
                if close_after:
                    break
        except (ConnectionError, OSError) as exc:
            logger.debug("client #%d I/O error: %s", self.client_id, exc)
        finally:
            await self.close()
            logger.debug("client #%d disconnected", self.client_id)

    async def _wait_durable(self) -> bool:
        """Wait for the group commit; ``False`` if the data could not be made durable."""
        if self._committer is None:
            return True
        try:
            await self._committer.wait_durable()
        except Exception as exc:
            logger.critical(
                "client #%d: writes could not be made durable (%s); "
                "closing the connection without acknowledging them",
                self.client_id,
                exc,
            )
            return False
        return True

    async def close(self) -> None:
        """Close the underlying socket (idempotent)."""
        if not self._writer.is_closing():
            self._writer.close()
        with contextlib.suppress(ConnectionError, OSError):
            await self._writer.wait_closed()

    def _process(self, data: bytes) -> tuple[bytes, bool]:
        """Execute every complete command in the buffer.

        Returns:
            The concatenated replies and whether the connection must close.
        """
        self._parser.feed(data)
        replies = bytearray()
        try:
            while (command := self._parser.next_command()) is not None:
                reply, close_after = self._dispatch(command)
                replies += reply
                if close_after:
                    return bytes(replies), True
        except ProtocolError as exc:
            logger.info("client #%d protocol error: %s", self.client_id, exc.detail)
            replies += encode_error(exc)
            return bytes(replies), True
        return bytes(replies), False

    def _dispatch(self, command: list[str]) -> tuple[bytes, bool]:
        """Execute one command and encode its reply."""
        if command[0].upper() == "QUIT":
            return encode_response(OK), True
        try:
            return encode_response(self._db.execute(*command)), False
        except MiniKVError as exc:
            return encode_error(exc), False
        except Exception:
            logger.exception("client #%d: unexpected error running %s", self.client_id, command[0])
            return encode_error("ERR internal server error"), False


def _format_peer(peer: object) -> str:
    """Render a socket peer address for logs."""
    if isinstance(peer, tuple) and len(peer) >= 2:
        return f"{peer[0]}:{peer[1]}"
    return str(peer)
