"""RESP2 (REdis Serialization Protocol) encoding and decoding.

This module is pure: it converts between bytes and Python values and never
touches sockets. That keeps the protocol logic easy to test exhaustively and
lets the asyncio server and the blocking client share the same code.

Binary safety
-------------
MiniKV stores ``str`` values. To stay binary-safe over the network, bulk
strings are decoded as UTF-8 with the ``surrogateescape`` error handler and
encoded back the same way. Arbitrary bytes therefore round-trip unchanged,
while ordinary text stays ordinary ``str``.

References: https://redis.io/docs/latest/develop/reference/protocol-spec/
"""

from __future__ import annotations

from collections.abc import Iterable

from minikv.errors import ParseError, ProtocolError, ReplyError
from minikv.protocol.parser import parse_command
from minikv.responses import Response, SimpleString
from minikv.utils import parse_int64

CRLF = b"\r\n"
ENCODING = "utf-8"
ERRORS = "surrogateescape"

MAX_INLINE_SIZE = 64 * 1024
"""Maximum length of an inline request or a protocol header line."""

MAX_BULK_LENGTH = 512 * 1024 * 1024
"""Maximum size of a single bulk string (same as Redis' default)."""

MAX_MULTIBULK_LENGTH = 1024 * 1024
"""Maximum number of arguments in one request."""


def to_bytes(text: str) -> bytes:
    """Encode a ``str`` for the wire, restoring any escaped raw bytes."""
    return text.encode(ENCODING, ERRORS)


def from_bytes(data: bytes | bytearray) -> str:
    """Decode wire bytes into ``str`` without ever failing."""
    return bytes(data).decode(ENCODING, ERRORS)


# --------------------------------------------------------------------------- #
# Encoding
# --------------------------------------------------------------------------- #


def _single_line(text: str) -> str:
    """Make ``text`` safe for single-line RESP types (simple strings, errors)."""
    return text.replace("\r", " ").replace("\n", " ")


def encode_response(response: Response) -> bytes:
    """Serialise a command response to RESP2.

    Args:
        response: A value returned by :meth:`minikv.Database.execute`.

    Returns:
        The RESP2 bytes.

    Raises:
        TypeError: If ``response`` contains an unsupported type.
    """
    parts: list[bytes] = []
    _encode_into(response, parts)
    return b"".join(parts)


def _encode_into(response: Response, parts: list[bytes]) -> None:
    match response:
        case None:
            parts.append(b"$-1\r\n")
        case SimpleString(value=value):
            parts.append(b"+" + to_bytes(_single_line(value)) + CRLF)
        case bool():
            raise TypeError("bool is not a valid response type")
        case int():
            parts.append(b":%d\r\n" % response)
        case str():
            data = to_bytes(response)
            parts.append(b"$%d\r\n" % len(data))
            parts.append(data + CRLF)
        case list():
            parts.append(b"*%d\r\n" % len(response))
            for item in response:
                _encode_into(item, parts)
        case _:
            raise TypeError(f"unsupported response type: {type(response).__name__}")


def encode_error(error: BaseException | str) -> bytes:
    """Serialise an error reply, e.g. ``-ERR unknown command 'FOO'\\r\\n``.

    Args:
        error: A :class:`~minikv.errors.MiniKVError` (whose ``str`` already
            carries the ``ERR`` prefix) or a pre-formatted message.
    """
    return b"-" + to_bytes(_single_line(str(error))) + CRLF


def encode_command(args: Iterable[str]) -> bytes:
    """Serialise a client request as a RESP array of bulk strings."""
    items = [to_bytes(arg) for arg in args]
    parts = [b"*%d\r\n" % len(items)]
    for item in items:
        parts.append(b"$%d\r\n" % len(item))
        parts.append(item + CRLF)
    return b"".join(parts)


# --------------------------------------------------------------------------- #
# Request parsing (server side)
# --------------------------------------------------------------------------- #


class RequestParser:
    """Incrementally parse client requests from a byte stream.

    Accepts both request formats Redis supports:

    * **Multibulk** - ``*<n>\\r\\n`` followed by ``n`` bulk strings. This is
      what client libraries and ``redis-cli`` send.
    * **Inline** - a plain text line such as ``SET key "a value"``, as typed
      in ``telnet`` or ``nc``. Lines may end in ``\\r\\n`` or ``\\n``.

    Feed bytes as they arrive with :meth:`feed`, then call
    :meth:`next_command` until it returns ``None``. A bulk string's bytes
    are only copied once it is complete, so large values arriving in many
    small chunks are handled in linear time.

    Example:
        >>> parser = RequestParser()
        >>> parser.feed(b"*1\\r\\n$4\\r\\nPI")
        >>> parser.next_command() is None
        True
        >>> parser.feed(b"NG\\r\\n")
        >>> parser.next_command()
        ['PING']
    """

    def __init__(
        self,
        *,
        max_inline_size: int = MAX_INLINE_SIZE,
        max_bulk_length: int = MAX_BULK_LENGTH,
        max_multibulk_length: int = MAX_MULTIBULK_LENGTH,
    ) -> None:
        self._buffer = bytearray()
        self._pos = 0
        self._max_inline = max_inline_size
        self._max_bulk = max_bulk_length
        self._max_multibulk = max_multibulk_length
        # State of a partially received multibulk request.
        self._args: list[str] | None = None
        self._remaining = 0
        self._bulk_length = -1

    @property
    def buffered(self) -> int:
        """Number of received bytes not yet consumed."""
        return len(self._buffer) - self._pos

    @property
    def has_partial(self) -> bool:
        """Whether an incomplete request is pending (e.g. a truncated stream)."""
        return self._args is not None or self.buffered > 0

    def feed(self, data: bytes) -> None:
        """Append newly received bytes to the internal buffer."""
        if self._pos:
            del self._buffer[: self._pos]
            self._pos = 0
        self._buffer += data

    def next_command(self) -> list[str] | None:
        """Return the next complete request, or ``None`` if more bytes are needed.

        Empty requests (blank inline lines, ``*0``) are skipped silently.

        Raises:
            ProtocolError: If the stream is malformed. The parser must not be
                used afterwards.
        """
        while True:
            if self._args is None:
                if self.buffered == 0:
                    return None
                if self._buffer[self._pos] == ord("*"):
                    if not self._start_multibulk():
                        return None
                    if self._args is None:  # empty multibulk, skip it
                        continue
                else:
                    tokens = self._read_inline()
                    if tokens is None:
                        return None
                    if tokens:
                        return tokens
                    continue

            if not self._read_bulk_strings():
                return None
            command, self._args = self._args, None
            return command

    # -- helpers ---------------------------------------------------------------

    def _read_line(self, what: str) -> bytes | None:
        """Read a ``\\r\\n``-terminated header line, enforcing the size limit."""
        end = self._buffer.find(CRLF, self._pos)
        if end == -1:
            if self.buffered > self._max_inline:
                raise ProtocolError(f"too big {what}")
            return None
        line = bytes(self._buffer[self._pos : end])
        self._pos = end + 2
        return line

    def _start_multibulk(self) -> bool:
        line = self._read_line("mbulk count string")
        if line is None:
            return False
        count = parse_int64(from_bytes(line[1:]))
        if count is None or count > self._max_multibulk:
            raise ProtocolError("invalid multibulk length")
        if count > 0:
            self._args = []
            self._remaining = count
            self._bulk_length = -1
        return True

    def _read_bulk_strings(self) -> bool:
        assert self._args is not None
        while self._remaining > 0:
            if self._bulk_length < 0:
                line = self._read_line("bulk count string")
                if line is None:
                    return False
                if not line.startswith(b"$"):
                    found = chr(line[0]) if line else "\\r"
                    raise ProtocolError(f"expected '$', got '{found}'")
                length = parse_int64(from_bytes(line[1:]))
                if length is None or length < 0 or length > self._max_bulk:
                    raise ProtocolError("invalid bulk length")
                self._bulk_length = length

            end = self._pos + self._bulk_length
            if len(self._buffer) < end + 2:
                return False
            if self._buffer[end : end + 2] != CRLF:
                raise ProtocolError("bulk string is not terminated by CRLF")
            self._args.append(from_bytes(self._buffer[self._pos : end]))
            self._pos = end + 2
            self._bulk_length = -1
            self._remaining -= 1
        return True

    def _read_inline(self) -> list[str] | None:
        end = self._buffer.find(b"\n", self._pos)
        if end == -1:
            if self.buffered > self._max_inline:
                raise ProtocolError("too big inline request")
            return None
        line = self._buffer[self._pos : end]
        self._pos = end + 1
        if line.endswith(b"\r"):
            line = line[:-1]
        try:
            return parse_command(from_bytes(line))
        except ParseError as exc:
            raise ProtocolError("unbalanced quotes in request") from exc


# --------------------------------------------------------------------------- #
# Reply parsing (client side)
# --------------------------------------------------------------------------- #

type Reply = Response | ReplyError
"""A decoded server reply; error replies are returned as values, not raised."""


class _Incomplete(Exception):
    """Internal signal: the buffer does not yet hold a complete reply."""


class ReplyParser:
    """Incrementally parse RESP2 server replies.

    Error replies are returned as :class:`~minikv.errors.ReplyError`
    instances so that an error nested inside an array does not abort
    parsing; callers decide whether to raise them.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> None:
        """Append newly received bytes."""
        self._buffer += data

    def next_reply(self) -> tuple[bool, Reply]:
        """Try to decode one complete reply.

        Returns:
            ``(True, reply)`` if a reply was decoded, otherwise ``(False, None)``.

        Raises:
            ProtocolError: If the server sent malformed data.
        """
        try:
            reply, end = self._parse(0)
        except _Incomplete:
            return False, None
        del self._buffer[:end]
        return True, reply

    def _line(self, start: int) -> tuple[bytes, int]:
        end = self._buffer.find(CRLF, start)
        if end == -1:
            raise _Incomplete
        return bytes(self._buffer[start:end]), end + 2

    def _integer(self, text: bytes) -> int:
        value = parse_int64(from_bytes(text))
        if value is None:
            raise ProtocolError(f"invalid integer in reply: {text!r}")
        return value

    def _parse(self, start: int) -> tuple[Reply, int]:
        if start >= len(self._buffer):
            raise _Incomplete
        kind = self._buffer[start : start + 1]
        line, pos = self._line(start + 1)
        if kind == b"+":
            return SimpleString(from_bytes(line)), pos
        if kind == b"-":
            return ReplyError(from_bytes(line)), pos
        if kind == b":":
            return self._integer(line), pos
        if kind == b"$":
            length = self._integer(line)
            if length < 0:
                return None, pos
            end = pos + length
            if len(self._buffer) < end + 2:
                raise _Incomplete
            return from_bytes(self._buffer[pos:end]), end + 2
        if kind == b"*":
            count = self._integer(line)
            if count < 0:
                return None, pos
            items: list[Response] = []
            for _ in range(count):
                item, pos = self._parse(pos)
                items.append(item)  # type: ignore[arg-type]
            return items, pos
        raise ProtocolError(f"unknown reply type byte {bytes(kind)!r}")
